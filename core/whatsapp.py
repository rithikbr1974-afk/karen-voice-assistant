"""WhatsApp channel for Karen Remote: Meta's official WhatsApp Cloud API (Karen has her own number; free test number).

Incoming: Meta calls a webhook. A small local HTTP server (127.0.0.1:port) receives it, reached from the internet through
ngrok's free fixed domain (started here). Every POST must carry Meta's X-Hub-Signature-256 (HMAC with the app secret),
so nobody else can inject messages. Replies within 24 h of his last message are free ("service" messages).
Same interface as core/telegram.py: poll / send / send_file / download / typing.
"""
import hashlib
import hmac
import json
import mimetypes
import os
import queue
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

GRAPH = "https://graph.facebook.com/v26.0"


class WhatsApp:
    name = "whatsapp"

    def __init__(self, token, phone_id, app_secret, verify_token, domain="", port=8787, download_dir="data/uploads",
                 log=print):
        self.token, self.phone_id, self.secret, self.verify = token, phone_id, app_secret.encode(), verify_token
        self.domain, self.port, self.download_dir, self.log = domain, port, download_dir, log
        self.http = httpx.Client(timeout=60, headers={"Authorization": f"Bearer {token}"})
        self.inbox, self.last_id, self.ngrok = queue.Queue(), {}, None

    # ------------------------------------------------------------ incoming
    def start(self):
        channel = self

        class Hook(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):  # Meta's one-time webhook verification
                from urllib.parse import parse_qs, urlparse
                q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                ok = q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == channel.verify
                self.send_response(200 if ok else 403)
                self.end_headers()
                self.wfile.write(q.get("hub.challenge", "").encode() if ok else b"no")

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                sig = "sha256=" + hmac.new(channel.secret, body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(sig, self.headers.get("X-Hub-Signature-256", "")):
                    channel.log("block", "whatsapp webhook with a bad signature ignored")
                    self.send_response(403)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                try:
                    channel._receive(json.loads(body))
                except Exception as e:
                    channel.log("error", f"whatsapp webhook: {e!r}")

        server = ThreadingHTTPServer(("127.0.0.1", self.port), Hook)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        exe = shutil.which("ngrok")
        if self.domain and exe:
            self.ngrok = subprocess.Popen([exe, "http", f"--domain={self.domain}", str(self.port), "--log=stdout"],
                                          stdout=open("logs/ngrok.log", "a"), stderr=subprocess.STDOUT,
                                          creationflags=0x08000000)
        elif self.domain:
            self.log("error", "ngrok isn't installed (winget install ngrok.ngrok), so WhatsApp can't reach Karen")

    def _receive(self, data):
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                names = {c.get("wa_id"): c.get("profile", {}).get("name", "") for c in value.get("contacts", [])}
                for m in value.get("messages", []):
                    kind = m.get("type")
                    item = m.get(kind, {}) if isinstance(m.get(kind), dict) else {}
                    label = "voice" if item.get("voice") else kind  # a recorded voice note is him talking to Karen
                    files = [(item["id"], item.get("filename") or f"{label}-{m['id'][-8:]}")] if "id" in item else []
                    text = (item.get("body") if kind == "text" else item.get("caption")) or ""
                    if kind == "button":
                        text = m.get("button", {}).get("text", "")
                    self.last_id[m["from"]] = m["id"]
                    self.inbox.put({"channel": self.name, "chat": m["from"], "user": m["from"],
                                    "name": names.get(m["from"], ""), "text": text, "files": files})

    def poll(self):
        self.start()
        while True:
            yield self.inbox.get()

    # ------------------------------------------------------------ outgoing
    def _post(self, payload):
        r = self.http.post(f"{GRAPH}/{self.phone_id}/messages", json={"messaging_product": "whatsapp", **payload})
        if r.status_code >= 400:
            raise RuntimeError(f"WhatsApp send failed: {r.text[:300]}")
        return r.json()

    def send(self, chat, text):
        for i in range(0, len(text) or 1, 4000):  # WhatsApp's text limit is 4096 characters
            self._post({"to": chat, "type": "text", "text": {"body": text[i:i + 4000] or "(empty)"}})

    def typing(self, chat):
        if chat in self.last_id:  # marks his message read and shows "typing…" (until the reply, max 25 s)
            try:
                self._post({"status": "read", "message_id": self.last_id[chat], "typing_indicator": {"type": "text"}})
            except Exception:
                pass

    def send_file(self, chat, path, caption=""):
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            r = self.http.post(f"{GRAPH}/{self.phone_id}/media", data={"messaging_product": "whatsapp", "type": mime},
                               files={"file": (os.path.basename(path), f, mime)})
        media = r.json().get("id")
        if not media:
            raise RuntimeError(f"WhatsApp upload failed: {r.text[:300]}")
        kind = "image" if mime in ("image/jpeg", "image/png") else "document"
        body = {"id": media, "caption": caption[:1000]} if caption else {"id": media}
        if kind == "document":
            body["filename"] = os.path.basename(path)
        self._post({"to": chat, "type": kind, kind: body})

    def download(self, media_id, name):
        info = self.http.get(f"{GRAPH}/{media_id}").json()
        os.makedirs(self.download_dir, exist_ok=True)
        ext = mimetypes.guess_extension((info.get("mime_type") or "").split(";")[0]) or ""
        path = os.path.join(self.download_dir, name if os.path.splitext(name)[1] else name + ext)
        with open(path, "wb") as f:
            f.write(self.http.get(info["url"]).content)
        return path
