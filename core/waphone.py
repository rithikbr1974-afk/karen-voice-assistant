"""Karen Remote over WhatsApp on her own phone: the spare-SIM phone (config phone.second, adb over Wi-Fi) runs
WhatsApp with Karen's number, and the user chats with it like with the Telegram bot. Free: no Meta API, no webhook.

Incoming: WhatsApp's chat notifications (dumpsys notification), checked every ~3 s. Only a 1:1 chat whose contact
has his number (OWNER_PHONE) counts; groups and everyone else are ignored. last_row_id (WhatsApp's message number)
tells new messages apart. Only the newest message of a burst is in the notification, so a burst reads as its last
message (a note says so). Unread counts are tracked per chat so already-handled messages aren't counted again.
Outgoing: the phone server's whatsapp_me (wa.me link / share to his chat, tap send), which also clears the
notification. Buttons become numbered replies ("1 = ✅ Yes"). Logs go to remote.log.
"""
import importlib.util
import os
import re
import threading
import time

from core import adbguard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = {"📷": "image", "🎥": "video", "📹": "video", "📄": "document", "🎤": "voice", "🎵": "audio", "👾": "sticker"}
FOLDERS = {"image": "WhatsApp Images", "video": "WhatsApp Video", "document": "WhatsApp Documents",
           "voice": "WhatsApp Voice Notes", "audio": "WhatsApp Audio"}


