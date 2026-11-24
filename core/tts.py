"""Text to speech with Kokoro (ONNX, CPU). Speaks sentence by sentence while text streams in."""
import queue
import re
import threading
import time

import numpy as np
import sounddevice as sd
from kokoro_onnx import Kokoro

SENTENCE_END = re.compile(r"(?<=[.!?;:])\s+|\n+")
# where the first sentence may be cut so Karen starts talking sooner: a comma, or before a joining word
FIRST_BREAK = re.compile(r", | (?=(?:and|but|so|because|by|with|for|or|which|when|if|then|from|until) )")


def clean(text):
    text = re.sub(r"[*_#`>|~]", "", text)  # markdown
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)  # links -> label
    return re.sub(r"[^\x00-ɏ‘-”… ]", "", text).strip()  # emojis etc.


class Speaker:
    def __init__(self, model, voices, voice="af_heart", speed=1.0, lang="en-us", device=None):
        self.kokoro = Kokoro(model, voices)
        self.voice, self.speed, self.lang, self.device = voice, speed, lang, device
        self.stop_event = threading.Event()  # set() from another thread to cut speech short
        self.cache = {}
        self.level = 0.0       # loudness of what is playing now, for the UI
        self.on_start = None   # optional callback when sound starts
        self.first_audio = None
        self.volume = lambda: 1.0  # main sets this: quieter at night (night mode)

    def synth(self, text):
        if text in self.cache:
            return self.cache[text]
        samples, _ = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang=self.lang)
        if len(text) <= 20:
            self.cache[text] = samples  # short stock phrases like "Yes?"
        return samples

    def say(self, text):
        return self.speak_stream([text])

    def say_in(self, text, lang, voice):
        """One line in another language and voice (translations). No clean(): it would strip non-Latin script."""
        self.stop_event.clear()
        samples, _ = self.kokoro.create(text, voice=voice, speed=self.speed, lang=lang)
        samples = samples * self.volume()
        with sd.OutputStream(samplerate=24000, channels=1, dtype="float32", device=self.device) as out:
            for i in range(0, len(samples), 2400):
                if self.stop_event.is_set():
                    break
                block = samples[i:i + 2400]
                self.level = float(np.sqrt(np.mean(block ** 2)))
                out.write(block.reshape(-1, 1))
            self.level = 0.0
            out.write(np.zeros((4800, 1), dtype=np.float32))

    def chime(self):
        """A short soft two-note "I'm listening" sound, instead of saying "Yes?"."""
        t = np.arange(int(24000 * 0.09)) / 24000
        fade = np.minimum(1, np.minimum(t, t[::-1]) / 0.01)  # 10 ms fades, no clicks
        notes = [0.12 * self.volume() * np.sin(2 * np.pi * f * t) * fade for f in (880, 1320)]
        sd.play(np.concatenate(notes).astype(np.float32), 24000, device=self.device, blocking=True)

    def speak_stream(self, chunks):
        """Speak an iterable of text chunks (e.g. a streaming LLM answer). Returns the full text."""
        self.stop_event.clear()
        self.first_audio = None  # time.time() when sound starts, for latency logging
        audio_q = queue.Queue(maxsize=8)
        spoken = []
        errors = []

        def produce():
            buf, first = "", True
            try:
                for chunk in chunks:
                    if self.stop_event.is_set():
                        break
                    buf += chunk
                    spoken.append(chunk)
                    parts = SENTENCE_END.split(buf)
                    if first and len(parts) == 1 and (m := FIRST_BREAK.search(buf, 25)):
                        keep = m.start() + (buf[m.start()] == ",")  # keep the comma, drop the space
                        parts = [buf[:keep], buf[m.end():]]  # start talking at the first natural break
                    first = first and len(parts) == 1
                    for sentence in parts[:-1]:
                        self._queue(sentence, audio_q)
                    buf = parts[-1]
                    if len(buf) > 250:  # very long sentence: break at a comma or space
                        cut = max(buf.rfind(", ", 0, 250), buf.rfind(" ", 0, 250))
                        self._queue(buf[:cut], audio_q)
                        buf = buf[cut:]
                self._queue(buf, audio_q)
            except Exception as e:
                errors.append(e)
            finally:
                audio_q.put(None)

        threading.Thread(target=produce, daemon=True).start()
        gain = self.volume()
        with sd.OutputStream(samplerate=24000, channels=1, dtype="float32", device=self.device) as out:
            while (samples := audio_q.get()) is not None:
                if not self.first_audio:
                    self.first_audio = time.time()
                    if self.on_start:
                        self.on_start()
                for i in range(0, len(samples), 2400):  # 100 ms blocks so stop is quick
                    if self.stop_event.is_set():
                        break
                    block = samples[i:i + 2400] * gain
                    self.level = float(np.sqrt(np.mean(block ** 2)))
                    out.write(block.reshape(-1, 1).astype(np.float32))
            self.level = 0.0
            if not self.stop_event.is_set():
                out.write(np.zeros((4800, 1), dtype=np.float32))  # let the tail play out
        if errors:
            raise errors[0]
        return "".join(spoken)

    def _queue(self, text, audio_q):
        text = clean(text)
        if text and not self.stop_event.is_set():
            audio_q.put(self.synth(text))
