"""Shared data structures: what a reply looks like, what a browser session holds."""

from __future__ import annotations

import base64
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

import analytics as A
import config
import reports as R


@dataclass
class Reply:
    text: str
    speech: str | None = None          # short line to speak; None -> derived from text when short
    charts: list = field(default_factory=list)
    files: list = field(default_factory=list)
    dataset: dict | None = None
    streamed: bool = False             # text already streamed to the UI token by token (and spoken)
    sources: list = field(default_factory=list)   # [{"title", "url"}]
    docs: list | None = None           # current document list, when it changed
    events: list = field(default_factory=list)    # extra UI events, e.g. timers
    suggestions: list = field(default_factory=list)   # follow-up chips the UI can send with one click

    def __post_init__(self):
        # reports (they contain a rule or table) get long prose lines wrapped; chat text is left alone
        if "─" in self.text:
            self.text = R.wrap_report(self.text)

    def spoken(self) -> str | None:
        """What should be read aloud after the reply is sent (streamed replies were already spoken)."""
        if self.streamed:
            return None
        if self.speech:
            return self.speech
        t = self.text or ""
        if t and "─" not in t and len(t) <= 260:
            return t
        return None

    def payload(self, mode: str) -> dict:
        return {
            "type": "reply",
            "text": self.text,
            "speech": self.speech,
            "charts": self.charts,
            "files": self.files,
            "dataset": self.dataset,
            "streamed": self.streamed,
            "sources": self.sources,
            "docs": self.docs,
            "suggestions": self.suggestions,
            "mode": mode,
        }


class Session:
    """State for one browser connection."""

    def __init__(self):
        self.dataset: A.Dataset | None = None
        self.original: A.Dataset | None = None
        self.docs = None                       # docs.DocStore, created on first upload
        self.history: list = []
        self.summary = ""                      # rolling summary of older turns
        self.mode = "formal"
        self.title = config.USER_TITLE
        self.pending = None
        self.lang = "en"                       # language of the user's last message: en | hi
        self.lang_pref = "auto"                # auto | en | hi
        self.last_chart: dict | None = None    # spec of the last editable chart
        self.image: str | None = None          # path of the picture the user is currently asking about
        self.deep = False                      # always reason step by step first
        self.web = config.WEB_ENABLED
        self.cloud = config.CLOUD_DEFAULT          # use Claude (Anthropic API) for conversation
        self.voice = True
        self.cancel = threading.Event()
        self.sink = None                       # current speech sink, so "stop" can silence it
        self.turn = 0
        self.epoch = 0                         # bumped on every chat switch, so a late turn cannot leak into the new chat
        self.lock = threading.Lock()


class StreamCtx:
    """What the brain needs to stream a reply: where to send events, how to stop, where to speak."""

    def __init__(self, emit, cancel: threading.Event, sink=None, turn_id: str = "t0"):
        self.emit = emit
        self.cancel = cancel
        self.sink = sink
        self.turn_id = turn_id
        self.started = False

    def start(self):
        if not self.started:
            self.started = True
            self.emit({"type": "stream_start", "id": self.turn_id})

    def token(self, text: str):
        self.start()
        self.emit({"type": "token", "id": self.turn_id, "text": text})
        if self.sink is not None:
            self.sink.feed(text)

    def status(self, text: str):
        self.emit({"type": "status", "text": text})


def null_ctx() -> StreamCtx:
    """A context that goes nowhere (tests, plain get_response)."""
    return StreamCtx(lambda _p: None, threading.Event(), None)


def _file_payload(path: Path, mime: str, limit: int = 12 * 1024 * 1024):
    try:
        raw = path.read_bytes()
    except Exception:
        return None
    if len(raw) > limit:
        return None
    return {"name": path.name, "mime": mime, "data": base64.b64encode(raw).decode("ascii")}


_SPECIAL = ("<|assistant|>", "<|user|>", "<|system|>", "<|end|>", "<|endoftext|>", "<|im_end|>", "<|im_start|>")


def clean_model_output(text) -> str:
    """Strip chat-template tokens and hidden reasoning; KEEP markdown (the UI renders it)."""
    if not isinstance(text, str):
        text = str(text)
    for token in _SPECIAL:
        text = text.replace(token, "")
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"<think>.*$", "", text, flags=re.S)
    text = re.sub(r"^(Assistant|JARVIS|J\.A\.R\.V\.I\.S\.?)\s*:\s*", "", text.strip(), flags=re.I)
    return text.strip()
