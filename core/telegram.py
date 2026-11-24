"""Telegram channel for Karen Remote: Bot API over HTTPS long polling (no public URL needed, no library).

Messages come in as dicts {channel, chat, user, name, text, files}; Karen answers with send / send_file.
"""
import json
import os
import time

import httpx

API = "https://api.telegram.org/bot{token}/{method}"
PHOTO = {".jpg", ".jpeg", ".png", ".webp"}


class Telegram:
    name = "telegram"
    buttons = True  # inline ✅/❌ buttons under messages
    quiet = True    # send(..., silent=True) arrives without a sound; send_voice for voice notes

    def __init__(self, token, download_dir="data/uploads"):
        self.token, self.download_dir, self.offset = token, download_dir, None
        self.http = httpx.Client(timeout=70)

    def _call(self, method, **params):
        files = params.pop("files", None)
        r = self.http.post(API.format(token=self.token, method=method), data=params if files else None,
                           json=None if files else params, files=files)
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method}: {data.get('description')}")
        return data["result"]

    def me(self):
        return self._call("getMe")

    def set_commands(self, commands):
        """The menu Telegram shows when he types "/": [(command, description)]."""
        try:
            self._call("setMyCommands", commands=[{"command": c, "description": d} for c, d in commands])
        except Exception:
            pass

    def poll(self):
        """Yield incoming messages forever (reconnects on network errors)."""
        while True:
            try:
                updates = self._call("getUpdates", timeout=50, offset=self.offset,
                                     allowed_updates=["message", "callback_query"])
            except Exception:
                time.sleep(5)
                continue
            for u in updates:
                self.offset = u["update_id"] + 1
                m, tap = u.get("message"), u.get("callback_query")
                if m:
                    yield self._message(m)
                elif tap:  # he tapped a button under one of Karen's messages
                    try:
                        self._call("answerCallbackQuery", callback_query_id=tap["id"])
                    except Exception:
                        pass
                    user = tap.get("from", {})
                    yield {"channel": self.name, "chat": tap["message"]["chat"]["id"], "user": user.get("id"),
                           "name": user.get("first_name", ""), "text": "", "files": [], "button": tap.get("data", "")}

    def _message(self, m):
        user = m.get("from", {})
        files = []
        for kind in ("document", "photo", "audio", "video", "voice", "video_note"):
            item = m.get(kind)
            if item:
                item = item[-1] if kind == "photo" else item  # photos come in several sizes; the last is largest
                name = item.get("file_name") or f"{kind.replace('_', '')}-{m['message_id']}"
                if kind == "document" and os.path.exists(os.path.join(self.download_dir, name)):
                    name = f"{m['message_id']}-{name}"  # a second "report.pdf" doesn't overwrite the first
                files.append((item["file_id"], name))
        return {"channel": self.name, "chat": m["chat"]["id"], "user": user.get("id"),
                "username": user.get("username") or "",  # his @name: /callme gives it to CallMeBot (core/callme.py)
                "name": " ".join(filter(None, [user.get("first_name"), user.get("last_name")])),
                "text": m.get("text") or m.get("caption") or "", "files": files,
                "group": m.get("media_group_id")}  # an album: several messages, the caption on one of them

    def download(self, file_id, name):
        info = self._call("getFile", file_id=file_id)
        os.makedirs(self.download_dir, exist_ok=True)
        ext = os.path.splitext(info["file_path"])[1]
        path = os.path.join(self.download_dir, name if os.path.splitext(name)[1] else name + ext)
        r = self.http.get(f"https://api.telegram.org/file/bot{self.token}/{info['file_path']}")
        if r.status_code != 200:  # (not raise_for_status: its message would carry the URL with the bot token)
            raise RuntimeError(f"Telegram file download: HTTP {r.status_code}")
        with open(path, "wb") as f:
            f.write(r.content)
        return path

    def send(self, chat, text, buttons=None, silent=False):
        """buttons: [(label, data)] shown under the message; a tap comes back from poll() as {"button": data}.
        silent: no sound on his phone (Karen's updates in quiet hours)."""
        for i in range(0, len(text) or 1, 4000):  # Telegram's limit is 4096 characters per message
            last = i + 4000 >= len(text)
            extra = {"reply_markup": {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}} \
                if buttons and last else {}
            if silent:
                extra["disable_notification"] = True
            self._call("sendMessage", chat_id=chat, text=text[i:i + 4000] or "(empty)", **extra)

    def send_voice(self, chat, path, caption="", seconds=0, silent=False):
        """A voice message (OGG/Opus): plays in the chat like one he recorded himself."""
        extra = {"duration": int(round(seconds))} if seconds else {}
        if silent:
            extra["disable_notification"] = "true"
        with open(path, "rb") as f:
            self._call("sendVoice", chat_id=chat, caption=caption[:1000], files={"voice": (os.path.basename(path), f,
                                                                                            "audio/ogg")}, **extra)

    def typing(self, chat):
        try:
            self._call("sendChatAction", chat_id=chat, action="typing")
        except Exception:
            pass

    def send_file(self, chat, path, caption="", buttons=None):
        ext = os.path.splitext(path)[1].lower()
        photo = ext in PHOTO and os.path.getsize(path) < 10_000_000
        video = ext == ".mp4" and os.path.getsize(path) < 50_000_000  # plays inline in the chat
        kind = "photo" if photo else "video" if video else "document"
        extra = {"reply_markup": json.dumps({"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]})} \
            if buttons else {}
        with open(path, "rb") as f:
            self._call({"photo": "sendPhoto", "video": "sendVideo", "document": "sendDocument"}[kind], chat_id=chat,
                       caption=caption[:1000], files={kind: (os.path.basename(path), f)}, **extra)
