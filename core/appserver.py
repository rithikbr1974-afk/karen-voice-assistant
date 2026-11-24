"""Karen's app link: the Android app (android/) talks to the PC through Karen Remote.

The app reaches this small HTTP server on the home Wi-Fi (0.0.0.0:8788) or from anywhere through ngrok's fixed domain.
Every request and reply is sealed with AES-GCM under that device's own sync key (data/app_devices.json, made by
scripts/pair_app.py): nobody without the key can read or forge one, even on the plain-HTTP home network, and a request
older than 5 minutes or seen before is refused. Revoking a device (pair_app.py --revoke tablet) stops its key at once.

Ops (POST /app, header X-Karen-Device: <device>, body = base64(nonce + AES-GCM(json))):
  hello / status     is the PC on: battery, storage, voice Karen open, uptime
  routes             the PC's tool words, so the app knows which requests need the PC
  ask / wait / answer   run a request with the PC's brain and tools (Gmail, PC control, Alexa, files, study...);
                     events stream back by long polling: say, confirm (his yes/no on the phone), file, link, reply
  file               a file a job produced (only ids that job issued)
  upload             a file from the phone -> Downloads/From phone
  sync               notes, to-dos, memory, expenses, reminders and his personality mode, both ways (core/appsync.py)
"""
import base64
import datetime
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DEVICES = "data/app_devices.json"
SKIP = {"phone", "hands", "artemis", "daily"}  # tools that drive the phone itself, or that the app has natively
MAX_BODY = 40 << 20
APP_NOTE = (" This request comes from Karen's app on his {dev}; you're running on his PC, with the PC's tools. Her "
            "answer is spoken on the {dev}: short, plain, no markdown. Files you produce are sent to the {dev}.")
MARKER = re.compile(r"\[\[\w+:.*?\]\]", re.S)


