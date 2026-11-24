"""LLM via OpenAI-compatible APIs (Gemini, OpenRouter). Backends are tried in order.

Plain chat streams. Requests with tools run a non-streaming tool loop first (only on the private backends),
then the final answer is handed to the speaker.
"""
import datetime
import json
import os
import re
import time

from openai import OpenAI


def private_backends(llm):
    """Backends allowed to see tool data (emails, files, camera). Groq only once its Zero Data Retention is on."""
    names = list(llm.get("private_backends", ()))
    groq = [b["name"] for b in llm.get("backends", ()) if b["name"].startswith("groq")] or ["groq"]  # groq, groq-120b
    return groq + [n for n in names if n not in groq] if llm.get("groq_zdr") else names


CLAIM = re.compile(r"\bI(?:'ve| have)\s+(?:just |now |already |successfully )*(scheduled|set|created|added|sent|"
                   r"booked|saved|deleted|removed|turned|opened|started|stopped|cancell?ed|updated|moved|noted|called|"
                   r"played|paused|muted|locked|reminded)\b|\bI\s+(?:just |already |successfully )*(scheduled|created|"
                   r"added|sent|booked|saved|deleted|removed|turned|opened|started|stopped|cancell?ed|updated|moved|"
                   r"called|played|paused|muted|locked)\b", re.I)


