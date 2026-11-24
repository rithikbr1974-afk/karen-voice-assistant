"""Karen's own voice on a real phone call, free: the second phone (spare SIM) dials, goes on speakerphone, and sits
next to the PC. Karen speaks through the PC speakers into the phone's mic and hears the other person from the
phone's speaker through the PC mic: plain Mic -> STT -> Brain -> Speaker turns, no barge-in.

The call opens by saying it's an AI assistant calling for the user, uses only private (zero-retention) backends,
ends when the goal is done ([END] from the LLM), after max_s, or when the other side hangs up.
"""
import re
import time

from core.brain import Brain

LOUD, SLOW = 0.9, 0.9  # PC master volume and Kokoro speed while she talks into the phone


def pc_volume(level=None):
    """Set the PC's master volume (0-1); returns what it was. None just reads it."""
    import comtypes
    from pycaw.pycaw import AudioUtilities
    comtypes.CoInitialize()
    try:
        ep = AudioUtilities.GetSpeakers().EndpointVolume
        was = ep.GetMasterVolumeLevelScalar()
        if level is not None:
            ep.SetMasterVolumeLevelScalar(max(was, level) if level == LOUD else level, None)
        return was
    finally:
        comtypes.CoUninitialize()

PROMPT = ("You are Karen, an AI voice assistant, on a live phone call made on behalf of the user. {who_line} Your goal: "
          "{goal}. Always respond to what they just said first, then steer back to the goal. Speak in one or two short "
          "spoken sentences per turn, plain words, no lists or emojis. Say you are "
          "an AI if asked. On this call you can only talk: never say you'll do a task (open apps, send things); say "
          "The user can ask you after the call. Say times like a person (\"seven twelve PM\"), never time zones. Never share the user's personal details, passwords, codes or payment information, never "
          "agree to pay or buy anything, and make no promises for him. The other person's words come from speech "
          "recognition through a phone speaker and may be misheard; ask them to repeat if it makes no sense. When the "
          "goal is done, or they want to stop, say a short goodbye and end your reply with [END].")


class CallVoice:
    def __init__(self, cfg, mic, stt, speaker, log):
        """cfg: config.yaml (llm backends); mic/stt/speaker: Karen's own."""
        llm = cfg["llm"]
        from core.brain import private_backends
        private = private_backends(llm)
        backends = sorted([b for b in llm["backends"] if b["name"] in private], key=lambda b: private.index(b["name"]))
        self.backends, self.max_tokens = backends, 120
        self.mic, self.stt, self.speaker, self.log = mic, stt, speaker, log

    def run(self, goal, who, state, hang_up, max_s=300, answer_wait_s=45, on_answer=None):
        """state() -> "dialing" | "active" | "ended" (from the phone); on_answer() runs once they pick up (call volume
        up). Returns a short report with the transcript."""
        t0 = time.time()
        while state() == "dialing" and time.time() - t0 < answer_wait_s:
            time.sleep(0.7)
        if state() != "active":
            hang_up()
            return f"{who} didn't answer."
        if on_answer:
            on_answer()
        speed, volume = self.speaker.speed, pc_volume(LOUD)  # into the phone's mic: louder, a little slower
        self.speaker.speed = min(speed, SLOW)
        try:
            return self._talk(goal, who, state, hang_up, max_s, t0)
        finally:
            self.speaker.speed = speed
            pc_volume(volume)

    def _talk(self, goal, who, state, hang_up, max_s, t0):
        own = who.lower() in ("owner", "you", "me")
        brain = Brain(self.backends, PROMPT.format(
            goal=goal, who_line="You are calling the user himself." if own else f"You are calling {who}."),
            self.max_tokens, history_turns=20)
        brain.use_persona = False  # his love/work mode is for him, not for phone calls
        transcript = []

        def say(text):
            transcript.append(f"Karen: {text}")
            self.speaker.say(text)
            time.sleep(0.4)       # the phone's echo of her voice
            self.mic.flush()

        time.sleep(1.0)  # people say "hello" first
        self.mic.flush()
        opening = "Hi, it's Karen, calling from your second phone." if own else \
            "Hello, this is Karen, an AI assistant calling on behalf of the user."
        heard = "(call connected)"
        say(opening)
        silent = 0
        while time.time() - t0 < max_s:
            audio = self.mic.listen(timeout=9)
            if state() != "active":
                transcript.append("(they hung up)")
                break
            text = self.stt.transcribe(audio).strip() if audio is not None else ""
            if not text:
                silent += 1
                if silent >= 2:
                    say("I can't hear you, so I'll hang up now. Bye!")
                    break
                say("Sorry, are you there?")
                continue
            silent = 0
            transcript.append(f"{who}: {text}")
            heard = text
            reply = "".join(brain.ask(heard if len(transcript) > 2 else f"{heard} (they just answered; you already "
                                                                         f"said: {opening})")).strip()
            done = "[END]" in reply
            reply = re.sub(r"\s*\[END\]\s*", " ", reply).strip()
            if reply:
                say(reply)
            if done:
                break
        else:
            say("I have to go now. Goodbye!")
        hang_up()
        self.log("callvoice", f"{who}: {len(transcript)} lines, {time.time() - t0:.0f}s")
        return f"Call with {who} ended after {time.time() - t0:.0f} s.\n" + "\n".join(transcript)