class WaPhone:
    name, label = "waphone", "WhatsApp"
    buttons = True  # numbered replies stand in for buttons

    def __init__(self, owner, download_dir="data/uploads", log=print):
        self.owner = re.sub(r"\D", "", owner)[-10:]
        self.download_dir, self.log = download_dir, log
        spec = importlib.util.spec_from_file_location("karen_phone_ch", os.path.join(ROOT, "servers", "phone", "server.py"))
        self.phone = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.phone)
        self.lock = threading.Lock()  # one adb/UI action on the phone at a time
        self.verified = {}  # notification tag -> True if that chat is his number
        self.last_row = None  # None until the first look: old notifications aren't replayed at start
        self.counts = {}  # notification tag -> unread count at the last look
        self.pending = {}  # "1" -> button data, from the last message with buttons
        self.chat = self.owner  # one chat only: his
        self.down = False  # the second phone is unreachable (set by poll)

    # ------------------------------------------------------------ phone access
    def _adb(self, *args, **kw):
        with self.lock:
            if self.phone._pick("second"):
                raise ConnectionError("second phone not connected")
            return self.phone.adb(*args, **kw)

    def _is_owner(self, contact_id):
        rows = self._adb("shell", "content", "query", "--uri", "content://com.android.contacts/data/phones",
                         "--projection", "data1", "--where", self.phone._q(f"contact_id={contact_id}"))
        return any(re.sub(r"\D", "", n)[-10:] == self.owner for n in re.findall(r"data1=([^\n,]+)", rows))

    def _notes(self):
        """[(tag, row_id, count, text)] for WhatsApp 1:1 chat notifications from his number."""
        dump = self._adb("shell", "dumpsys", "notification", "--noredact", timeout=25)
        out = []
        for block in dump.split("NotificationRecord(")[1:]:
            if "pkg=com.whatsapp" not in block[:80] or "android.messages" not in block:
                continue
            tag = re.search(r"\btag=(\S+)", block)
            row = re.search(r"last_row_id=Long \((\d+)\)", block)
            if not (tag and row) or "isGroupConversation=Boolean (true)" in block:
                continue
            tag = tag.group(1)
            if tag not in self.verified:
                person = re.search(r"contacts/lookup/\S+/(\d+)", block)
                self.verified[tag] = bool(person) and self._is_owner(person.group(1))
                if not self.verified[tag]:
                    title = re.search(r"android\.title=String \((.*)\)", block)
                    self.log("block", f"waphone: chat '{title.group(1) if title else '?'}' isn't his number, ignored")
            if self.verified[tag]:
                text = re.search(r"android\.text=String \((.*)\)\r?$", block, re.M)
                count = re.search(r"android\.messages=Parcelable\[\] \((\d+)\)", block)
                out.append((tag, int(row.group(1)), int(count.group(1)) if count else 1, text.group(1) if text else ""))
        return out

    # ------------------------------------------------------------ incoming
    def poll(self):
        """Yield his messages forever, like Telegram.poll."""
        down = False
        while True:
            if not adbguard.background_ok():  # Android Studio is open, or someone stopped adb: stay off it
                time.sleep(10)
                continue
            try:
                notes = self._notes()
                if down:
                    self.log("ready", "waphone: second phone reachable again")
                    down = self.down = False
            except Exception as e:
                if not down:
                    self.log("error", f"waphone: {e} (plug it in by USB once if it restarted: adb tcpip 5555)")
                    down = self.down = True  # notify() skips it meanwhile (each try waited ~10 s for adb)
                time.sleep(30)
                continue
            newest = max((n[1] for n in notes), default=self.last_row or 0)
            seen = dict(self.counts)
            self.counts = {n[0]: n[2] for n in notes}  # unread count per chat; gone = read (Karen replied)
            if self.last_row is None:
                self.last_row = newest
            for tag, row, count, text in sorted(notes, key=lambda n: n[1]):
                if row <= self.last_row:
                    continue
                self.last_row = row
                yield self._message(text, count - seen.get(tag, 0) if count > seen.get(tag, 0) else 1)
            time.sleep(3)

    def _message(self, text, count):
        files, kind = [], MEDIA.get(text[:1]) or MEDIA.get(text[:2])
        if kind:
            caption = text.split(" ", 1)[1].strip() if " " in text else ""
            name = caption if kind == "document" else ""
            if kind != "document" and caption.lower() in ("photo", "video", "audio", "sticker", "gif") or \
                    caption.lower().startswith("voice message"):
                caption = ""
            files = [(kind, name or f"{kind}-{int(time.time())}")]
            text = "" if kind == "document" else caption
        m = {"channel": self.name, "chat": self.chat, "user": self.owner, "name": "The user", "text": text,
             "files": files}
        if text.strip() in self.pending:  # "1" / "2" answers the last numbered question
            m["button"] = self.pending.pop(text.strip())
            self.pending.clear()
        elif count > 1 and not files:
            # worded so it matches no tool triggers (a "messages" here once pulled in WhatsApp reading)
            m["text"] = text + f"\n(heads-up: he typed {count} things in a row; only this last one came through)"
        return m

    def download(self, kind, name):
        """The newest file WhatsApp saved for this kind of media (auto-download on Wi-Fi), pulled to the PC."""
        time.sleep(4)  # let WhatsApp finish downloading it
        for base in ("/sdcard/WhatsApp/Media", "/sdcard/Android/media/com.whatsapp/WhatsApp/Media"):
            folder = f"{base}/{FOLDERS.get(kind, 'WhatsApp Documents')}"
            listing = self._adb("shell", "ls", "-t", self.phone._q(folder))
            first = next((f for f in listing.splitlines() if f and "." in f and "No such" not in f), "")
            if first:
                os.makedirs(self.download_dir, exist_ok=True)
                local = os.path.join(self.download_dir, re.sub(r"[^\w. -]", "_", first))
                self._adb("pull", f"{folder}/{first}", local, timeout=120)
                return local
        raise FileNotFoundError("WhatsApp hasn't saved that file on Karen's phone (is auto-download on?)")

    # ------------------------------------------------------------ outgoing
    def send(self, chat, text, buttons=None):
        if buttons:
            self.pending = {str(i + 1): data for i, (_label, data) in enumerate(buttons)}
            text += "\n\n" + "\n".join(f"Reply {i + 1} = {label}" for i, (label, _data) in enumerate(buttons))
        for i in range(0, len(text) or 1, 3500):  # one wa.me link per part
            with self.lock:
                result = self.phone.whatsapp_me(message=text[i:i + 3500] or "(empty)")
            if not result.startswith("Sent"):
                raise RuntimeError(result)

    def typing(self, chat):
        pass  # no typing indicator from an ordinary WhatsApp account

    def send_file(self, chat, path, caption="", buttons=None):
        if buttons:
            self.pending = {str(i + 1): data for i, (_label, data) in enumerate(buttons)}
            caption += "\n\n" + "\n".join(f"Reply {i + 1} = {label}" for i, (label, _data) in enumerate(buttons))
        with self.lock:
            result = self.phone.whatsapp_me(message=caption, file=path)
        if not result.startswith("Sent"):
            raise RuntimeError(result)
