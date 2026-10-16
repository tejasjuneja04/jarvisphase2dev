"""
JARVIS cloud brain: Claude (Anthropic API, paid), Groq (free tier) or Gemini (Google, free tier).

Optional. When ANTHROPIC_API_KEY, GROQ_API_KEY or GEMINI_API_KEY is set (backend/.env) and the "Cloud brain" switch is on,
normal conversation and pictures go to the cloud instead of the local model. Everything else (commands,
files, data analysis, charts, voice) still runs on the Mac.

Privacy: in cloud mode your messages and the conversation history are sent to Anthropic or Google.
Cost: Claude is paid per use. Gemini has a free tier with daily limits.
Turn the switch off to go back to fully local and offline.
"""

from __future__ import annotations

import base64
from pathlib import Path

import config

_client = None
_error = ""

FALLBACK_BETA = "server-side-fallback-2026-07-01"


def _get_client():
    global _client, _error
    if _client is not None:
        return _client
    if not config.ANTHROPIC_API_KEY:
        _error = "ANTHROPIC_API_KEY is not set in backend/.env"
        return None
    try:
        import anthropic
    except ImportError:
        _error = "the anthropic package is not installed (pip install anthropic)"
        return None
    _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def provider() -> str:
    """"claude", "groq", "gemini" or "" (no cloud brain set up)."""
    want = config.CLOUD_PROVIDER
    if want in ("auto", "claude") and _get_client() is not None:
        return "claude"
    if want in ("auto", "groq") and config.GROQ_API_KEY:
        return "groq"
    if want in ("auto", "gemini") and config.GEMINI_API_KEY:
        return "gemini"
    return ""


def available() -> bool:
    return provider() != ""


def name() -> str:
    """Short name for the UI: Claude or Gemini."""
    return {"claude": "Claude", "groq": "Groq", "gemini": "Gemini"}.get(provider(), "Claude")   # terminal only, never shown in the app


def model_name() -> str:
    return {"claude": config.CLOUD_MODEL, "groq": config.GROQ_MODEL, "gemini": config.GEMINI_MODEL}.get(provider(), "")


def status() -> str:
    """One line for the startup banner."""
    if available():
        return f"ready ({model_name()})"
    return "off: add GROQ_API_KEY or GEMINI_API_KEY (both free) or ANTHROPIC_API_KEY to backend/.env"


def _clean(messages: list) -> list:
    """The API wants user/assistant turns that alternate and start with the user."""
    out: list = []
    for m in messages:
        role, content = m.get("role"), (m.get("content") or "").strip()
        if role not in ("user", "assistant") or not content:
            continue
        if out and out[-1]["role"] == role:
            out[-1]["content"] += "\n\n" + content
        else:
            out.append({"role": role, "content": content})
    while out and out[0]["role"] != "user":
        out.pop(0)
    return out


def _consume(stream, cancel, on_delta) -> tuple[str, str]:
    parts = []
    for delta in stream.text_stream:
        if cancel is not None and cancel.is_set():
            break
        parts.append(delta)
        on_delta(delta)
    stop = ""
    if not (cancel is not None and cancel.is_set()):
        stop = getattr(stream.get_final_message(), "stop_reason", "") or ""
    return "".join(parts), stop


_MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp"}


