"""
Eyes for JARVIS: pictures, the screen and the clipboard.

    Upload / paste / drop a picture, then ask about it ("what is this?", "read the text", "explain this chart").
    "What's on my screen?"      takes a screenshot (macOS) and describes it.
    "Summarise my clipboard"    reads the clipboard text and works on it.

Pictures are understood by a separate vision model (mlx-vlm), loaded only the first time it is needed so
JARVIS starts as fast as before. Nothing leaves the computer unless the cloud brain (Claude) is switched on.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import traceback

import cloud
import config
import lang
from models import Reply, Session, StreamCtx

IMAGE_DIR = config.DATA_DIR / "images"

SCREEN_RE = re.compile(
    r"\b(?:what(?:'s| is)?(?:\s+is)?\s+(?:on|in)\s+my\s+(?:screen|display)|(?:look at|read|describe|check|see|analy[sz]e|explain)\s+my\s+(?:screen|display)|"
    r"what\s+am\s+i\s+(?:looking at|working on)|screenshot\s+(?:and|then)\s+\w+|"
    # Hindi / Hinglish: "meri screen padho", "screen pe kya hai", "मेरी स्क्रीन देखो", "स्क्रीन पर क्या है"
    r"(?:meri|mera|मेरी|मेरा)\s+(?:screen|display|स्क्रीन)|"
    r"(?:screen|स्क्रीन)\s+(?:pe|par|mein|me|पे|पर|में)\s+(?:kya|क्या)|"
    r"(?:screen|स्क्रीन)\s+(?:padho|parho|dekho|batao|samjhao|पढ़ो|पढो|देखो|बताओ|समझाओ))", re.I)
CLIP_RE = re.compile(r"\b(?:my\s+)?clipboard\b|\bwhat\s+i\s+(?:just\s+)?copied\b|\bthe\s+(?:text|thing)\s+i\s+copied\b", re.I)
IMAGE_REF_RE = re.compile(
    r"\b(?:image|picture|photo|pic|screenshot|diagram|drawing|scan|receipt|invoice|meme|chart in)\b|"
    r"^(?:describe|read|transcribe|extract|translate|ocr|what(?:'s| is| are)|who(?:'s| is)|where(?:'s| is| was)|how many|"
    r"explain|identify|caption|can you (?:read|see)|is this|are these)\b.*\b(?:this|these|it|here|that|text|written|say|says|shown|visible)\b", re.I)
FORGET_RE = re.compile(r"^(?:forget|remove|clear|drop|close)\s+(?:the\s+|this\s+)?(?:image|picture|photo|pic)\W*$", re.I)

_IMAGE_SYSTEM = ("You are JARVIS, a precise and concise visual assistant. Answer from what is actually visible. "
                 "Quote any text exactly. If something is unclear, say so instead of guessing.")


# Tried in order until one loads. The first download is a few GB, so the first picture takes a while.
_CANDIDATES = [config.VISION_MODEL, "mlx-community/Qwen2.5-VL-3B-Instruct-4bit", "mlx-community/gemma-3-4b-it-4bit"]


def _is_cached(name: str) -> bool:
    """True when every weight file of the model is already in the Hugging Face cache (no download needed)."""
    try:
        from huggingface_hub import snapshot_download
        folder = Path(snapshot_download(name, local_files_only=True))
    except Exception:
        return False
    index = folder / "model.safetensors.index.json"
    try:
        if index.exists():
            shards = set(json.loads(index.read_text())["weight_map"].values())
            return all((folder / f).exists() for f in shards)
        return any(folder.glob("*.safetensors"))
    except Exception:
        return False


class _VisionModel:
    """Lazy mlx-vlm loader. One instance, guarded by a lock."""

    def __init__(self):
        self.model = self.processor = self.config = None
        self.name: str | None = None
        self.error: str | None = None
        self.missing_package = False
        self.downloading = False
        self.lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.model is not None

    @property
    def busy(self) -> bool:
        """True while a load is in progress (another thread holds the lock)."""
        return self.lock.locked()

    def warm_up(self):
        """Load (and if needed download) the vision model in the background at startup,
        so the first picture is answered at once instead of after a long wait."""
        if not config.VISION_PRELOAD or self.ready or self.error:
            return
        threading.Thread(target=self.ensure, name="vision-warmup", daemon=True).start()

    def ensure(self, say=None):
        if self.model is not None:
            return True
        if self.error:
            return False
        with self.lock:
            if self.model is not None:
                return True
            try:
                from mlx_vlm import load
                from mlx_vlm.utils import load_config
            except Exception as e:
                self.missing_package = isinstance(e, ImportError)
                self.error = f"{type(e).__name__}: {e}"
                traceback.print_exc()
                return False
            errors = []
            # Use a model that is already fully downloaded if there is one. Otherwise download only the
            # preferred model, never every fallback in turn (that looked like endless re-downloading).
            cached = [n for n in dict.fromkeys(_CANDIDATES) if _is_cached(n)]
            order = cached or [_CANDIDATES[0]]
            for name in order:
                try:
                    first_time = name not in cached
                    self.downloading = first_time
                    if say:
                        say(f"Downloading vision model {name.split('/')[-1]}, one time only" if first_time
                            else "Loading the vision model")
                    print(f"{'Downloading (one time)' if first_time else 'Loading'} vision model: {name}")
                    self.model, self.processor = load(name)
                    self.config = load_config(name)
                    self.name = name
                    self.downloading = False
                    print(f"Vision model ready: {name.split('/')[-1]}")
                    return True
                except Exception as e:
                    traceback.print_exc()
                    errors.append(f"{name.split('/')[-1]}: {type(e).__name__}: {e}")
            self.downloading = False
            self.error = " | ".join(errors)[:600]
            return False

    def ask(self, image_path: str, question: str, max_tokens: int = 700, on_delta=None, cancel=None) -> str:
        """Answer about a picture, streaming the words as they come (on_delta) so speech starts early."""
        from mlx_vlm.prompt_utils import apply_chat_template
        prompt = apply_chat_template(self.processor, self.config, f"{_IMAGE_SYSTEM}\n\n{question}", num_images=1)
        image = [_small_copy(image_path)]
        try:
            from mlx_vlm import stream_generate
        except ImportError:                          # very old mlx-vlm: no streaming
            from mlx_vlm import generate
            out = generate(self.model, self.processor, prompt, image=image, max_tokens=max_tokens, verbose=False)
            text = getattr(out, "text", out)
            text = _strip_think(str(text[0] if isinstance(text, tuple) else text))
            if on_delta and text:
                on_delta(text)
            return text
        parts, filt = [], _ThinkStrip()
        for chunk in stream_generate(self.model, self.processor, prompt, image=image, max_tokens=max_tokens):
            if cancel is not None and cancel.is_set():
                break
            piece = filt.feed(getattr(chunk, "text", chunk) or "")
            if piece:
                parts.append(piece)
                if on_delta:
                    on_delta(piece)
        return "".join(parts).strip()


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


class _ThinkStrip:
    """Drops <think>...</think> from a token stream."""

    def __init__(self):
        self.inside, self.buf = False, ""

    def feed(self, piece: str) -> str:
        self.buf += piece
        out = ""
        while self.buf:
            if self.inside:
                end = self.buf.find("</think>")
                if end < 0:
                    self.buf = self.buf[-8:]
                    return out
                self.buf, self.inside = self.buf[end + 8:], False
            else:
                start = self.buf.find("<think>")
                if start < 0:
                    keep = len(self.buf) - 7 if "<" in self.buf[-7:] else len(self.buf)
                    out, self.buf = out + self.buf[:keep], self.buf[keep:]
                    return out
                out, self.buf, self.inside = out + self.buf[:start], self.buf[start + 7:], True
        return out


def _small_copy(path: str) -> str:
    """The local model works on a smaller copy: far fewer image tokens, much faster, still sharp enough to read."""
    side = config.VISION_LOCAL_SIZE
    try:
        from PIL import Image
        img = Image.open(path)
        if max(img.size) <= side:
            return path
        small = Path(path).with_name(Path(path).stem + f"_{side}.png")
        if not small.exists():
            img = img.convert("RGB")
            img.thumbnail((side, side))
            img.save(small, "PNG")
        return str(small)
    except Exception:
        return path


_vision = _VisionModel()


def warm_up():
    _vision.warm_up()


def status() -> str:
    if _vision.ready:
        return f"ready ({_vision.name.split('/')[-1]})"
    if not config.VISION_PRELOAD:
        return "loads on the first picture"
    cached = any(_is_cached(n) for n in _CANDIDATES)
    return "loading in the background" if cached else \
        f"downloading {config.VISION_MODEL.split('/')[-1]} in the background (one time only)"


# ------------------------------------------------------------ files

def save_image(name: str, data: bytes) -> Path:
    """Store the picture, shrunk to a size the model handles quickly. Returns the saved path."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem)[:40] or "image"
    path = IMAGE_DIR / f"{stem}_{uuid.uuid4().hex[:6]}.png"
    try:
        import io
        from PIL import Image, ImageOps
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        img = img.convert("RGB")
        img.thumbnail((1568, 1568))
        img.save(path, "PNG")
    except Exception:
        path = path.with_suffix(Path(name).suffix.lower() or ".png")
        path.write_bytes(data)
    return path


