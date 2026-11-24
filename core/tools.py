"""MCP client: starts the servers in config.yaml -> mcp_servers and gives their tools to the brain.

The MCP SDK is async and Karen's loop is not, so one asyncio loop runs in a background thread and the
rest of Karen calls into it with run_coroutine_threadsafe. Tool schemas are slimmed before they reach the
LLM (first paragraph of descriptions, hidden and auto-filled args) and only the tools a request needs are sent.
"""
import asyncio
import fnmatch
import json
import os
import re
import sys
import threading
import time

TASK_START = re.compile(r"^(please |can you |could you |karen,? )*(send|create|make|add|delete|remove|check|find|"
                        r"search|read|show|open|schedule|book|reply|forward|move|list|write|update|cancel)\b", re.I)


class Tools:
    def __init__(self, servers, confirm, log=print, max_result_chars=2500, ask=None):
        """ask(question) -> bool answers a server's mid-task question (MCP elicitation), e.g. Karen's spoken yes/no."""
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.confirm = confirm or []
        self.ask = ask
        self.log = log
        self.max_chars = max_result_chars
        self.status = {}   # server name -> "starting" | "on" | "error"
        self.tools = {}    # "server__tool" -> {"server", "name", "schema", "group"}
        self.clients = {}
        self.on_change = None  # called when a server finishes starting
        self.specs = {n: s for n, s in (servers or {}).items() if s.get("enabled", True)}  # setup.py turns them on/off
        for name, spec in self.specs.items():
            self.status[name] = "starting"
            asyncio.run_coroutine_threadsafe(self._serve(name, spec), self.loop)

    # ------------------------------------------------------------- servers
    async def _serve(self, name, spec):
        from mcp import Client, StdioServerParameters, stdio_client
        env = {k: os.path.expandvars(str(v)) for k, v in (spec.get("env") or {}).items()}
        missing = [k for k, v in env.items() if "$" in v or not v]
        env.setdefault("PYTHONUTF8", "1")  # Windows' default cp1252 crashed workspace-mcp's logging on "✓"
        env.setdefault("PYTHONIOENCODING", "utf-8")
        if missing:
            self.status[name] = "setup"
            self.log("tools", f"{name}: not started, set {', '.join(missing)} in .env")
            self._changed()
            return
        command = sys.executable if spec["command"] == "{python}" else spec["command"]  # Karen's own venv
        params = StdioServerParameters(command=command, args=[str(a) for a in spec.get("args", [])], env=env)
        try:
            with open(f"logs/mcp_{name}.log", "a", encoding="utf-8") as errlog:
                # legacy mode: servers can ask questions mid-tool (ctx.elicit), e.g. power mode's risky steps
                async with Client(stdio_client(params, errlog=errlog), mode="legacy",
                                  elicitation_callback=self._elicit) as client:
                    listed, cursor = [], None
                    while True:
                        page = await client.list_tools(cursor=cursor)
                        listed += page.tools
                        cursor = page.next_cursor
                        if not cursor:
                            break
                    for t in listed:
                        self.tools[f"{name}__{t.name}"] = {"server": name, "name": t.name,
                                                           "group": self._group(spec, t.name),
                                                           "schema": self._slim(name, spec, t)}
                    self.clients[name] = client
                    self.status[name] = "on"
                    self.log("tools", f"{name}: {len(listed)} tools ready")
                    self._changed()
                    await asyncio.Event().wait()  # keep the server running for Karen's lifetime
        except Exception as e:
            self.status[name] = "error"
            self.log("tools", f"{name} failed to start: {e!r} (see logs/mcp_{name}.log)")
            self._changed()

    async def _elicit(self, context, params):
        """A server asks a yes/no question while a tool runs: answered by self.ask (Karen speaks it and listens)."""
        from mcp.types import ElicitResult
        if not self.ask or getattr(params, "mode", "form") != "form":
            return ElicitResult(action="decline")
        yes = await asyncio.to_thread(self.ask, params.message)
        fields = ((params.requested_schema or {}).get("properties") or {})
        if not yes:
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={k: True for k, v in fields.items() if v.get("type") == "boolean"})

    def _changed(self):
        if self.on_change:
            self.on_change()

    @staticmethod
    def _group(spec, tool):
        """Which trigger group a tool belongs to (triggers may be a list, or {"pattern|pattern": [words]})."""
        if isinstance(spec.get("triggers"), dict):
            for patterns in spec["triggers"]:
                if any(fnmatch.fnmatch(tool, p) for p in patterns.split("|")):
                    return patterns
        return "*"

    def _slim(self, server, spec, tool):
        """OpenAI tool schema with short descriptions and without hidden / auto-filled args."""
        drop = set(spec.get("hide_args", [])) | set(spec.get("inject", {}))
        schema = json.loads(json.dumps(tool.input_schema or {"type": "object", "properties": {}}))
        props = {}
        for arg, p in schema.get("properties", {}).items():
            if arg in drop:
                continue
            p = {k: v for k, v in p.items() if k != "title" and not (k == "default" and v is None)}
            if "description" in p:
                p["description"] = p["description"][:120]
            props[arg] = p
        schema["properties"] = props
        schema["required"] = [r for r in schema.get("required", []) if r in props]
        schema.pop("title", None)
        schema.pop("$schema", None)
        desc = (tool.description or "").strip().split("\n\n")[0].replace("\n", " ")[:300]
        return {"type": "function", "function": {"name": f"{server}__{tool.name}", "description": desc,
                                                 "parameters": schema}}

    # ------------------------------------------------------------- routing
    def select(self, text, sticky=()):
        """Schemas for the tools this request needs: matching triggers, plus groups used in the last turn."""
        words = text.lower()
        groups = set(sticky)
        for name, spec in self.specs.items():
            trig = spec.get("triggers") or []
            for group, keys in (trig.items() if isinstance(trig, dict) else [("*", trig)]):
                if any(re.search(rf"\b{re.escape(k)}", words) for k in keys):
                    groups.add((name, group))
        # a request right after start-up: wait for the servers it needs instead of answering without tools
        needed = {server for server, _ in groups} or (set(self.specs) if TASK_START.match(text.strip()) else set())
        deadline = time.time() + 30
        while any(self.status.get(s) == "starting" for s in needed) and time.time() < deadline:
            time.sleep(0.2)
        if not groups and TASK_START.match(text.strip()):
            groups = {(t["server"], t["group"]) for t in self.tools.values()}  # looks like a task: send all
        return [t["schema"] for t in self.tools.values() if (t["server"], t["group"]) in groups]

    def groups_of(self, schemas):
        """The (server, group) pairs of the tool schemas select() returned."""
        return {self.group_of(s["function"]["name"]) for s in schemas} - {None}

    def group_of(self, full_name):
        t = self.tools.get(full_name)
        return (t["server"], t["group"]) if t else None

    @staticmethod
    def allows(rules, full_name):
        """rules like ["dev", "dots:*page*"]: a whole server, or server:tool-glob (a Dot's allowed tools)."""
        server, _, tool = full_name.partition("__")
        return any(r == server or (r.startswith(server + ":") and fnmatch.fnmatch(tool, r.split(":", 1)[1]))
                   for r in rules)

    def schemas(self, rules):
        """Schemas of the tools a Dot may use, waiting (<=30 s) for its servers if they're still starting."""
        needed = {r.split(":")[0] for r in rules}
        deadline = time.time() + 30
        while any(self.status.get(s) == "starting" for s in needed) and time.time() < deadline:
            time.sleep(0.2)
        return [t["schema"] for n, t in self.tools.items() if self.allows(rules, n)]

    def needs_confirm(self, full_name):
        return any(fnmatch.fnmatch(full_name, p) for p in self.confirm)

    # ------------------------------------------------------------- calls
    def call(self, full_name, args):
        """Run a tool and return its result as (trimmed) text."""
        t = self.tools.get(full_name)
        if not t or t["server"] not in self.clients:
            return f"Error: tool {full_name} is not available."
        spec = self.specs[t["server"]]
        timeout = spec.get("timeout", 90)
        args = dict(args or {}, **{k: os.path.expandvars(str(v)) for k, v in (spec.get("inject") or {}).items()})
        fut = asyncio.run_coroutine_threadsafe(self.clients[t["server"]].call_tool(t["name"], args), self.loop)
        try:
            result = fut.result(timeout)
        except Exception as e:
            fut.cancel()
            return f"Error: {type(e).__name__}: {e}"
        text = "\n".join(getattr(b, "text", "") for b in result.content).strip()
        if not text and result.structured_content:
            text = json.dumps(result.structured_content)
        return ("Error: " if result.is_error else "") + self.trim(text, spec.get("max_result_chars"))

    def trim(self, text, max_chars=None):
        max_chars = max_chars or self.max_chars
        text = re.sub(r"(?im)^\s*(web link|link|url):.*$\n?", "", text)  # links the voice never reads
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if len(text) > max_chars:
            text = text[:max_chars] + "\n...(truncated)"
        return text