def claims_action(reply):
    """A sentence (not a question) where she says she did something. Only meaningful when no tool ran: then it's
    made up (2026-11-06, plain chat: "I have scheduled a meeting titled 'Meet' for the 10th", nothing scheduled)."""
    return any(CLAIM.search(s) for s in re.split(r"(?<=[.!?])\s+", reply.strip()) if s and not s.endswith("?"))


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
        self.extra = lambda: ""  # voice Karen adds a line here (night mode: short answers)
        self.last = {}   # which backend answered, LLM seconds, whether a fallback was needed
        self.use_persona = True
        # Background jobs (evening wrap-up, mail watch) set this: when every backend failed and one only said "rate
        # limit, try again in N s" with N <= patience, wait and try once more. Voice keeps 0: better a quick "sorry".
        # The wrap-up failed every night (2026-10-30..11-07): Groq's 8K tokens/min ran out in round 2 of its tool loop
        # ("try again in 17.9 s") and OpenRouter, the last backend, had no credits.
        self.patience = 0

    def _messages(self, user, details=True):
        """details=False (plain chat): no tool calls in the history. With them, gpt-oss sometimes called a tool in a
        chat request and Groq failed it ("Tool choice is none, but model called a tool", 2026-10-27)."""
        now = datetime.datetime.now().astimezone()
        persona = self.persona() if self.use_persona else ""  # Dots keep their own role, not Karen's mode
        facts = self.memory() if self.use_persona else ""
        system = {"role": "system", "content": f"{self.system_prompt} Now: {now:%A %d %B %Y, %I:%M %p}, "
                                               f"UTC{now:%z}." + (f" The user asked you to always talk {persona}; stay "
                                                                  f"in that character in every reply." if persona else "")
                                               + (f" Things the user asked you to remember (use them when they fit; "
                                                  f"newest first): {facts}." if facts else "")
                                               + self.extra()}
        msgs = [system]
        turns = self.turns[-self.history_turns:]
        for i, turn in enumerate(turns):
            msgs += turn if details and i == len(turns) - 1 else [turn[0], turn[-1]]  # tool details: latest turn
        return msgs + [user]

    def ask(self, text, tools=None, run_tool=None):
        """Return an iterator of answer text. With tools, the tool loop runs now (in the caller's thread)."""
        user = {"role": "user", "content": text}
        if not tools:
            return self._stream(user)
        return iter([self._tool_loop(user, tools, run_tool)])

    def correct_claim(self):
        """After a made-up "I have scheduled it" (no tool ran): mark it in the history, so the next turn doesn't build
        on it, and return the line to tell him."""
        if self.turns:
            self.turns[-1][-1]["content"] += " [Correction: nothing was actually done; no tool ran.]"
        return "Correction: I didn't actually do that. Say it again, and I'll really do it."

    def complete(self, text):
        """One answer to text with no tools, not streamed and not kept in the history, on the private backends only
        (the text carries tool data: mail, calendar). Waits out short rate limits when patience is set."""
        backends = [b for b in self.backends if b["name"] in self.private]
        if not backends:
            raise RuntimeError("no private AI backend (Groq Zero Data Retention or OpenRouter credits)")
        msg = self._create(backends, self._messages({"role": "user", "content": text}, details=False), None)
        return self.dedupe((msg.content or "").strip())

    def _stream(self, user):
        messages = self._messages(user, details=False)
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
                            self._ok(b)
                        answer += chunk.choices[0].delta.content
                        yield chunk.choices[0].delta.content
                break
            except Exception as e:
                if answer:  # cut off after the first words (already spoken): keep them, don't start a second answer
                    break
                if i == len(self.backends) - 1:
                    raise  # nothing left to try
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
                answer = self.dedupe((msg.content or "").strip())  # "PC locked.PC locked." (gpt-oss, 2026-10-27)
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
        failed, limited, error = "", {}, None  # limited: backend name -> seconds it asked us to wait
        for i, b in enumerate(backends):
            if self._resting(b, i, backends):
                continue
            try:
                return self._call(b, messages, tools, failed)
            except Exception as e:
                failed += self._failed(b, e)
                error = e
                if self.retry_after(e) is not None:
                    limited[b["name"]] = self.retry_after(e)
                if i == len(backends) - 1 and not (limited and min(limited.values()) <= self.patience):
                    e.karen_failed = failed  # what went wrong on each backend, for the log
                    raise
        # every backend failed, but some only for a few seconds (rate limit): wait for the soonest, try those again
        time.sleep(min(limited.values()) + 0.5)
        for b in sorted((b for b in backends if b["name"] in limited), key=lambda b: limited[b["name"]]):
            try:
                return self._call(b, messages, tools, failed)
            except Exception as e:
                failed += self._failed(b, e)
                error = e
        error.karen_failed = failed
        raise error

    def _call(self, b, messages, tools, failed):
        budget, slipped = max(self.max_tokens, 800), False
        while True:
            try:
                r = b["client"].chat.completions.create(model=b["model"], messages=messages, max_tokens=budget,
                                                        **({"tools": tools} if tools else {}), **b.get("params", {}))
                self.last = {"backend": b["name"], "first": 0.0, "failed": failed}
                self._ok(b)
                return r.choices[0].message
            except Exception as e:
                # gpt-oss now and then writes a broken tool call and Groq rejects it (400 tool_use_failed). It's a
                # random slip, so the same model tries once more: on 2026-10-31 "shut down my laptop" fell through
                # to DeepSeek, which said "Shutting down in 10 seconds" without calling any tool.
                if getattr(e, "code", None) == "tool_use_failed" and not slipped:
                    slipped = True
                    continue
                # OpenRouter with a few cents left refuses the whole request ("requested up to 800 tokens, but can
                # only afford 265": the 2026-11-09 wrap-up). Ask again for what it can pay for, if that's enough.
                afford = re.search(r"can only afford (\d+)", str(e)) if getattr(e, "status_code", None) == 402 else None
                if afford and 150 <= int(afford[1]) - 10 < budget:
                    budget = int(afford[1]) - 10
                    continue
                raise

    @staticmethod
    def retry_after(e):
        """Seconds a rate-limited backend asks us to wait (Groq: "Please try again in 17.94s"), else None."""
        import re
        if getattr(e, "status_code", None) != 429:
            return None
        try:
            header = e.response.headers.get("retry-after")
            if header:
                return float(header)
        except (AttributeError, ValueError):
            pass
        m = re.search(r"try again in (?:(\d+)m)?([\d.]+)s", str(e))
        return int(m.group(1) or 0) * 60 + float(m.group(2)) if m else None

    @staticmethod
    def dedupe(text):
        """A reply that is the same text twice in a row (a provider glitch seen once) becomes one copy."""
        t = text.strip()
        half = len(t) // 2
        return t[:half].strip() if half > 5 and t[:half].strip() == t[half:].strip() else text

    @staticmethod
    def _failed(b, e):
        code = getattr(e, "status_code", None)
        if code == 402:  # out of credits: don't try it again for an hour
            b["rest_until"] = time.time() + 3600
        # slow or overloaded twice in a row: skip it for 10 minutes. Gemini timed out on nearly every reply for days
        # (2026-10-20..24), so each answer waited the whole timeout before Groq spoke (voice started after ~9 s).
        # A plain APIConnectionError is our own internet being down (it hits every backend at once), so it doesn't
        # count: otherwise Gemini and Groq rested for 10 min after the Wi-Fi came back and paid DeepSeek answered.
        if code in (429, 500, 502, 503, 504) or "Timeout" in type(e).__name__:
            b["strikes"] = b.get("strikes", 0) + 1
            if b["strikes"] >= 2:
                b["rest_until"], b["strikes"] = time.time() + 600, 0
        reason = getattr(e, "code", None)  # e.g. tool_use_failed, rate_limit_exceeded
        return f"{b['name']} failed ({code or type(e).__name__}{' ' + reason if isinstance(reason, str) else ''}), "

    @staticmethod
    def _ok(b):
        b["strikes"] = 0

    @staticmethod
    def _resting(b, i, backends):
        """Skip a backend that is resting (out of credits, or slow twice in a row), unless it's the last one left."""
        return b.get("rest_until", 0) > time.time() and i < len(backends) - 1

    @staticmethod
    def persona(path="data/persona.txt"):
        """The personality he set (daily server's set_persona), read fresh every request so it applies at once."""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    # Facts that look like secrets stay in data/memory.json (recall finds them) but never ride along with every request:
    # plain chat goes to Gemini's free tier, where Google may use prompts.
    SECRET = re.compile(r"(?i)pass(word|code)|\bpin\b|otp|cvv|upi|account (no|number)|card|code is|\d{4,}")

    @staticmethod
    def memory(path="data/memory.json", limit=700):
        """What he asked Karen to remember, newest first, at most `limit` characters (~175 tokens). From Friday
        (2026-11-24): it sends its memory with every request; Karen's `remember` facts only reached the AI when it
        happened to call `recall`, so "when I ask who X is, say Y" was forgotten in the next conversation."""
        try:
            with open(path, encoding="utf-8") as f:
                facts = [str(x.get("fact", "")).strip() for x in json.load(f)]
        except (OSError, ValueError, AttributeError):
            return ""
        keep, size = [], 0
        for fact in reversed(facts):
            if not fact or Brain.SECRET.search(fact):
                continue
            if size + len(fact) > limit:
                break
            keep.append(fact)
            size += len(fact) + 3
        return " | ".join(keep)

    def forget(self):
        self.turns.clear()
