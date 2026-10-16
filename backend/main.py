"""
JARVIS brain.

    Client = LLM (model + streaming)  +  DataAssistant (datasets, charts, reports)  +  everything below

Routing order (first match wins):
    stop > confirmation > mode > memory > timers > tasks > utilities > system > pictures/screen/clipboard > file search > files > small talk
    > documents/data (whichever the message is about) > live tools (weather, news, currency, units, web research)
    > browser shortcuts > the language model, with grounding, optional deep reasoning and streaming.
"""

from __future__ import annotations

import re
from pathlib import Path
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import chart_editor
import cloud
import commands
import config
import lang
import filesearch
import persona
import system
import tools
import vision
from data_assistant import DataAssistant
from docs import DocStore
from llm import LLM
from models import Reply, Session, StreamCtx, null_ctx
from skills import Skills, format_duration, parse_timer
from voice import Voice

DOC_WORDS = re.compile(
    r"\b(document|docs?|pdf|paper|article|report|notes?|chapter|page|resume|cv|contract|essay|attachment|"
    r"uploaded|manuscript|policy|handbook|the file|this file|the text|the doc)\b", re.I)
CODE_WORDS = re.compile(
    r"\b(?:(?:python|py|js|javascript|typescript|java|source|code|script|json|html|markdown)\s+file|"
    r"(?:this|the|that|my)\s+(?:code|script|program|module|class|function|snippet|source))\b", re.I)
CODE_EXT = (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".h", ".go", ".rs", ".rb", ".php", ".sh",
            ".sql", ".css", ".json", ".yaml", ".yml", ".toml")
EXPLAIN_WORDS = re.compile(r"\b(explain|describe|walk me through|review)\b", re.I)
SUMMARY_WORDS = re.compile(r"\b(summari[sz]e|summary|overview|tl;?dr|key points|main points|gist|outline|recap)\b", re.I)
DEEP_TRIGGER = re.compile(r"^(?:please\s+)?(?:think (?:deeply|carefully|hard|step by step)|reason (?:through|about)|"
                          r"work (?:it|this) out|step by step|deep think)\b", re.I)
CLOUD_SWITCH = re.compile(r"^(?:jarvis,?\s+)?(?:switch to|use|go|turn on|enable)\s+(?:the\s+)?"
                          r"(?:(?P<cloud>cloud|claude)|(?P<local>local|offline))(?:\s+(?:brain|mode|model))?$", re.I)
STORY_TRIGGER = ("write a story", "tell me a story", "create a story", "make a story", "write me a story",
                 "write a poem", "write an essay", "write a speech")