def take_screenshot() -> Path | None:
    if sys.platform != "darwin":
        return None
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = IMAGE_DIR / f"screen_{int(time.time())}.png"
    try:
        subprocess.run(["screencapture", "-x", str(path)], check=True, timeout=10)
    except Exception:
        return None
    if not path.exists() or path.stat().st_size < 1000:
        return None
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB")
        img.thumbnail((1568, 1568))
        img.save(path, "PNG")
    except Exception:
        pass
    return path


_OCR_JXA = r"""
ObjC.import('Vision'); ObjC.import('Foundation');
function run(argv) {
  const url = $.NSURL.fileURLWithPath(argv[0]);
  const handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $({}));
  const req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;
  req.usesLanguageCorrection = true;
  handler.performRequestsError($([req]), null);
  const res = req.results; const out = [];
  for (let i = 0; i < res.count; i++) {
    const c = res.objectAtIndex(i).topCandidates(1);
    if (c.count) out.push(c.objectAtIndex(0).string.js);
  }
  return out.join("\n");
}
"""


def ocr_text(path: Path) -> str:
    """Read the text in a picture with macOS's built-in Vision framework. No install needed."""
    if sys.platform != "darwin":
        return ""
    script = IMAGE_DIR / "_ocr.js"
    try:
        script.write_text(_OCR_JXA)
        out = subprocess.run(["osascript", "-l", "JavaScript", str(script), str(path)],
                             capture_output=True, text=True, timeout=40)
        return out.stdout.strip()
    except Exception:
        return ""


