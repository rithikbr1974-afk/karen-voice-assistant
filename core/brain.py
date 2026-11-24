"""LLM via OpenAI-compatible APIs (Gemini, OpenRouter). Backends are tried in order.

Plain chat streams. Requests with tools run a non-streaming tool loop first (only on the private backends),
then the final answer is handed to the speaker.
"""
import datetime
import json
import os
import time

from openai import OpenAI


def load_config(path="config.yaml"):
    """config.yaml, with {user} in the system prompt filled in from user.name (set by setup.py)."""
    import yaml
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    user = cfg.get("user") or {}
    llm = cfg.setdefault("llm", {})
    llm["system_prompt"] = (llm.get("system_prompt", "").replace("{user}", user.get("name") or "the user")
                            .replace("{accent}", user.get("accent") or ""))
    return cfg


def private_backends(llm):
    """Backends allowed to see tool data (emails, files, camera). Groq only once its Zero Data Retention is on."""
    names = list(llm.get("private_backends", ()))
    return ["groq"] + names if llm.get("groq_zdr") else names


class Brain:
    def __init__(self, backends, system_prompt, max_tokens=300, history_turns=6, private=(), max_rounds=5):
        """backends: list of {name, base_url, key_env, model, params}. Ones without a key in .env are skipped."""
        self.backends = [dict(b, client=OpenAI(base_url=b["base_url"], api_key=os.environ[b["key_env"]],
                                               timeout=b.get("timeout", 30), max_retries=b.get("retries", 0)))
                         for b in backends if os.environ.get(b["key_env"])]
        if not self.backends:
            raise RuntimeError("No LLM API key found in .env")
        self.private = list(private)  # backend names allowed to see tool data (emails, calendar)
        self.system_prompt = system_prompt
        self.max_tokens = max_tokens
        self.history_turns = history_turns
        self.max_rounds = max_rounds
        self.turns = []  # each turn: [user, (assistant tool calls, tool results)..., assistant answer]
        self.last = {}   # which backend answered, LLM seconds, whether a fallback was needed
        self.use_persona = True

    def _messages(self, user):
        now = datetime.datetime.now().astimezone()
        persona = self.persona() if self.use_persona else ""  # Dots keep their own role, not Karen's mode
        system = {"role": "system", "content": f"{self.system_prompt} Now: {now:%A %d %B %Y, %I:%M %p}, "
                                               f"UTC{now:%z}." + (f" The user asked you to always talk {persona}; stay "
                                                                  f"in that character in every reply." if persona else "")}
        msgs = [system]
        turns = self.turns[-self.history_turns:]
        for i, turn in enumerate(turns):
            msgs += turn if i == len(turns) - 1 else [turn[0], turn[-1]]  # tool details only for the latest turn
        return msgs + [user]

    def ask(self, text, tools=None, run_tool=None):
        """Return an iterator of answer text. With tools, the tool loop runs now (in the caller's thread)."""
        user = {"role": "user", "content": text}
        if not tools:
            return self._stream(user)
        return iter([self._tool_loop(user, tools, run_tool)])

    def _stream(self, user):
        messages = self._messages(user)
        answer, failed = "", ""
        for i, b in enumerate(self.backends):
            t = time.time()
            if self._resting(b, i, self.backends):
                continue
            try:
                stream = b["client"].chat.completions.create(model=b["model"], messages=messages,
                                                             max_tokens=self.max_tokens, stream=True,
                                                             **b.get("params", {}))
                for chunk in stream:
                    if chunk.choices and chunk.choices[0].delta.content:
                        if not answer:
                            self.last = {"backend": b["name"], "first": time.time() - t, "failed": failed}
                        answer += chunk.choices[0].delta.content
                        yield chunk.choices[0].delta.content
                break
            except Exception as e:
                if answer or i == len(self.backends) - 1:
                    raise  # mid-answer or nothing left to try
                failed += self._failed(b, e)
        self.turns.append([user, {"role": "assistant", "content": answer}])

    def _tool_loop(self, user, tools, run_tool):
        backends = [b for b in self.backends if b["name"] in self.private] or self.backends
        messages, new, llm_s, answer = self._messages(user), [], 0.0, ""
        for _ in range(self.max_rounds):
            t = time.time()
            msg = self._create(backends, messages + new, tools)
            llm_s += time.time() - t
            if not msg.tool_calls:
                answer = (msg.content or "").strip()
                break
            new.append({"role": "assistant", "content": msg.content or "",
                        "tool_calls": [{"id": c.id, "type": "function",
                                        "function": {"name": c.function.name, "arguments": c.function.arguments}}
                                       for c in msg.tool_calls]})
            for c in msg.tool_calls:
                try:
                    args = json.loads(c.function.arguments or "{}")
                except ValueError:
                    args = {}
                new.append({"role": "tool", "tool_call_id": c.id, "content": run_tool(c.function.name, args)})
        if not answer:
            answer = "Sorry, that took too many steps. Could you ask in a simpler way?"
        self.last["first"] = llm_s
        self.turns.append([user] + new + [{"role": "assistant", "content": answer}])
        return answer

    def _create(self, backends, messages, tools):
        failed = ""
        for i, b in enumerate(backends):
            if self._resting(b, i, backends):
                continue
            try:
                r = b["client"].chat.completions.create(model=b["model"], messages=messages, tools=tools,
                                                        max_tokens=max(self.max_tokens, 800), **b.get("params", {}))
                self.last = {"backend": b["name"], "first": 0.0, "failed": failed}
                return r.choices[0].message
            except Exception as e:
                if i == len(backends) - 1:
                    raise
                failed += self._failed(b, e)

    @staticmethod
    def _failed(b, e):
        code = getattr(e, "status_code", None)
        if code == 402:  # out of credits: don't try it again for an hour
            b["rest_until"] = time.time() + 3600
        return f"{b['name']} failed ({code or type(e).__name__}), "

    @staticmethod
    def _resting(b, i, backends):
        """Skip a backend that recently said it's out of credits, unless it's the last one left."""
        return b.get("rest_until", 0) > time.time() and i < len(backends) - 1

    @staticmethod
    def persona(path="data/persona.txt"):
        """The personality they set (daily server's set_persona), read fresh every request so it applies at once."""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def forget(self):
        self.turns.clear()
