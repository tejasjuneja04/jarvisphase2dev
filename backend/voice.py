"""
JARVIS voice.

    speech_text()     turns chat text (markdown, tables, numbers) into something worth reading aloud
    SentenceBuffer    finds complete sentences in a token stream so speech starts before the answer ends
    Voice             picks the best engine that works, with a disk cache:
                        1. edge-tts  neural British male voice (needs internet)
                        2. macOS say + afconvert (offline, "Daniel")
                        3. nothing -> the browser falls back to its own speech synthesis
    SpeechSink        speaks a reply sentence by sentence on a background thread, in order

The audio is sent to the browser as base64 chunks; the browser adds the "JARVIS" processing (EQ, slapback,
room) with Web Audio, so it works the same for every engine.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import config
from lang import is_hindi_text, looks_roman_hinglish, roman_hinglish_to_devanagari
from lang import roman as lang_roman

# ============================================================
# TEXT PREPARATION
# ============================================================

_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F]")
_BOX = re.compile("[\u2500-\u257F•◈▲▼►→←↑↓]")
_ABBREV = {"e.g.": "for example", "i.e.": "that is", "vs.": "versus", "etc.": "et cetera",
           "approx.": "approximately", "Mr.": "Mister", "Mrs.": "Missus", "Dr.": "Doctor", "St.": "Saint"}


def speech_text(text: str) -> str:
    """Chat text -> clean spoken text. Code, tables and links are not read out."""
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)          # complete code blocks
    t = re.sub(r"```.*$", " ", t, flags=re.S)              # unfinished code block
    lines = []
    for line in t.split("\n"):
        if re.search(r"[\u2500-\u257F]{2,}", line) or re.match(r"^\s*\|.*\|\s*$", line):
            continue                                        # table rows and rules
        lines.append(line)
    t = "\n".join(lines)

    t = re.sub(r"`([^`]+)`", r"\1", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"\[\d+(?:,\s*\d+)*\]", "", t)              # citation markers [1][2]
    t = re.sub(r"\(\s*(?:see|cf\.?|source[s]?:?)?\s*\)", "", t, flags=re.I)   # ...leaving "(see )"
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.M)
    t = re.sub(r"(\*\*|__|~~)", "", t)
    t = re.sub(r"(?<!\w)[*_](?=\S)|(?<=\S)[*_](?!\w)", "", t)
    t = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", t, flags=re.M)
    t = re.sub(r"^\s*>\s?", "", t, flags=re.M)
    t = _BOX.sub(" ", t)
    t = _EMOJI.sub("", t)

    for k, v in _ABBREV.items():
        t = t.replace(k, v)
    t = re.sub(r"(\d+(?:\.\d+)?)\s?K\b", r"\1 thousand", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s?M\b", r"\1 million", t)
    t = re.sub(r"(\d+(?:\.\d+)?)\s?B\b", r"\1 billion", t)
    t = re.sub(r"(\d)\s?%", r"\1 percent", t)
    t = t.replace("&", " and ")

    # line breaks become pauses
    out = []
    for line in t.split("\n"):
        line = line.strip()
        if not line:
            continue
        out.append(line if re.search(r"[.!?:;,।]$", line) else line + ("।" if is_hindi_text(line) else "."))
    t = " ".join(out)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\s+([.,!?;:])", r"\1", t).strip()
    return t


# ============================================================
# SENTENCES
# ============================================================

_ABBR_END = ("mr.", "mrs.", "dr.", "st.", "vs.", "e.g.", "i.e.", "etc.", "approx.", "no.", "fig.")
_BOUNDARY = re.compile(r"(?<=[.!?])[\"')\]]?\s+(?=[\"'(\[]?[A-Z0-9\u0900-\u097F])|(?<=[।॥])\s*|\n{1,}")


def _extract(text: str, min_len: int = 14):
    """Split into (complete sentences, unfinished remainder)."""
    sentences, start = [], 0
    for m in _BOUNDARY.finditer(text):
        piece = text[start:m.start() + (1 if text[m.start():m.start() + 1] in ".!?" else 0)]
        if piece.strip().lower().endswith(_ABBR_END):
            continue
        cand = text[start:m.end()].strip()
        if len(cand) < min_len:
            continue
        sentences.append(cand)
        start = m.end()
    return sentences, text[start:]


def split_sentences(text: str, max_len: int = 260) -> list:
    """Sentences for a finished text; long ones are split at commas."""
    sentences, rest = _extract(text)
    if rest.strip():
        sentences.append(rest.strip())
    out = []
    for s in sentences:
        while len(s) > max_len:
            cut = s.rfind(",", 0, max_len)
            cut = cut if cut > 60 else s.rfind(" ", 0, max_len)
            cut = cut if cut > 0 else max_len
            out.append(s[:cut + 1].strip())
            s = s[cut + 1:].strip()
        if s:
            out.append(s)
    return out


class SentenceBuffer:
    """Feed streamed text; get back sentences as soon as they are complete. Skips fenced code."""

    def __init__(self):
        self.buf = ""

    def feed(self, delta: str) -> list:
        self.buf += delta
        self.buf = re.sub(r"```.*?```", " ", self.buf, flags=re.S)
        cut = self.buf.find("```")
        speakable, hidden = (self.buf, "") if cut < 0 else (self.buf[:cut], self.buf[cut:])
        sentences, rest = _extract(speakable)
        self.buf = rest + hidden
        return sentences

    def flush(self) -> str | None:
        tail = re.sub(r"```.*$", " ", re.sub(r"```.*?```", " ", self.buf, flags=re.S), flags=re.S).strip()
        self.buf = ""
        return tail or None


# ============================================================
# ENGINES
# ============================================================

class Engine:
    name = "base"

    def available(self) -> bool:
        return False

    def label(self) -> str:
        return self.name

    def synth(self, text: str, lang: str = "en"):
        """Return (audio_bytes, mime)."""
        raise NotImplementedError


class ElevenLabsEngine(Engine):
    """ElevenLabs text-to-speech over plain HTTPS (no extra package). Needs ELEVENLABS_API_KEY.
    Each sentence costs credits; replies are cached, so repeated lines are free."""
    name = "elevenlabs"
    _warned: set = set()

    def __init__(self):
        self.cool_until = 0.0

    def available(self) -> bool:
        return bool(config.ELEVEN_API_KEY and config.ELEVEN_VOICE_ID) and time.time() >= self.cool_until

    def label(self) -> str:
        return f"elevenlabs · {config.ELEVEN_VOICE_ID[:6]}… · {config.ELEVEN_MODEL}"

    def _warn_once(self, key: str, message: str):
        if key not in self._warned:
            self._warned.add(key)
            print(f"[voice] elevenlabs: {message}")

    def synth(self, text: str, lang: str = "en"):
        url = (f"{config.ELEVEN_URL.rstrip('/')}/v1/text-to-speech/{config.ELEVEN_VOICE_ID}"
               "?output_format=mp3_44100_128")
        payload = {
            "text": text,
            "model_id": config.ELEVEN_MODEL,
            "voice_settings": {
                "stability": config.ELEVEN_STABILITY,
                "similarity_boost": config.ELEVEN_SIMILARITY,
                "style": config.ELEVEN_STYLE,
                "use_speaker_boost": True,
            },
        }
        if lang == "hi" and config.ELEVEN_MODEL.endswith("v2_5"):
            payload["language_code"] = "hi"          # the same voice, speaking proper Hindi
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "xi-api-key": config.ELEVEN_API_KEY,
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        })
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = resp.read()
            if not data:
                raise RuntimeError("elevenlabs returned no audio")
            return data, "audio/mpeg"
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:
                pass
            if e.code == 401:
                self._warn_once("401", "the API key was rejected (check ELEVENLABS_API_KEY).")
                self.cool_until = time.time() + 600
            elif e.code in (402, 403):
                self._warn_once("plan", "this voice or request is not allowed on your plan. Library voices usually need "
                                        "a paid plan for API use, and the voice must be added to your account. " + detail)
                self.cool_until = time.time() + 600
            elif e.code == 404:
                self._warn_once("404", f"voice id '{config.ELEVEN_VOICE_ID}' was not found on your account.")
                self.cool_until = time.time() + 600
            elif e.code == 429:
                self._warn_once("429", "rate limit or credit quota reached; using the next voice for a while.")
                self.cool_until = time.time() + 90
            else:
                self.cool_until = time.time() + 45
            raise RuntimeError(f"elevenlabs HTTP {e.code}")
        except Exception:
            self.cool_until = time.time() + 45          # offline: let the next engine cover for a bit
            raise


class KokoroEngine(Engine):
    """Kokoro-82M: a small neural voice that runs fully offline. British male voices (bm_george, bm_fable,
    bm_lewis, bm_daniel) are the closest match to a butler-AI. First use downloads the model (~330 MB)."""
    name = "kokoro"

    def __init__(self):
        self._pipes = {}
        self.cool_until = 0.0

    def available(self) -> bool:
        if time.time() < self.cool_until:
            return False
        try:
            import kokoro  # noqa: F401
            import numpy  # noqa: F401
            return True
        except Exception:
            return False

    def label(self) -> str:
        return f"kokoro · {config.KOKORO_VOICE}"

    def _load(self, lang: str = "en"):
        code = "h" if lang == "hi" else "b"           # "b" = British English, "h" = Hindi
        if code not in self._pipes:
            from kokoro import KPipeline
            self._pipes[code] = KPipeline(lang_code=code)
        return self._pipes[code]

    def synth(self, text: str, lang: str = "en"):
        import io
        import wave

        import numpy as np

        try:
            pipe = self._load(lang)
            parts = []
            voice = config.KOKORO_VOICE_HI if lang == "hi" else config.KOKORO_VOICE
            for _gs, _ps, audio in pipe(text, voice=voice, speed=config.KOKORO_SPEED):
                arr = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                parts.append(np.asarray(arr, dtype="float32").reshape(-1))
            if not parts:
                raise RuntimeError("kokoro returned no audio")
            pcm = (np.clip(np.concatenate(parts), -1.0, 1.0) * 32767).astype("<i2")
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(24000)
                w.writeframes(pcm.tobytes())
            return buf.getvalue(), "audio/wav"
        except Exception:
            self.cool_until = time.time() + 60          # let the next engine cover for a minute
            raise


class EdgeEngine(Engine):
    """Microsoft neural voices through the free edge-tts package. Needs internet."""
    name = "edge"

    def __init__(self):
        self.cool_until = 0.0

    def available(self) -> bool:
        if time.time() < self.cool_until:
            return False
        try:
            import edge_tts  # noqa: F401
            return True
        except Exception:
            return False

    def label(self) -> str:
        return f"edge · {config.EDGE_VOICE}"

    def synth(self, text: str, lang: str = "en"):
        import edge_tts

        voice = config.EDGE_VOICE_HI if lang == "hi" else config.EDGE_VOICE
        pitch = "+0Hz" if lang == "hi" else config.EDGE_PITCH

        async def run():
            comm = edge_tts.Communicate(text, voice, rate=config.EDGE_RATE, pitch=pitch, volume=config.EDGE_VOLUME)
            buf = bytearray()
            async for chunk in comm.stream():
                if chunk.get("type") == "audio":
                    buf.extend(chunk["data"])
            return bytes(buf)

        try:
            data = asyncio.run(asyncio.wait_for(run(), timeout=20))
            if not data:
                raise RuntimeError("edge-tts returned no audio")
            return data, "audio/mpeg"
        except Exception:
            self.cool_until = time.time() + 45   # offline or blocked: let the next engine take over for a while
            raise


class SayEngine(Engine):
    """macOS built-in speech (offline). Converted to WAV so every browser can decode it."""
    name = "say"

    def available(self) -> bool:
        return sys.platform == "darwin" and shutil.which("say") is not None

    def label(self) -> str:
        return f"macOS say · {config.SAY_VOICE}"

    def synth(self, text: str, lang: str = "en"):
        voice = config.SAY_VOICE_HI if lang == "hi" else config.SAY_VOICE
        with tempfile.TemporaryDirectory() as d:
            aiff, wav = Path(d) / "o.aiff", Path(d) / "o.wav"
            base = ["say", "-r", str(config.SAY_RATE), "-o", str(aiff), "--", text]
            try:
                subprocess.run(["say", "-v", voice] + base[1:], check=True, timeout=40, capture_output=True)
            except subprocess.CalledProcessError:
                subprocess.run(base, check=True, timeout=40, capture_output=True)   # voice missing: system default
            if shutil.which("afconvert"):
                subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", str(aiff), str(wav)],
                               check=True, timeout=40, capture_output=True)
                return wav.read_bytes(), "audio/wav"
            return aiff.read_bytes(), "audio/aiff"


# ============================================================
# VOICE
# ============================================================

class Voice:
    def __init__(self, engines: list | None = None):
        mode = config.VOICE_ENGINE
        if engines is not None:
            self.engines = engines
        elif mode == "off":
            self.engines = []
        elif mode == "say":
            self.engines = [SayEngine()]
        elif mode == "elevenlabs":
            self.engines = [ElevenLabsEngine(), EdgeEngine(), SayEngine()]
        elif mode == "kokoro":
            self.engines = [KokoroEngine(), SayEngine()]
        elif mode == "edge":
            self.engines = [EdgeEngine(), SayEngine()]
        else:  # auto: ElevenLabs (when a key is set) -> Microsoft neural (edge) -> macOS say -> browser voice.
            # Kokoro is no longer in the automatic chain (it sounds robotic); ask for it with JARVIS_VOICE_ENGINE=kokoro.
            self.engines = [ElevenLabsEngine(), EdgeEngine(), SayEngine()]
        self.enabled = bool(self.engines)
        self.last_engine = None
        self.cache_dir = config.TTS_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def info(self) -> dict:
        avail = [e for e in self.engines if e.available()]
        return {
            "enabled": bool(avail),
            "engine": avail[0].name if avail else None,
            "label": avail[0].label() if avail else "browser voice",
            "fallbacks": [e.name for e in avail[1:]],
        }

    # ---- cache ----
    def _key(self, engine: Engine, text: str, lang: str = "en") -> str:
        return hashlib.sha1(f"{engine.label()}|{lang}|{config.EDGE_RATE}|{config.EDGE_PITCH}|{text}".encode()).hexdigest()

    def _cache_get(self, key: str):
        f, m = self.cache_dir / f"{key}.bin", self.cache_dir / f"{key}.mime"
        if f.exists() and m.exists():
            return f.read_bytes(), m.read_text()
        return None

    def _cache_put(self, key: str, data: bytes, mime: str):
        try:
            (self.cache_dir / f"{key}.bin").write_bytes(data)
            (self.cache_dir / f"{key}.mime").write_text(mime)
            files = sorted(self.cache_dir.glob("*.bin"), key=lambda p: p.stat().st_mtime)
            for old in files[:-400]:
                old.unlink(missing_ok=True)
                old.with_suffix(".mime").unlink(missing_ok=True)
        except Exception:
            pass

    # ---- synthesis ----
    def synth(self, text: str, hint: str = "en") -> dict | None:
        """Speak already-clean text. Returns {"mime", "data" (base64), "engine"} or None.
        hint is the conversation language; WhatsApp-style Hinglish is respelled in Devanagari for the Hindi voice."""
        text = (text or "").strip()
        if not text or not self.enabled:
            return None
        text = text[:600]
        if is_hindi_text(text):
            lang = "hi"
        elif hint == "hi" and looks_roman_hinglish(text, hint):
            lang, text = "hi", roman_hinglish_to_devanagari(text)
        else:
            lang = "en"
        for engine in self.engines:
            if not engine.available() or (lang == "hi" and engine.name == "kokoro"):
                continue
            key = self._key(engine, text, lang)
            cached = self._cache_get(key)
            if cached:
                data, mime = cached
            else:
                try:
                    data, mime = engine.synth(text, lang)
                except Exception as e:
                    print(f"[voice] {engine.name} failed: {e}")
                    continue
                self._cache_put(key, data, mime)
            self.last_engine = engine.name
            return {"mime": mime, "data": base64.b64encode(data).decode("ascii"), "engine": engine.name}
        return None


# ============================================================
# SPEAKING A REPLY, SENTENCE BY SENTENCE
# ============================================================

class SpeechSink:
    """Queue sentences, synthesise them in order on a worker thread, emit audio events."""

    def __init__(self, voice: Voice, emit, turn_id: str, enabled: bool = True, title: str = "sir",
                 max_sentences: int | None = None, lang: str = "en"):
        self.voice, self.emit, self.turn_id = voice, emit, turn_id
        self.enabled = bool(enabled and voice.enabled)
        self.title = title
        self.lang = lang
        self.max_sentences = max_sentences or config.SPEAK_MAX_SENTENCES
        self.buf = SentenceBuffer()
        self.q: queue.Queue = queue.Queue()
        self.seq = 0
        self.queued = 0
        self.truncated = False
        self.stopped = False
        self.closed = False
        self.thread = None
        if self.enabled:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()

    # -- input --
    def feed(self, delta: str):
        if not self.enabled or self.stopped:
            return
        for s in self.buf.feed(delta):
            self._enqueue(s)

    def say(self, text: str):
        """Speak a finished piece of text."""
        if not self.enabled or self.stopped:
            return
        clean = speech_text(text)
        for s in split_sentences(clean):
            self._enqueue(s, already_clean=True)

    def _enqueue(self, sentence: str, already_clean: bool = False):
        s = sentence if already_clean else speech_text(sentence)
        if len(s) < 2:
            return
        if self.queued >= self.max_sentences:
            self.truncated = True
            return
        self.queued += 1
        self.q.put(s)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.enabled and not self.stopped:
            tail = self.buf.flush()
            if tail:
                self._enqueue(tail)
            if self.truncated:
                self.q.put(("Poora jawab screen par hai." if lang_roman() else "पूरा जवाब screen पर है।") if self.lang == "hi" else f"The full answer is on screen, {self.title}.")
        self.q.put(None)

    def stop(self):
        self.stopped = True
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass
        self.q.put(None)

    # -- worker --
    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                break
            if self.stopped:
                continue
            try:
                audio = self.voice.synth(item, self.lang)
            except Exception as e:
                print("[voice] synth error:", e)
                audio = None
            if audio and not self.stopped:
                self.emit({"type": "audio", "id": self.turn_id, "seq": self.seq, "mime": audio["mime"],
                           "data": audio["data"], "text": item, "engine": audio["engine"]})
                self.seq += 1
        self.emit({"type": "audio_end", "id": self.turn_id, "count": self.seq})