def read_clipboard() -> str:
    try:
        if sys.platform == "darwin":
            return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5).stdout
    except Exception:
        pass
    return ""


# ------------------------------------------------------------ answering

_SUGGEST = ["Read all the text in it", "Explain it simply", "What should I notice?"]


def _done(session: Session, ctx, text: str, remember: str, streamed: bool) -> Reply:
    if ctx is not None and ctx.cancel.is_set():
        text = (text + "\n\n*(stopped)*").strip()
    session.history.append({"role": "user", "content": remember})
    session.history.append({"role": "assistant", "content": text})
    if not text:
        return Reply("I couldn't make out anything in that picture.")
    return Reply(text, streamed=streamed, suggestions=_SUGGEST)


def _answer(client, session: Session, ctx: StreamCtx | None, path: Path, question: str, remember: str) -> Reply:
    title = session.title
    on_delta = ctx.token if ctx else None
    cancel = ctx.cancel if ctx else None

    # Cloud brain on: Claude looks at the picture. Best quality, and no local model to wait for.
    if getattr(session, "cloud", False) and cloud.available():
        if ctx:
            ctx.status("Looking at the picture")
        try:
            text = cloud.stream_vision(_IMAGE_SYSTEM + "\n\n" + lang.instruction(session.lang, title),
                                       client._history_messages(session), question, path,
                                       cancel, on_delta or (lambda _t: None))
            return _done(session, ctx, text, remember, streamed=ctx is not None)
        except Exception as e:
            print(f"[vision] cloud failed, using the local model: {e}")

    # Local model still downloading: answer from the text in the picture right away instead of making you wait.
    if not _vision.ready and _vision.busy and _vision.downloading:
        return _ocr_fallback(client, session, ctx, path, question, remember, still_downloading=True)

    if not _vision.ensure(say=(ctx.status if ctx else None)):
        return _ocr_fallback(client, session, ctx, path, question, remember)
    if ctx:
        ctx.status("Looking at the picture")
    hindi = session.lang == "hi" and getattr(client, "llm_ready", False)
    try:
        with client.llm_lock:                       # one model on the GPU at a time
            if hindi:
                # the small vision model writes poor Hindi: it looks in English, then the chat model answers in Hindi
                seen = _vision.ask(str(path), "Describe this in detail and read out any text exactly. "
                                              f"The user asked (in Hindi): {question}", cancel=cancel)
            else:
                text = _vision.ask(str(path), question, on_delta=on_delta, cancel=cancel)
    except Exception as e:
        traceback.print_exc()
        return Reply(f"I couldn't read that picture, {title}: {e}")
    if hindi:
        if ctx:
            ctx.status("Answering in Hindi")
        return client.chat_stream(session, question, ctx, ground=False, remember_as=remember,
                                  user_content=f"{question}\n\nWhat the picture shows (seen by your vision system):\n"
                                               f"---\n{seen}\n---\nAnswer from this as if you saw it yourself.")
    return _done(session, ctx, text, remember, streamed=ctx is not None and bool(text))


