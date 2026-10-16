"""
Language-model layer (MLX).

    generate()      one-shot answer (used by the advisor, summaries, private reasoning notes)
    stream_text()   token-by-token answer, cancellable, with hidden <think> blocks filtered out
"""

from __future__ import annotations

import re
import threading

import config
from models import _SPECIAL, clean_model_output


class ThinkFilter:
    """Turns a raw token stream into visible text: drops <think>…</think> and chat-template tokens."""

    def __init__(self):
        self.raw = ""
        self.sent = 0

    def feed(self, delta: str) -> str:
        self.raw += delta
        vis = re.sub(r"<think>.*?</think>", "", self.raw, flags=re.S)
        vis = re.sub(r"<think>.*$", "", vis, flags=re.S)
        for tok in _SPECIAL:
            vis = vis.replace(tok, "")
        hold = 0  # never emit half of a tag that is still arriving
        for tag in ("<think>",) + _SPECIAL:
            for k in range(len(tag) - 1, 0, -1):
                if vis.endswith(tag[:k]):
                    hold = max(hold, k)
                    break
        safe = vis[: len(vis) - hold] if hold else vis
        new = safe[self.sent:]
        self.sent = len(safe)
        return new


class LLM:
    """Mixin for Client. Call self._init_llm() from __init__."""

    def _init_llm(self, load_model: bool = True):
        self.model = None
        self.tokenizer = None
        self.llm_ready = False
        self.llm_lock = threading.Lock()
        if load_model:
            self._load_model()

    def _load_model(self):
        print(f"Loading AI model: {config.MODEL_NAME}   (detected {config.RAM_GB:.0f} GB memory)")
        try:
            from mlx_lm import load
            self.model, self.tokenizer = load(config.MODEL_NAME)
            self.llm_ready = True
            print("AI model loaded.\n")
        except Exception as e:
            print(f"⚠️  Could not load the language model: {e}")
            print("    Data analytics, voice, timers and tools still work; chat falls back to simple replies.\n")

    # ---------------------------------------------------------

    def _render_prompt(self, system: str, messages: list, thinking: bool | None = None) -> str:
        msgs = [{"role": "system", "content": system}] + messages
        try:
            return self._template(msgs, thinking)
        except Exception:
            folded = [dict(m) for m in messages]
            folded[0]["content"] = system + "\n\n" + folded[0]["content"]
            return self._template(folded, thinking)

    def _template(self, msgs: list, thinking: bool | None = None) -> str:
        # Qwen3 accepts enable_thinking; older templates ignore or reject it, so fall back cleanly.
        try:
            return self.tokenizer.apply_chat_template(
                msgs, tokenize=False, add_generation_prompt=True,
                enable_thinking=config.THINKING if thinking is None else thinking)
        except TypeError:
            return self.tokenizer.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def _sampler(self):
        try:
            from mlx_lm.sample_utils import make_sampler
            try:
                return make_sampler(temp=config.TEMPERATURE, top_p=config.TOP_P, top_k=config.TOP_K)
            except TypeError:                         # older mlx-lm without top_k
                return make_sampler(temp=config.TEMPERATURE, top_p=config.TOP_P)
        except Exception:
            return None

    def _sampling_kwargs(self) -> dict:
        """Sampler plus a gentle repetition penalty (so he does not say the same sentence twice)."""
        kw = {}
        sampler = self._sampler()
        if sampler is not None:
            kw["sampler"] = sampler
        if config.REPETITION_PENALTY and config.REPETITION_PENALTY != 1.0:
            try:
                from mlx_lm.sample_utils import make_logits_processors
                kw["logits_processors"] = make_logits_processors(
                    repetition_penalty=config.REPETITION_PENALTY, repetition_context_size=64)
            except Exception:
                pass
        return kw

    def _generate_raw(self, prompt: str, max_tokens: int) -> str:
        from mlx_lm import generate

        kwargs = {"prompt": prompt, "max_tokens": max_tokens, **self._sampling_kwargs()}

        with self.llm_lock:
            try:
                out = generate(self.model, self.tokenizer, **kwargs)
            except TypeError:
                kwargs.pop("sampler", None)
                kwargs.pop("logits_processors", None)
                out = generate(self.model, self.tokenizer, **kwargs)

        if isinstance(out, list):
            out = "".join(out)
        return clean_model_output(out)

    def _stream_raw(self, prompt: str, max_tokens: int, cancel: threading.Event):
        """Yield text deltas. Stops promptly when `cancel` is set."""
        from mlx_lm import stream_generate

        kwargs = {"prompt": prompt, "max_tokens": max_tokens, **self._sampling_kwargs()}

        def run(kw):
            for resp in stream_generate(self.model, self.tokenizer, **kw):
                yield getattr(resp, "text", resp)

        with self.llm_lock:
            it = run(kwargs)
            try:
                first = next(it)
            except StopIteration:
                return
            except TypeError:  # older mlx-lm without the sampler argument
                kwargs.pop("sampler", None)
                kwargs.pop("logits_processors", None)
                it = run(kwargs)
                try:
                    first = next(it)
                except StopIteration:
                    return
            yield first
            for delta in it:
                if cancel.is_set():
                    break
                yield delta

    # ---------------------------------------------------------

    def generate(self, system: str, user: str, max_tokens: int) -> str:
        """One-shot generation used by the advisor and by internal helpers."""
        if not self.llm_ready:
            raise RuntimeError("language model not loaded")
        prompt = self._render_prompt(system, [{"role": "user", "content": user}])
        return self._generate_raw(prompt, max_tokens)

    def stream_text(self, system: str, messages: list, max_tokens: int, cancel: threading.Event, on_delta,
                    thinking: bool | None = None) -> str:
        prompt = self._render_prompt(system, messages, thinking)
        filt, parts = ThinkFilter(), []
        for delta in self._stream_raw(prompt, max_tokens, cancel):
            out = filt.feed(delta)
            if out:
                parts.append(out)
                on_delta(out)
        return clean_model_output("".join(parts))