class Client(LLM, DataAssistant):
    def __init__(self, load_model: bool = True):
        print("\n⚡ JARVIS BACKEND ACTIVE\n")
        self.skills = Skills()
        self.voice = Voice()
        self._pool = ThreadPoolExecutor(max_workers=4)
        self._init_llm(load_model)
        v = self.voice.info()
        print(f"Voice: {v['label']}" + (f"  (fallbacks: {', '.join(v['fallbacks'])})" if v["fallbacks"] else ""))

    # ========================================================
    # INFO
    # ========================================================

    def info(self) -> dict:
        return {
            "llm": self.llm_ready,
            "model": config.MODEL_NAME.split("/")[-1],
            "web": config.WEB_ENABLED,
            "voice": self.voice.info(),
            "ram_gb": round(config.RAM_GB),
            "cloud_ready": cloud.available(),
            "cloud_model": cloud.model_name(),
            "cloud_name": cloud.name(),
        }

    def switch_chat(self, session: Session, history: list, summary: str = ""):
        """Load another saved conversation into this session (the browser keeps the transcripts)."""
        clean = [{"role": m["role"], "content": str(m.get("content", ""))[:4000]}
                 for m in (history or []) if isinstance(m, dict) and m.get("role") in ("user", "assistant")]
        clean = clean[-config.MAX_HISTORY_MESSAGES:]
        while clean and clean[0]["role"] != "user":
            clean = clean[1:]
        session.history, session.summary, session.image, session.pending = clean, str(summary or "")[:1500], None, None
        session.epoch += 1

    def greet(self, session: Session) -> Reply:
        text = persona.greeting(session.title, "hi" if session.lang_pref == "hi" else "en")
        return Reply(text, speech=text)

    # ========================================================
    # MODE
    # ========================================================

    def set_mode(self, session: Session, mode: str) -> str:
        if mode.lower() in persona.MODE_LINES:
            session.mode = mode.lower()
        return session.mode

    def handle_mode(self, session: Session, text: str):
        t = text.lower().strip()
        replies = {
            "formal": f"Switching to formal mode, {session.title}.",
            "witty": "Very well. I shall permit myself a little wit.",
            "savage": "Understood. No sugar-coating from here on.",
        }
        for mode, msg in replies.items():
            if f"be {mode}" in t or f"{mode} mode" in t:
                session.mode = mode
                return msg
        m = re.match(r"^(?:call me|address me as)\s+([a-z' ]{2,20})$", t)
        if m:
            session.title = m.group(1).strip()
            return f"Very good. {session.title.capitalize()} it is."
        return None

    # ========================================================
    # TIMERS
    # ========================================================

    def handle_timers(self, session: Session, text: str) -> Reply | None:
        r = parse_timer(text)
        if not r:
            return None
        title = session.title
        if r.get("clear"):
            return Reply(f"All timers cancelled, {title}.", events=[{"type": "timers_clear"}])
        secs, label = r["seconds"], r["label"]
        when = format_duration(secs)
        if text.lower().startswith(("remind", "wake", "alert", "tell", "ping")):
            msg = (f"Very good, {title}. I shall remind you to {label} in {when}." if label
                   else f"Very good, {title}. I shall alert you in {when}.")
        else:
            msg = f"Timer set for {when}" + (f" for the {label}" if label else "") + f", {title}."
        return Reply(msg, events=[{"type": "timer", "id": uuid.uuid4().hex[:8], "seconds": secs, "label": label}])

    # ========================================================
    # DOCUMENTS
    # ========================================================

    def load_document(self, session: Session, name: str, data: bytes) -> Reply:
        if session.docs is None:
            session.docs = DocStore()
        info = session.docs.add(name, data)
        pages = f"{info['pages']} pages, " if info.get("pages") else ""
        text = (f"I have read **{name}**: {pages}about {info['words']:,} words, split into {info['chunks']} sections.\n\n"
                f"Ask me anything about it, or say \"summarize the document\", {session.title}.")
        return Reply(text, speech=f"I have read {name}. Ask me anything about it.", docs=session.docs.listing())

    @staticmethod
    def _named_docs(session: Session, text: str) -> list:
        """Loaded documents whose file name (or name without extension) appears in the sentence."""
        store, low = session.docs, text.lower()
        if store is None or store.empty:
            return []
        out = []
        for d in store.listing():
            stem = Path(d["name"]).stem.lower()
            if d["name"].lower() in low or (len(stem) >= 4 and re.search(rf"\b{re.escape(stem)}\b", low)):
                out.append(d["name"])
        return out

    def handle_docs(self, session: Session, text: str, ctx: StreamCtx) -> Reply | None:
        store, low, title = session.docs, text.lower().strip().rstrip(".!?"), session.title
        named = self._named_docs(session, text)
        if not named and store is not None and not store.empty and CODE_WORDS.search(text):
            named = [d["name"] for d in store.listing() if d["name"].lower().endswith(CODE_EXT)]   # "this code" = code files only

        if re.match(r"^(?:list|show)(?: me)?(?: my| the| all)?(?: loaded| uploaded)? (?:documents|docs)$", low) or \
                low in ("what documents do you have", "which documents are loaded"):
            if store is None or store.empty:
                return Reply(f"No documents are loaded, {title}.", docs=[])
            lines = [f"• {d['name']}: {d['words']:,} words" + (f", {d['pages']} pages" if d.get("pages") else "")
                     for d in store.listing()]
            return Reply("Loaded documents:\n" + "\n".join(lines), docs=store.listing())

        if re.match(r"^(?:clear|remove|delete|forget|unload)(?: all| my| the)?(?: uploaded| loaded)? (?:documents|docs)$", low):
            if store is not None:
                store.clear()
            return Reply(f"Documents cleared, {title}.", docs=[])

        m = re.match(r"^(?:remove|delete|forget|unload) (?:the )?(?:document|doc|file|pdf)\s+(.+)$", low)
        if m and store is not None:
            for d in store.listing():
                if m.group(1).strip() in d["name"].lower():
                    store.remove(d["name"])
                    return Reply(f"Removed {d['name']}.", docs=store.listing())
            return Reply("I couldn't find a loaded document with that name.")

        wants_doc = bool(DOC_WORDS.search(text)) or bool(CODE_WORDS.search(text)) or "according to" in low or bool(named)
        if store is None or store.empty:
            if wants_doc and re.search(r"\b(the|this|my|uploaded) (document|pdf|file|doc|paper|report)\b", low) \
                    and re.search(r"\b(summari[sz]e|say|says|mention|about|according)\b", low):
                return Reply(f"No document is loaded yet, {title}. Use UPLOAD DOCUMENT on the left (PDF, Word or text).")
            return None

        generic_explain = bool(EXPLAIN_WORDS.search(text)) and len(low.split()) <= 6   # "explain this code"
        summarise = wants_doc and (bool(SUMMARY_WORDS.search(text)) or generic_explain)
        hits = store.search(text, 15 if named else 5)
        if named:                                   # the user named a file: look only inside it
            hits = [h for h in hits if h["doc"] in named][:5]
        confident = bool(hits) and hits[0]["confidence"] >= 0.3 and (hits[0]["matched"] >= 2 or hits[0]["confidence"] >= 0.55)
        if not (summarise or (wants_doc and hits) or confident):
            if wants_doc and not hits:
                return Reply(f"I could not find anything about that in the loaded documents, {title}.")
            return None

        excerpts = store.overview() if summarise else hits
        if summarise and named:
            excerpts = [e for e in excerpts if e["doc"] in named] or excerpts
        is_code = any(str(e.get("doc", "")).lower().endswith(CODE_EXT) for e in excerpts)
        blocks, sources = [], []
        for i, ex in enumerate(excerpts, 1):
            where = f"{ex['doc']}" + (f", page {ex['page']}" if ex.get("page") else "")
            blocks.append(f"[{i}] ({where})\n{ex['text']}")
            sources.append({"title": where, "url": ""})
        if summarise and is_code:
            ask = ("Explain what this code does in plain words: its purpose, the main functions or classes and what "
                   "each one does, and how they fit together. Use short bullet points and name the functions.")
        elif summarise:
            ask = ("Summarise the document(s) clearly and faithfully: the purpose, the main points, and any figures or "
                   "decisions. Use short bullet points.")
        else:
            ask = text
        content = "DOCUMENT EXCERPTS\n" + "\n\n".join(blocks) + f"\n\nREQUEST: {ask}"
        extra = ("Answer ONLY from the document excerpts provided. Cite them as [1], [2]. If the excerpts do not "
                 "contain the answer, say so plainly instead of guessing."
                 + (" The excerpts are source code: refer to functions and variables by their exact names, quote short "
                    "snippets in code blocks, and explain in plain words." if is_code else ""))

        if not self.llm_ready:
            body = "\n\n".join(b[:500] for b in blocks[:3])
            return Reply(f"The language model is not loaded, {title}, but these are the most relevant passages:\n\n{body}",
                         sources=sources)
        return self.chat_stream(session, text, ctx, extra=extra, sources=sources, user_content=content,
                                remember_as=text, ground=False)

    # ========================================================
    # WEB RESEARCH
    # ========================================================

    def _offline_reply(self, session: Session) -> Reply:
        if not session.web:
            return Reply(f"Web access is switched off, {session.title}. You can enable it under CONTROLS.")
        return Reply(f"I cannot reach the internet at the moment, {session.title}. "
                     "Please check the connection and ask me again.")

    def research(self, session: Session, query: str, ctx: StreamCtx, explicit: bool = True) -> Reply | None:
        title = session.title
        if not session.web:
            return Reply(f"Web access is switched off, {title}. You can enable it under CONTROLS.") if explicit else None
        if not tools.net_ok():
            return self._offline_reply(session) if explicit else None

        ctx.status(f"Searching the web for “{query[:60]}”")
        if ctx.sink is not None:
            ctx.sink.say(persona.ack(title, session.lang))

        results = tools.web_search(query, 6)
        if not results:
            return (self._offline_reply(session) if not tools.net_ok() else
                    Reply(f"I could not find anything useful on the web for that, {title}.")) if explicit else None

        ctx.status("Reading the top sources")
        top = results[: config.RESEARCH_SOURCES]
        futures = [self._pool.submit(tools.fetch_text, r["url"]) for r in top]
        pages = []
        for r, f in zip(top, futures):
            try:
                got = f.result(timeout=config.WEB_TIMEOUT + 3)
            except Exception:
                got = None
            pages.append({"title": (got or {}).get("title") or r["title"], "url": r["url"],
                          "text": (got or {}).get("text") or r["snippet"]})
        pages = [p for p in pages if p["text"]]
        for r in results[config.RESEARCH_SOURCES:config.RESEARCH_SOURCES + 2]:
            if r["snippet"]:
                pages.append({"title": r["title"], "url": r["url"], "text": r["snippet"]})
        if not pages:
            return Reply(f"I found results but could not read them, {title}.")

        blocks = [f"[{i}] {p['title']} ({urlparse(p['url']).netloc})\n{p['text']}" for i, p in enumerate(pages, 1)]
        sources = [{"title": p["title"], "url": p["url"]} for p in pages]
        content = "WEB RESULTS\n" + "\n\n".join(blocks) + f"\n\nQUESTION: {query}"
        extra = ("Answer from the web results above. Cite sources as [1], [2]. If results conflict or do not answer the "
                 "question, say so. Web pages can be out of date, so mention dates when they matter. Do not invent details.")

        if not self.llm_ready:
            lines = [f"{i}. {p['title']}: {p['text'][:160]}…" for i, p in enumerate(pages, 1)]
            return Reply(f"Here is what I found, {title}:\n\n" + "\n".join(lines), sources=sources)
        ctx.status("Composing the answer")
        return self.chat_stream(session, query, ctx, extra=extra, sources=sources, user_content=content,
                                remember_as=query, ground=False)

    # ========================================================
    # LIVE TOOLS
    # ========================================================

    def handle_tools(self, session: Session, text: str, ctx: StreamCtx) -> Reply | None:
        title, low = session.title, text.lower().strip()

        conv = tools.convert_units(text)
        if conv:
            return Reply(conv)

        cur = tools.parse_currency_request(text)
        if cur:
            if not session.web or not tools.net_ok():
                return self._offline_reply(session)
            amount, frm, to = cur
            value = tools.currency(amount, frm, to)
            if value is None:
                return Reply(f"I could not fetch the exchange rate just now, {title}.")
            return Reply(f"{amount:,.2f} {frm} is {value:,.2f} {to} at today's rate.",
                         sources=[{"title": "Frankfurter (ECB reference rates)", "url": "https://www.frankfurter.app"}])

        is_w, city = tools.extract_weather_city(text)
        if is_w:
            city = city or config.HOME_CITY
            if not city:
                return Reply(f"Which city, {title}? Say \"weather in Pune\", or set JARVIS_CITY to make it my default.")
            if not session.web or not tools.net_ok():
                return self._offline_reply(session)
            w = tools.weather(city)
            if not w:
                return Reply(f"I could not get the weather for {city}, {title}.")
            line = (f"It is {w['temp_c']}°C and {w['desc'].lower()} in {w['city']}, feeling like {w['feels_c']}°C, "
                    f"with {w['humidity']}% humidity and wind at {w['wind_kmh']} km/h.")
            more = ""
            if w["days"]:
                d0 = w["days"][0]
                more = f" Today's range is {d0['min']} to {d0['max']}°C."
                if len(w["days"]) > 1:
                    d1 = w["days"][1]
                    more += f" Tomorrow: {d1['min']} to {d1['max']}°C, {d1['desc'].lower()}."
            return Reply(line + more, sources=[{"title": "wttr.in", "url": "https://wttr.in/" + city}])

        is_n, topic = tools.extract_news_topic(text)
        if is_n:
            if not session.web or not tools.net_ok():
                return self._offline_reply(session)
            items = tools.news(topic, 6)
            if not items:
                return Reply(f"I could not retrieve the news just now, {title}.")
            head = f"Latest on {topic}:" if topic else "Top headlines:"
            lines = [f"{i}. {it['title']}" + (f"  ·  {it['source']}" if it["source"] else "") for i, it in enumerate(items, 1)]
            spoken = (f"Here are the top stories, {title}. " +
                      " ".join(f"{['First', 'Second', 'Third'][i]}: {it['title']}." for i, it in enumerate(items[:3])))
            return Reply(head + "\n\n" + "\n".join(lines), speech=spoken,
                         sources=[{"title": it["title"], "url": it["url"]} for it in items])

        m = re.match(r"^(?:wikipedia|wiki)\s+(.+)$", low)
        if m:
            if not session.web or not tools.net_ok():
                return self._offline_reply(session)
            w = tools.wikipedia_summary(m.group(1))
            if not w:
                return Reply(f"I found no Wikipedia article for that, {title}.")
            return Reply(f"**{w['title']}**\n\n{w['extract']}", sources=[{"title": w["title"] + " (Wikipedia)", "url": w["url"]}])

        needs, query, explicit = tools.extract_search_query(text)
        if needs:
            return self.research(session, query, ctx, explicit)
        return None

    # ========================================================
    # CHAT (streaming)
    # ========================================================

    def _history_messages(self, session: Session) -> list:
        h = session.history[-config.MAX_HISTORY_MESSAGES:]
        while h and h[0]["role"] != "user":
            h = h[1:]
        budget, kept = config.CONTEXT_CHARS // 2, []
        for m in reversed(h):
            budget -= len(m["content"])
            if budget < 0:
                break
            kept.insert(0, m)
        while kept and kept[0]["role"] != "user":
            kept = kept[1:]
        return kept

    def _think(self, messages: list) -> str:
        """Private step-by-step reasoning pass (deep mode). Never shown to the user."""
        system = ("You are the private reasoning module of an assistant. Work the problem out step by step in at most "
                  "150 words: restate the task, list the relevant facts, carry out any calculation carefully, and note "
                  "pitfalls. Do NOT write the final answer.")
        prompt = self._render_prompt(system, [messages[-1]])
        return self._generate_raw(prompt, config.DEEP_NOTES_TOKENS)

    def chat_stream(self, session: Session, text: str, ctx: StreamCtx, extra: str = "", sources: list | None = None,
                    user_content: str | None = None, remember_as: str | None = None, ground: bool = True) -> Reply:
        title, low = session.title, text.lower()
        sources = list(sources or [])
        history = session.history                  # the chat this turn belongs to, even if the user switches mid-answer

        use_cloud = session.cloud and cloud.available()
        if not self.llm_ready and not use_cloud:
            return Reply(f"My language model is not loaded, {title}, so I can only run commands, tools and data analysis "
                         "for now. The server log shows the loading error.")

        # ground plain factual questions in Wikipedia so a small model does not have to guess
        if ground and session.web and tools.net_ok() and not extra:
            topic = tools.extract_fact_topic(text)
            if topic:
                wiki = tools.wikipedia_summary(topic)
                if wiki:
                    extra = (f"REFERENCE (Wikipedia, article “{wiki['title']}”):\n{wiki['extract']}\n"
                             "Base factual claims on this reference and cite it as [1]. Add well-established context "
                             "only if you are certain.")
                    sources.append({"title": wiki["title"] + " (Wikipedia)", "url": wiki["url"]})

        system = persona.system_prompt(session.mode, title, self.skills.memory_context(), session.summary, extra)
        if session.lang == "hi":
            # small models drift back to English, so say it at the top, the bottom and next to the question
            system = lang.instruction("hi", title) + "\n\n" + system + "\n\n" + lang.instruction("hi", title)
        else:
            system += "\n\n" + lang.instruction(session.lang, title)
        question = user_content or text
        if session.lang == "hi" and not use_cloud:
            question += lang.nudge()
        messages = self._history_messages(session) + [{"role": "user", "content": question}]

        # deep mode: Qwen3 thinks privately before answering (the thinking is never shown or spoken)
        deep = session.deep or bool(DEEP_TRIGGER.match(text))
        if deep:
            ctx.status("Thinking it through carefully")

        story = any(k in low for k in STORY_TRIGGER)
        max_tokens = config.STORY_MAX_TOKENS if story else config.CHAT_MAX_TOKENS
        if deep:
            max_tokens += config.DEEP_THINK_TOKENS

        out = None
        if use_cloud:
            ctx.status("Thinking" if not deep else "Thinking it through")
            print(f"[cloud] {cloud.name()} ({cloud.model_name()})")
            try:
                out = cloud.stream_text(system, messages, max(max_tokens, config.CLOUD_MAX_TOKENS), ctx.cancel,
                                        ctx.token, deep=deep)
            except Exception as e:
                print(f"[cloud] {type(e).__name__}: {e}")
                if ctx.started:                      # part of the answer is already out: don't repeat it
                    note = f"\n\n*(Lost my connection to the cloud, {title}. Ask again to continue.)*"
                    ctx.token(note)
                    history.append({"role": "user", "content": remember_as or text})
                    return Reply(note.strip(), streamed=True)
                if not self.llm_ready:
                    return Reply(f"I couldn't reach my cloud brain, {title}, and my local model is not loaded: {e}")
                ctx.status("Cloud unreachable, using the local brain")
                out = None
        if out is None:
            try:
                out = self.stream_text(system, messages, max_tokens, ctx.cancel, ctx.token,
                                       thinking=True if deep else None)
            except Exception as e:
                traceback.print_exc()
                return Reply(f"I hit an error while thinking, {title}: {e}")

        ctx.start()
        if ctx.cancel.is_set():
            out = (out + "\n\n*(stopped)*").strip()
        if any(p in low for p in ("who created you", "who made you", "who built you")):
            out = "I was created by Tejas."

        history.append({"role": "user", "content": remember_as or text})
        history.append({"role": "assistant", "content": out})
        return Reply(out, streamed=True, sources=sources)

    def after_turn(self, session: Session):
        """Fold old turns into a rolling summary so long conversations keep their memory. Runs after the reply is sent."""
        if not self.llm_ready or len(session.history) < config.MAX_HISTORY_MESSAGES:
            return
        cut = len(session.history) // 2
        old, keep = session.history[:cut], session.history[cut:]
        while keep and keep[0]["role"] != "user":
            old.append(keep.pop(0))
        transcript = "\n".join(f"{m['role']}: {m['content'][:600]}" for m in old)
        prompt = (f"Previous summary: {session.summary or 'none'}\n\nNew conversation to fold in:\n{transcript}\n\n"
                  "Write the updated summary in at most 120 words. Keep names, facts, preferences, decisions and open tasks.")
        try:
            summary = self.generate("You compress conversations faithfully. Plain text only.", prompt, 240)
            if summary:
                session.summary, session.history = summary, keep
        except Exception:
            traceback.print_exc()

    # ========================================================
    # MAIN ENTRY
    # ========================================================

    def respond_stream(self, session: Session, text: str, ctx: StreamCtx) -> Reply:
        try:
            text = (text or "").strip()
            if not text:
                return Reply("I'm listening.")
            title = session.title
            if session.lang_pref in ("en", "hi"):
                session.lang = session.lang_pref
            else:
                session.lang = lang.detect(text, previous=session.lang)

            m = CLOUD_SWITCH.match(text.strip().rstrip(".!"))
            if m:
                want = not m.group("local")
                if want and not cloud.available():
                    return Reply(f"The cloud brain isn't set up yet, {title}. Add GROQ_API_KEY or GEMINI_API_KEY (both free) "
                                 "to backend/.env and restart me.")
                session.cloud = want
                return Reply(f"Cloud brain on, {title}." if want else
                             f"Back on my local brain, {title}. Fully offline.",
                             events=[{"type": "settings", "deep": session.deep, "web": session.web,
                                      "voice": session.voice, "lang": session.lang_pref, "cloud": session.cloud}])

            if persona.is_stop(text):
                session.cancel.set()
                return Reply(lang.hi("Theek hai.", "ठीक है।") if session.lang == "hi" else f"Very good, {title}.",
                             events=[{"type": "stop_speech"}])

            if re.match(r"^(?:new chat|clear (?:the )?(?:chat|conversation)|forget (?:this|our) conversation|start over)$",
                        text.lower().rstrip(".!")):
                session.history, session.summary = [], ""
                return Reply(f"Conversation cleared, {title}. A clean slate.")

            handlers = (
                lambda: self.skills.handle_confirmation(text, session),
                lambda: self.handle_mode(session, text),
                lambda: self.skills.handle_memory(text),
                lambda: self.handle_timers(session, text),
                lambda: self.skills.handle_tasks(text),
                lambda: self.skills.handle_utils(text),
                lambda: commands.handle(session, text),
                lambda: system.handle(text, title),
                lambda: vision.handle(self, session, text, ctx),
                lambda: chart_editor.handle(session, text),
                lambda: filesearch.handle(self, session, text),
                lambda: None if filesearch.owns(text) else self.skills.handle_files(text, session),
            )
            for h in handlers:
                result = h()
                if result:
                    return result if isinstance(result, Reply) else Reply(result)

            quick = lang.quick_reply(text, title) or (persona.quick_reply(text, title) if session.lang == "en" else None)
            if quick:
                return Reply(quick)

            about_docs = session.docs is not None and not session.docs.empty and bool(
                DOC_WORDS.search(text) or CODE_WORDS.search(text) or self._named_docs(session, text))
            if about_docs:
                r = self.handle_docs(session, text, ctx)
                if r:
                    return r

            data_reply = self.handle_data(session, text)
            if data_reply:
                return data_reply

            if not about_docs:
                r = self.handle_docs(session, text, ctx)
                if r:
                    return r

            r = self.handle_tools(session, text, ctx)
            if r:
                return r

            web = self.skills.handle_web(text)
            if web:
                return Reply(web)

            return self.chat_stream(session, text, ctx)

        except Exception as e:
            traceback.print_exc()
            return Reply(f"I hit an error while processing that, {session.title}: {e}")

    # plain, non-streaming entry points (tests, scripts)
    def respond(self, session: Session, text: str) -> Reply:
        return self.respond_stream(session, text, null_ctx())

    def get_response(self, text: str, session: Session | None = None) -> str:
        session = session or getattr(self, "_default_session", None)
        if session is None:
            session = self._default_session = Session()
        return self.respond(session, text).text