def _ocr_fallback(client, session: Session, ctx, path: Path, question: str, remember: str,
                  still_downloading: bool = False) -> Reply:
    """No vision model: read the picture's text with macOS OCR and let the language model answer from it."""
    title = session.title
    if ctx:
        ctx.status("Reading the text in the picture")
    text = ocr_text(path)
    if still_downloading:
        fix = "My vision model is still downloading in the background (one time only), so I read the text for now."
    elif _vision.missing_package:
        fix = (f"To let me *see* pictures, run this in the same terminal that starts me:\n\n"
               f"```\n{sys.executable} -m pip install -U mlx-vlm Pillow\n```\nthen restart me.")
    else:
        fix = (f"The vision model failed to load:\n\n```\n{_vision.error}\n```\n"
               "Run `python check_vision.py` in the backend folder for the full report.")
    if text and client.llm_ready:
        reply = client.chat_stream(session, question, ctx, ground=False, remember_as=remember,
                                   user_content=f"{question}\n\nText found in the picture (by OCR):\n---\n{text[:5000]}\n---\n"
                                                "Answer from this text. You cannot see the picture itself.")
        reply.text = reply.text + f"\n\n> I read the text in your picture; I could not see the image itself. {fix}"
        return reply
    if text:
        return Reply(f"Here is the text in your picture, {title}:\n\n```\n{text[:4000]}\n```\n\n{fix}")
    return Reply(f"I can't see pictures yet, {title}. {fix}")


def handle_image_upload(client, session: Session, name: str, data: bytes, question: str = "",
                        ctx: StreamCtx | None = None) -> Reply:
    if len(data) > config.MAX_IMAGE_BYTES:
        return Reply(f"That picture is larger than {config.MAX_IMAGE_BYTES // (1024 * 1024)} MB, {session.title}.")
    path = save_image(name, data)
    session.image = str(path)
    q = question.strip() or "Describe this picture clearly. If it contains text, numbers, a chart or a table, read them out."
    return _answer(client, session, ctx, path, q, f"[picture: {name}] {question or 'describe it'}")


def handle(client, session: Session, text: str, ctx: StreamCtx) -> Reply | None:
    t = text.strip()
    if FORGET_RE.match(t.rstrip(".!")):
        session.image = None
        return Reply("Picture cleared.")

    if SCREEN_RE.search(t):
        path = take_screenshot()
        if path is None:
            return Reply("I couldn't capture the screen. On macOS, allow your terminal under System Settings, "
                         "Privacy & Security, Screen Recording, then try again.")
        session.image = str(path)
        return _answer(client, session, ctx, path,
                       t if len(t) > 25 and session.lang != "hi" else
                       ("मेरी screen पर क्या है? उसे पढ़कर समझाइए।" if session.lang == "hi" else
                        "Describe what is on this screen and what the user seems to be doing."),
                       f"[screen] {t}")

    if CLIP_RE.search(t) and re.search(r"\b(summari[sz]e|explain|translate|fix|rewrite|improve|proofread|reply|what|read|"
                                      r"review|shorten|clean|format|correct)\b", t, re.I):
        clip = read_clipboard().strip()
        if not clip:
            return Reply(f"Your clipboard looks empty, {session.title}, or holds something that isn't text.")
        task = re.sub(r"\b(my|the)?\s*clipboard\b", "the text below", t, flags=re.I)
        return client.chat_stream(session, t, ctx, extra="", ground=False,
                                  user_content=f"{task}\n\n---\n{clip[:6000]}\n---", remember_as=f"{t} [clipboard text]")

    if getattr(session, "image", None) and Path(session.image).exists() and IMAGE_REF_RE.search(t):
        return _answer(client, session, ctx, Path(session.image), t, t)
    return None