def devices():
    try:
        return json.load(open(DEVICES, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _key(token):
    return AESGCM(hashlib.sha256(token.encode()).digest())


def seal(token, obj, aad):
    nonce = os.urandom(12)
    return base64.b64encode(nonce + _key(token).encrypt(nonce, json.dumps(obj, ensure_ascii=False).encode(), aad.encode()))


def unseal(token, body, aad):
    raw = base64.b64decode(body)
    return json.loads(_key(token).decrypt(raw[:12], raw[12:], aad.encode()))


class Job:
    """One request from the app, running in its own thread; the app long-polls its events."""

    def __init__(self, dev):
        self.id, self.dev = secrets.token_hex(6), dev
        self.events, self.done, self.files, self.started = [], False, {}, time.time()
        self.cond, self.answers = threading.Condition(), queue.Queue()

    def emit(self, **event):
        with self.cond:
            self.events.append(event)
            if event["type"] in ("reply", "error"):
                self.done = True
            self.cond.notify_all()

    def wait(self, after, timeout=20):
        with self.cond:
            self.cond.wait_for(lambda: len(self.events) > after or self.done, timeout)
            return self.events[after:], self.done

    def confirm(self, question):
        """His yes/no on the phone (the app's YES/NO card, spoken); no answer in 3 minutes = no."""
        while not self.answers.empty():
            self.answers.get_nowait()
        self.emit(type="confirm", text=question)
        try:
            return bool(self.answers.get(timeout=180))
        except queue.Empty:
            return False


class AppServer:
    def __init__(self, remote, cfg, log):
        self.remote, self.cfg, self.log = remote, cfg, log
        a = cfg.get("app") or {}
        self.port = a.get("port", 8788)
        self.jobs, self.brains, self.seen, self.lock = {}, {}, {}, threading.Lock()
        self.ngrok, self.public_url = None, ""
        from core.appsync import Sync
        self.sync = Sync(log)

    # ------------------------------------------------------------ server
    def start(self):
        app = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # a quick "is it up" for a browser; tells nothing
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"Karen")

            def do_POST(self):
                dev = self.headers.get("X-Karen-Device", "")
                size = int(self.headers.get("Content-Length", 0))
                token = devices().get(dev, {}).get("token")
                if self.path != "/app" or not token or size > MAX_BODY:
                    return self._send(403, b"no")
                try:
                    req = unseal(token, self.rfile.read(size), dev)
                except Exception:
                    app.log("block", f"app request for '{dev}' that doesn't open with its key")
                    return self._send(403, b"no")
                if not app.fresh(req):
                    return self._send(403, b"old")
                try:
                    out = app.handle(dev, req)
                except Exception as e:
                    app.log("error", f"app {req.get('op')}: {e!r}")
                    out = {"error": f"{type(e).__name__}: {e}"[:300]}
                self._send(200, seal(token, out, dev + "<"))

            def _send(self, code, body):
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        try:
            server = ThreadingHTTPServer(((self.cfg.get("app") or {}).get("host", "0.0.0.0"), self.port), Handler)
        except OSError as e:
            self.log("error", f"app link can't listen on port {self.port}: {e}")
            return
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.log("ready", f"app link on {lan_ip()}:{self.port} for {', '.join(devices()) or 'no paired devices yet'}")
        self.start_ngrok()

    def start_ngrok(self):
        """The app's way in from anywhere. One ngrok domain = one port: WhatsApp's webhook, if it's set up, keeps it."""
        domain = os.environ.get("NGROK_DOMAIN", "").removeprefix("https://").strip("/")
        if not domain or not (self.cfg.get("app") or {}).get("ngrok", True):
            return
        if "whatsapp" in self.remote.channels:
            self.log("error", "ngrok is in use by WhatsApp, so the app works on the home Wi-Fi only")
            return
        exe = shutil.which("ngrok") or next(iter(_glob(os.path.expandvars(
            r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Ngrok.Ngrok*\ngrok.exe"))), None)
        if not exe:
            self.log("error", "ngrok isn't installed (winget install ngrok.ngrok): the app works on the home Wi-Fi only")
            return
        # a hostname = the fixed free domain (--domain works on old and new ngrok 3); otherwise ngrok picks an address
        # and the app learns it at every sync (the "url" in the sync reply)
        fixed = "." in domain
        args = [exe, "http", str(self.port), "--log=stdout"] + ([f"--domain={domain}"] if fixed else [])
        self.ngrok = subprocess.Popen(args, stdout=open("logs/ngrok.log", "a"), stderr=subprocess.STDOUT,
                                      creationflags=0x08000000)
        if fixed:
            self.public_url = f"https://{domain}"
            self.log("ready", f"app link also at {self.public_url}")
        else:
            threading.Thread(target=self.find_url, daemon=True).start()

    def find_url(self):
        """The address ngrok picked, from its local agent API."""
        import urllib.request
        for _ in range(30):
            time.sleep(2)
            if self.ngrok.poll() is not None:
                self.log("error", "ngrok stopped (see logs/ngrok.log): the app works on the home Wi-Fi only")
                return
            try:
                tunnels = json.load(urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=3))["tunnels"]
                url = next(t["public_url"] for t in tunnels if t["public_url"].startswith("https://"))
            except Exception:
                continue
            self.public_url = url
            self.log("ready", f"app link also through ngrok (the app learns the address at its next sync)")
            return

    def fresh(self, req):
        """Not older than 5 minutes (either way, for clock drift) and not seen before: no replays."""
        now, rid = time.time(), str(req.get("id", ""))
        with self.lock:
            self.seen = {k: v for k, v in self.seen.items() if now - v < 600}
            if not rid or rid in self.seen or abs(now - req.get("t", 0) / 1000) > 300:
                return False
            self.seen[rid] = now
        return True

    # ------------------------------------------------------------ ops
    def handle(self, dev, req):
        op = req.get("op")
        if op in ("hello", "status"):
            return status()
        if op == "routes":
            return {"routes": self.routes()}
        if op == "ask":
            return self.ask(dev, req)
        if op == "wait":
            job = self.jobs.get(req.get("job"))
            if not job or job.dev != dev:
                return {"error": "That request has expired."}
            events, done = job.wait(int(req.get("after", 0)))
            return {"events": events, "done": done}
        if op == "answer":
            job = self.jobs.get(req.get("job"))
            if job and job.dev == dev:
                job.answers.put(bool(req.get("yes")))
            return {"ok": bool(job)}
        if op == "file":
            job = self.jobs.get(req.get("job"))
            path = job.files.get(req.get("file")) if job and job.dev == dev else None
            if not path or not os.path.isfile(path):
                return {"error": "That file isn't available any more."}
            return {"name": os.path.basename(path), "data": base64.b64encode(open(path, "rb").read()).decode()}
        if op == "upload":
            return self.upload(dev, req)
        if op == "sync":
            out = self.sync.run(dev, req)
            self.save_log(dev, req.get("log") or [])
            # the PC's addresses may have changed since pairing (new Wi-Fi address, a new ngrok address)
            return dict(out, lan=[f"{lan_ip()}:{self.port}"], **({"url": self.public_url} if self.public_url else {}))
        return {"error": f"unknown op {op}"}

    def routes(self):
        """The words that send a request to each PC tool server (the app checks its own tools first)."""
        out = {}
        for name, spec in self.remote.tools.specs.items():
            if name in SKIP:
                continue
            trig = spec.get("triggers") or []
            words = [w for ws in (trig.values() if isinstance(trig, dict) else [trig]) for w in ws]
            if words and self.remote.tools.status.get(name) != "setup":  # "setup" = its keys aren't in .env
                out[name] = sorted(set(w.lower() for w in words))
        return out

    def upload(self, dev, req):
        name = re.sub(r'[\\/:*?"<>|]', "_", os.path.basename(req.get("name") or "file"))[:120] or "file"
        folder = os.path.join(os.path.expanduser("~"), "Downloads", "From phone")
        os.makedirs(folder, exist_ok=True)
        stem, ext = os.path.splitext(name)
        path, n = os.path.join(folder, name), 1
        while os.path.exists(path):
            n += 1
            path = os.path.join(folder, f"{stem} ({n}){ext}")
        with open(path, "wb") as f:
            f.write(base64.b64decode(req.get("data", "")))
        self.log("app", f"[{dev}] file saved: {path}")
        return {"path": path, "folder": folder}

    def save_log(self, dev, lines):
        """His conversation on the app, into logs/app.log (what activity_report reads as "app")."""
        if not lines:
            return
        with open("logs/app.log", "a", encoding="utf-8") as f:
            for x in lines[-200:]:
                when = datetime.datetime.fromtimestamp(x.get("t", 0) / 1000)
                tag = {"you": "you", "karen": "karen"}.get(x.get("role"), "")
                if tag and x.get("text"):
                    f.write(f"[{when:%Y-%m-%d %H:%M:%S}] {tag:<6} [{dev}] {' '.join(str(x['text']).split())[:500]}\n")

    # ------------------------------------------------------------ requests run on the PC
    def ask(self, dev, req):
        with self.lock:
            for jid in [j for j, job in self.jobs.items() if time.time() - job.started > 1800]:
                self.jobs.pop(jid)
        job = Job(dev)
        self.jobs[job.id] = job
        threading.Thread(target=self.run_job, args=(job, req), daemon=True).start()
        events, done = job.wait(0)
        return {"job": job.id, "events": events, "done": done}

    def brain(self, dev):
        if dev not in self.brains:
            from core.brain import Brain, private_backends
            c = self.cfg["llm"]
            self.brains[dev] = Brain(c["backends"], c["system_prompt"] + APP_NOTE.format(dev=dev), c["max_tokens"],
                                     c["history_turns"], private_backends(c), c.get("max_tool_rounds", 5))
        return self.brains[dev]

    def run_job(self, job, req):
        from core import persona
        from main import REFERS_BACK, NeedsSignIn
        dev, text = job.dev, " ".join(str(req.get("text", "")).split())
        self.log("you", f"[app:{dev}] {text}")
        t = time.time()
        try:
            switched = persona.command(text)
            if switched:
                self.log("karen", f"[app:{dev}] {switched}  (mode switch)")
                return job.emit(type="reply", text=switched, mode=persona_mode())
            brain = self.brain(dev)
            brain.turns = self.history(brain, req.get("history") or [])
            tools = self.remote.tools
            sticky = {g for g in getattr(brain, "used_groups", set())} if REFERS_BACK.search(text) else set()
            chosen = tools.select(text, sticky)
            job.used = set()
            try:
                reply = brain.dedupe("".join(brain.ask(text, chosen, lambda n, a: self.run_tool(job, n, a))).strip())
            except NeedsSignIn:
                reply = "Google sign-in opened in the browser on your PC. Sign in there, then ask me again."
            brain.used_groups = job.used
            b = brain.last
            self.log("karen", f"[app:{dev}] {reply}  ({b.get('failed', '')}{b.get('backend')} {time.time() - t:.1f}s)")
            job.emit(type="reply", text=reply or "Done.", tools=bool(chosen), mode=persona_mode())
        except Exception as e:
            self.log("error", f"app job: {e!r}")
            job.emit(type="error", text=f"Something went wrong on the PC: {type(e).__name__}.")

    @staticmethod
    def history(brain, phone):
        """The conversation as the phone saw it (its own answers too), keeping the PC's tool details for the last
        turn the PC itself answered."""
        turns = [[{"role": "user", "content": str(u)}, {"role": "assistant", "content": str(a)}]
                 for u, a in phone[-brain.history_turns:] if u and a]
        if turns and brain.turns and brain.turns[-1][0].get("content") == turns[-1][0]["content"]:
            turns[-1] = brain.turns[-1]
        return turns

    def run_tool(self, job, name, args):
        from main import MAP, SAY, SHOW, SLOW_SAY, SLOW_TOOLS, describe
        tools, short = self.remote.tools, name.split("__", 1)[-1]
        if tools.needs_confirm(name) and not job.confirm(describe(short, args)):
            self.log("tool", f"[app:{job.dev}] {short} cancelled")
            return "The user said no, so nothing was done. Don't retry; just tell him it was cancelled."
        if short in SLOW_TOOLS:
            job.emit(type="say", text=SLOW_SAY.get(short, "On it."))
        self.remote.app_job = job  # a tool's own yes/no question (power mode, screen agent) goes to the phone
        t = time.time()
        try:
            result = tools.call(name, args)
        finally:
            self.remote.app_job = None
        self.log("tool", f"[app:{job.dev}] {short} {json.dumps(args, ensure_ascii=False)[:200]} -> {len(result)} chars, "
                         f"{time.time() - t:.1f}s")
        if "ACTION REQUIRED" in result and "Authorization URL" in result:
            from main import NeedsSignIn
            raise NeedsSignIn()
        for target in (s.strip() for s in SHOW.findall(result)):
            if os.path.isfile(target) and os.path.getsize(target) <= 25 << 20:
                fid = secrets.token_hex(4)
                job.files[fid] = target
                job.emit(type="file", file=fid, name=os.path.basename(target), size=os.path.getsize(target))
            elif target.startswith("http"):
                job.emit(type="link", url=target)
        result = SHOW.sub(f"(now sent to his {job.dev})", result)
        for _, _, link in MAP.findall(result):
            job.emit(type="link", url=link.strip())
        result = MAP.sub(f"(the Google Maps link is now sent to his {job.dev})", result)
        result = SAY.sub(lambda m: f'"{m.group(3)}" (write this translation in the reply)', result)
        result = MARKER.sub("", result)
        job.used.add(tools.group_of(name))
        return result


def persona_mode():
    try:
        return open("data/mode.txt", encoding="utf-8").read().strip()
    except OSError:
        return ""


def status():
    import psutil
    battery = psutil.sensors_battery()
    free = shutil.disk_usage("C:\\").free / 1e9
    import ctypes
    handle = ctypes.windll.kernel32.OpenMutexW(0x00100000, False, r"Local\KarenVoiceAssistant")  # voice Karen open?
    voice = bool(handle)
    if handle:
        ctypes.windll.kernel32.CloseHandle(handle)
    up = time.time() - psutil.boot_time()
    return {"pc": socket.gethostname(), "battery": round(battery.percent) if battery else None,
            "plugged": battery.power_plugged if battery else True, "c_free_gb": round(free, 1), "voice": voice,
            "up_h": round(up / 3600, 1), "time": datetime.datetime.now().isoformat(timespec="seconds")}


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _glob(pattern):
    import glob
    return glob.glob(pattern)
