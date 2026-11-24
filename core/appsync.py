"""Two-way sync of his lists between Karen's app and the PC (called by core/appserver.py).

The PC's own files stay exactly as the tool servers write them (data/notes.json, memory.json, expenses.json,
reminders.json); they have no ids, so a ledger (data/app_sync.json) remembers which PC item is which app item: id ->
{key, item (app format), updated (PC ms), deleted, dev}. Each sync first notices what changed on the PC (a new note,
a ticked to-do, a forgotten fact), then applies the app's changes to the PC files, then sends back everything that
changed since the app's last sync. Last change wins.

Reminders made on the app ring on the app (its own alarm) and are spoken by voice Karen; Remote doesn't also send them
to Telegram ("app" field). Reminders made on the PC keep going to Telegram and are only listed on the app.
"""
import datetime
import json
import os
import secrets
import threading
import time

LEDGER = "data/app_sync.json"


def ms():
    return int(time.time() * 1000)


def _load(path, default):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(data, open(path + ".tmp", "w", encoding="utf-8"), indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def _ms_of(text, fmt):
    try:
        return int(datetime.datetime.strptime(text, fmt).timestamp() * 1000)
    except (TypeError, ValueError):
        return ms()


def _when(t, fmt):
    return datetime.datetime.fromtimestamp(t / 1000).strftime(fmt)


class List:
    """One synced list: where it lives on the PC and how a PC item maps to the app's item and back."""

    def __init__(self, path, sub=None):
        self.path, self.sub = path, sub

    def read(self):
        data = _load(self.path, {} if self.sub else [])
        return data.get(self.sub, []) if self.sub else data

    def write(self, items):
        if self.sub:
            data = _load(self.path, {})
            data[self.sub] = items
            _save(self.path, data)
        else:
            _save(self.path, items)

    def wanted(self, pc):  # PC items that sync (not timers)
        return True


class Notes(List):
    base = staticmethod(lambda pc: pc.get("text", ""))
    to_app = staticmethod(lambda pc: {"text": pc.get("text", ""), "t": _ms_of(pc.get("at"), "%Y-%m-%d %H:%M")})

    @staticmethod
    def to_pc(app, old=None, dev=""):
        return {"text": app.get("text", ""), "at": (old or {}).get("at") or _when(app.get("t") or ms(), "%Y-%m-%d %H:%M")}


class Todos(List):
    base = staticmethod(lambda pc: pc.get("item", ""))
    to_app = staticmethod(lambda pc: {"text": pc.get("item", ""), "done": bool(pc.get("done"))})

    @staticmethod
    def to_pc(app, old=None, dev=""):
        return {"item": app.get("text", ""), "done": bool(app.get("done")),
                "added": (old or {}).get("added") or _when(app.get("t") or ms(), "%Y-%m-%d")}


class Memory(List):
    base = staticmethod(lambda pc: pc.get("fact", ""))
    to_app = staticmethod(lambda pc: {"text": pc.get("fact", ""), "t": _ms_of(pc.get("saved"), "%Y-%m-%d")})

    @staticmethod
    def to_pc(app, old=None, dev=""):
        return {"fact": app.get("text", ""), "saved": (old or {}).get("saved") or _when(app.get("t") or ms(), "%Y-%m-%d")}


class Expenses(List):
    base = staticmethod(lambda pc: f"{pc.get('date')}|{float(pc.get('amount', 0)):g}|{pc.get('what', '')}")

    @staticmethod
    def to_app(pc):
        return {"amount": float(pc.get("amount", 0)), "text": pc.get("what", ""), "category": pc.get("category", "other"),
                "t": _ms_of(pc.get("date"), "%Y-%m-%d") + 12 * 3_600_000}

    @staticmethod
    def to_pc(app, old=None, dev=""):
        return {"date": _when(app.get("t") or ms(), "%Y-%m-%d"), "amount": round(float(app.get("amount", 0)), 2),
                "what": app.get("text", ""), "category": (app.get("category") or "other").lower()}


class Reminders(List):
    base = staticmethod(lambda pc: f"r{pc.get('id')}")

    def wanted(self, pc):
        return not pc.get("timer")

    @staticmethod
    def to_app(pc):
        return {"text": pc.get("text", ""), "due": _ms_of(pc.get("due", "")[:16], "%Y-%m-%dT%H:%M"),
                "repeat": pc.get("repeat") or "", "done": bool(pc.get("done")), "origin": pc.get("app") or "pc"}

    @staticmethod
    def to_pc(app, old=None, dev=""):
        pc = dict(old or {})
        pc.update({"text": app.get("text", ""), "due": _when(app.get("due") or ms(), "%Y-%m-%dT%H:%M"),
                   "repeat": app.get("repeat") or None, "done": bool(app.get("done"))})
        if not old:
            pc["app"] = app.get("origin") if app.get("origin") not in (None, "", "pc") else dev
        return pc


LISTS = {"notes": Notes("data/notes.json", "notes"), "todos": Todos("data/notes.json", "todos"),
         "memory": Memory("data/memory.json"), "expenses": Expenses("data/expenses.json"),
         "reminders": Reminders("data/reminders.json")}
META = {"id", "updated", "deleted", "dev", "dirty"}


def keyed(lst, items):
    """(key, item) pairs; repeated texts get #2, #3 so each stays its own item."""
    seen, out = {}, []
    for pc in items:
        if not lst.wanted(pc):
            continue
        base = lst.base(pc)
        seen[base] = seen.get(base, 0) + 1
        out.append((base if seen[base] == 1 else f"{base}#{seen[base]}", pc))
    return out


class Sync:
    def __init__(self, log=print):
        self.log, self.lock = log, threading.Lock()

    def run(self, dev, req):
        since, changes, now = int(req.get("since") or 0), req.get("changes") or {}, ms()
        with self.lock:
            ledger = _load(LEDGER, {})
            took = 0
            for name, lst in LISTS.items():
                book = ledger.setdefault(name, {})
                self.scan(book, lst, now)
                took += self.apply(book, lst, changes.get(name) or [], dev, now)
            _save(LEDGER, ledger)
            out = {name: [dict(e["item"], id=i, updated=e["updated"], deleted=e.get("deleted", False), dev=e.get("dev", "pc"))
                          for i, e in ledger[name].items() if e["updated"] > since] for name in LISTS}
        mode = self.mode(req.get("mode"))
        sent = sum(len(v) for v in out.values())
        if took or sent:
            self.log("sync", f"[{dev}] took {took} change(s), sent {sent}")
        return {"now": now, "changes": out, **mode}

    @staticmethod
    def scan(book, lst, now):
        """What changed on the PC since the last sync, into the ledger."""
        live = {e["key"]: i for i, e in book.items() if not e.get("deleted")}
        current = keyed(lst, lst.read())
        keys = {k for k, _ in current}
        for key, pc in current:
            item = lst.to_app(pc)
            if key not in live:
                book["pc-" + secrets.token_hex(4)] = {"key": key, "item": item, "updated": now, "dev": "pc"}
            else:
                e = book[live[key]]
                if any(e["item"].get(f) != v for f, v in item.items() if f != "t"):
                    e.update(item=dict(e["item"], **item), updated=now, dev="pc")
        for key, i in live.items():
            if key not in keys:
                book[i].update(deleted=True, updated=now, dev="pc")

    @staticmethod
    def apply(book, lst, items, dev, now):
        """The app's changes into the PC file. Returns how many were taken."""
        pc_items, took, dirty = lst.read(), 0, False
        for x in items:
            iid = str(x.get("id", ""))
            if not iid:
                continue
            e = book.get(iid)
            if e and x.get("updated", 0) <= e.get("app_updated", 0):
                continue  # already applied (a retried sync)
            fields = {k: v for k, v in x.items() if k not in META}
            where = dict(keyed(lst, pc_items))
            took += 1
            if x.get("deleted"):
                if e and not e.get("deleted") and e["key"] in where:
                    old = where[e["key"]]
                    if isinstance(lst, Reminders):
                        old["done"] = True
                    else:
                        pc_items.remove(old)
                    dirty = True
                book[iid] = {"key": (e or {}).get("key", ""), "item": dict((e or {}).get("item", {}), **fields),
                             "updated": now, "deleted": True, "dev": dev, "app_updated": x.get("updated", 0)}
                continue
            if e and not e.get("deleted") and e["key"] in where:
                old = where[e["key"]]
                new = lst.to_pc(fields, old, dev)
                pc_items[pc_items.index(old)] = new
            else:
                new = lst.to_pc(fields, None, dev)
                if isinstance(lst, Reminders):
                    new["id"] = max([r.get("id", 0) for r in pc_items] or [0]) + 1
                    new = {"id": new.pop("id"), **new}
                pc_items.append(new)
            dirty = True
            key = next(k for k, pc in keyed(lst, pc_items) if pc is new)
            book[iid] = {"key": key, "item": lst.to_app(new) | {k: v for k, v in fields.items() if k == "t"},
                         "updated": now, "dev": dev, "app_updated": x.get("updated", 0)}
        if dirty:
            lst.write(pc_items)
        return took

    @staticmethod
    def mode(asked):
        """His personality mode: the app may switch it (asked = "love", "work", "normal"...); both get the result."""
        from core import persona
        if asked:
            persona.clear() if asked == "normal" else persona.set_mode(asked)
        try:
            mode = open("data/mode.txt", encoding="utf-8").read().strip()
        except OSError:
            mode = ""
        try:
            text = open("data/persona.txt", encoding="utf-8").read().strip()
        except OSError:
            text = ""
        return {"mode": mode or "normal", "persona": text}
