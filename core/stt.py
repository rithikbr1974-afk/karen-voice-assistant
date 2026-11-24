"""Speech to text: Groq's Whisper large-v3-turbo when `stt.cloud` is set (his choice, 2026-10-12: far fewer misheard
words), else / on any failure the local faster-whisper (int8, CPU)."""
import io
import os
import wave

import numpy as np
from faster_whisper import WhisperModel


class STT:
    def __init__(self, model="base.en", compute_type="int8", models_dir="models", threads=4, cloud=None):
        self.model = WhisperModel(model, device="cpu", compute_type=compute_type,
                                  cpu_threads=threads, download_root=models_dir)
        self.cloud, self.client, self.used = cloud or {}, None, "local"
        if self.cloud.get("model") and os.environ.get("GROQ_API_KEY"):
            from openai import OpenAI
            self.client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=os.environ["GROQ_API_KEY"],
                                 timeout=self.cloud.get("timeout", 5), max_retries=0)

    def transcribe(self, audio, prompt=None):
        if self.client:
            try:
                text = self._groq(audio, prompt)
                self.used = "groq"
                return text
            except Exception as e:  # offline, rate limit, slow: the local model still works
                self.used = f"local ({type(e).__name__})"
        else:
            self.used = "local"
        segments, _ = self.model.transcribe(audio, language="en", beam_size=1,
                                            vad_filter=False, initial_prompt=prompt,
                                            condition_on_previous_text=False)
        return " ".join(s.text.strip() for s in segments if not self.made_up(s)).strip()

    def _groq(self, audio, prompt):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(16000)
            w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
        r = self.client.audio.transcriptions.create(
            model=self.cloud["model"], file=("speech.wav", buf.getvalue()), language="en",
            response_format="verbose_json", temperature=0, **({"prompt": prompt} if prompt else {}))
        segments = getattr(r, "segments", None) or []
        if not segments:
            return (r.text or "").strip()
        keep = [s for s in segments if not self.made_up(s)]
        text = " ".join(self._get(s, "text").strip() for s in keep).strip()
        return text if any(ch.isalnum() for ch in text) else ""  # noise comes back as "." from Groq

    @staticmethod
    def _get(seg, name):
        return seg[name] if isinstance(seg, dict) else getattr(seg, name)

    @staticmethod
    def made_up(seg):
        """Whisper invents words from music or noise ("I'm a foreign and I'm a foreign"): drop segments it was
        unsure held speech, or that just repeat themselves."""
        g = STT._get
        return ((g(seg, "no_speech_prob") > 0.6 and g(seg, "avg_logprob") < -0.8) or g(seg, "avg_logprob") < -1.2
                or g(seg, "compression_ratio") > 2.4)