def stream_vision(system: str, history: list, question: str, image_path, cancel, on_delta) -> str:
    """Claude looks at a picture and answers, streaming. The picture is sent with the question only."""
    path = Path(image_path)
    media = _MEDIA.get(path.suffix.lower())
    if media is None:
        raise ValueError(f"unsupported picture type {path.suffix}")
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    content = [
        {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
        {"type": "text", "text": question},
    ]
    messages = _clean(history)
    if messages and messages[-1]["role"] == "user":
        messages.append({"role": "assistant", "content": "Understood."})
    if provider() == "groq":
        raise RuntimeError("the Groq brain does not look at pictures")    # the local vision model answers instead
    if provider() == "gemini":
        parts = [{"inline_data": {"mime_type": media, "data": data}}, {"text": question}]
        return _gemini_stream(system, messages + [{"role": "user", "parts": parts}], config.CLOUD_MAX_TOKENS,
                              cancel, on_delta)
    messages.append({"role": "user", "content": content})
    return stream_text(system, messages, config.CLOUD_MAX_TOKENS, cancel, on_delta, prepared=True)


def stream_text(system: str, messages: list, max_tokens: int, cancel, on_delta, deep: bool = False, prepared: bool = False) -> str:
    """Stream a Claude reply, calling on_delta(text) for each piece. Returns the full text.

    Raises on connection or API errors so the caller can fall back to the local model.
    """
    if provider() == "groq":
        return _groq_stream(system, messages if prepared else _clean(messages), max_tokens, cancel, on_delta, deep)
    if provider() == "gemini":
        return _gemini_stream(system, messages if prepared else _clean(messages), max_tokens, cancel, on_delta, deep)
    client = _get_client()
    if client is None:
        raise RuntimeError(f"cloud brain unavailable: {_error}")

    effort = "high" if deep else config.CLOUD_EFFORT
    kwargs = dict(model=config.CLOUD_MODEL, max_tokens=max_tokens, system=system, messages=messages if prepared else _clean(messages))

    # Server-side refusal fallback: if Claude declines, the API retries on Anthropic's recommended
    # fallback model inside the same call. Older SDKs (Python 3.9 resolves anthropic 0.x) may not
    # know these parameters, so retry by sending the same fields as raw body/header extras.
    try:
        with client.beta.messages.stream(**kwargs, output_config={"effort": effort},
                                         betas=[FALLBACK_BETA], fallbacks="default") as stream:
            text, stop = _consume(stream, cancel, on_delta)
    except TypeError:
        with client.messages.stream(**kwargs, extra_headers={"anthropic-beta": FALLBACK_BETA},
                                    extra_body={"output_config": {"effort": effort}, "fallbacks": "default"}) as stream:
            text, stop = _consume(stream, cancel, on_delta)

    if stop == "refusal":
        note = "\n\n*(I'd rather not answer that one.)*"
        on_delta(note)
        text += note
    elif stop == "max_tokens":
        note = " …"
        on_delta(note)
        text += note
    return text


# ------------------------------------------------------------------ Gemini (Google, free tier)
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent?alt=sse"


def _gemini_body(system: str, messages: list, max_tokens: int, deep: bool, thinking: dict | None) -> dict:
    contents = []
    for m in messages:
        role = "model" if m.get("role") == "assistant" else "user"
        parts = m.get("parts") or [{"text": m.get("content") or ""}]
        contents.append({"role": role, "parts": parts})
    gen = {"maxOutputTokens": max_tokens, "temperature": 0.8}
    if thinking:
        gen["thinkingConfig"] = thinking                   # chat stays fast; deep mode lets it think more
    return {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": gen,
        "safetySettings": [{"category": c, "threshold": "BLOCK_ONLY_HIGH"} for c in (
            "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
            "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")],
    }


def _gemini_stream(system: str, messages: list, max_tokens: int, cancel, on_delta, deep: bool = False) -> str:
    """Stream a Gemini reply over plain HTTPS (no extra package), calling on_delta for each piece."""
    import json
    import urllib.error
    import urllib.request

    url = GEMINI_URL.format(model=config.GEMINI_MODEL)
    headers = {"Content-Type": "application/json", "x-goog-api-key": config.GEMINI_API_KEY}

    def open_stream(thinking: dict | None):
        body = json.dumps(_gemini_body(system, messages, max_tokens, deep, thinking)).encode("utf-8")
        return urllib.request.urlopen(urllib.request.Request(url, data=body, headers=headers, method="POST"), timeout=120)

    # Gemini 3 takes a thinking level, Gemini 2.5 a thinking budget. Try the fast settings first and fall
    # back step by step if this model rejects one, so a slow "thinking" pass never stalls a quick chat.
    if config.GEMINI_MODEL.startswith("gemini-2"):
        tries = [None] if deep else [{"thinkingBudget": 0}, None]
    else:
        tries = [{"thinkingLevel": "high"}, None] if deep else [{"thinkingLevel": "minimal"}, {"thinkingLevel": "low"}, None]
    resp = None
    for i, thinking in enumerate(tries):
        try:
            resp = open_stream(thinking)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and i < len(tries) - 1:     # this model rejects that thinking setting: try the next
                continue
            if e.code == 429:
                raise RuntimeError("the free daily limit of the cloud brain is used up for now") from None
            raise RuntimeError(f"Gemini error {e.code}: {detail}") from None

    parts, finish = [], ""
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                chunk = json.loads(line[5:].strip())
            except ValueError:
                continue
            for cand in chunk.get("candidates") or []:
                for part in (cand.get("content") or {}).get("parts") or []:
                    if part.get("thought"):
                        continue                      # private reasoning, not part of the answer
                    t = part.get("text") or ""
                    if t:
                        parts.append(t)
                        on_delta(t)
                finish = cand.get("finishReason") or finish
    text = "".join(parts)
    if not text and finish in ("SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST"):
        note = "*(I'd rather not answer that one.)*"
        on_delta(note)
        text = note
    elif finish == "MAX_TOKENS":
        on_delta(" …")
        text += " …"
    return text


# ------------------------------------------------------------------ Groq (free tier, very fast)
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _groq_stream(system: str, messages: list, max_tokens: int, cancel, on_delta, deep: bool = False) -> str:
    """Stream a Groq reply over plain HTTPS (OpenAI-style API, no extra package), calling on_delta for each piece."""
    import json
    import urllib.error
    import urllib.request

    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {config.GROQ_API_KEY}",
               "User-Agent": "jarvis"}
    base = {"model": config.GROQ_MODEL, "stream": True, "temperature": 0.8, "max_completion_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}] + messages}

    def open_stream(extra: dict):
        body = json.dumps({**base, **extra}).encode("utf-8")
        return urllib.request.urlopen(urllib.request.Request(GROQ_URL, data=body, headers=headers, method="POST"), timeout=120)

    # reasoning models think briefly in chat and properly in deep mode; other models reject the setting
    tries = [{"reasoning_effort": "high" if deep else "low", "include_reasoning": False}, {}]
    resp = None
    for i, extra in enumerate(tries):
        try:
            resp = open_stream(extra)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 400 and i < len(tries) - 1:
                continue
            if e.code == 429:
                raise RuntimeError("the free limit of the cloud brain is used up for now") from None
            raise RuntimeError(f"Groq error {e.code}: {detail}") from None

    parts, finish = [], ""
    with resp:
        for raw in resp:
            if cancel is not None and cancel.is_set():
                break
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            for choice in chunk.get("choices") or []:
                t = (choice.get("delta") or {}).get("content") or ""
                if t:
                    parts.append(t)
                    on_delta(t)
                finish = choice.get("finish_reason") or finish
    text = "".join(parts)
    if not text and finish == "content_filter":
        note = "*(I'd rather not answer that one.)*"
        on_delta(note)
        text = note
    elif finish == "length":
        on_delta(" …")
        text += " …"
    return text
