"""
JARVIS WebSocket server.

Protocol (JSON, one object per message)

  browser -> server
    {"type": "chat",     "text": "..."}
    {"type": "cancel"}                                   stop generating and speaking
    {"type": "dataset",  "name": "sales.csv", "data": "<base64>"}
    {"type": "document", "name": "paper.pdf",  "data": "<base64>"}
    {"type": "say",      "text": "..."}                  speak this text (timers, replay)
    {"type": "greet"}                                    spoken greeting
    {"type": "settings", "deep": bool, "web": bool, "voice": bool, "cloud": bool, "lang": str}
    {"type": "mode",     "mode": "formal" | "witty" | "savage"}

  server -> browser
    hello | status | stream_start | token | reply | audio | audio_end | stop_speech | timer | timers_clear
    settings | mode | error | pong

Run:  python server.py
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import uuid

import websockets

import chart_editor
import cloud
import lang
import config
import system
import vision
import persona
from main import Client
from models import Reply, Session, StreamCtx
from voice import SpeechSink

jarvis = Client()


def _decode(msg: dict) -> bytes:
    raw = msg.get("data") or ""
    if "," in raw[:100]:                      # tolerate a data-URL prefix
        raw = raw.split(",", 1)[1]
    return base64.b64decode(raw, validate=False)


async def sender(ws, q: asyncio.Queue):
    """Single writer: keeps tokens, audio and replies in the order they were produced."""
    while True:
        item = await q.get()
        if item is None:
            break
        try:
            await ws.send(json.dumps(item, default=str))
        except websockets.exceptions.ConnectionClosed:
            break


async def handler(ws):
    session = Session()
    loop = asyncio.get_running_loop()
    out_q: asyncio.Queue = asyncio.Queue()
    turn_lock = asyncio.Lock()
    tasks: set = set()

    def emit(payload: dict):
        loop.call_soon_threadsafe(out_q.put_nowait, payload)

    send_task = asyncio.create_task(sender(ws, out_q))

    async def metrics_loop():
        """Real CPU / memory / battery readings for the HUD, every few seconds."""
        while True:
            snap = await asyncio.to_thread(system.snapshot)
            emit({"type": "metrics", **snap})
            await asyncio.sleep(3)

    print("🟢 Client connected")
    emit({"type": "hello", **jarvis.info(), "mode": session.mode, "title": session.title,
          "deep": session.deep, "web": session.web,
          "cloud": getattr(session, "cloud", False) and cloud.available()})

    def spawn(coro):
        t = asyncio.create_task(coro)
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    spawn(metrics_loop())

    live_sinks: list = []

    def new_turn() -> tuple:
        session.turn += 1
        tid = f"t{session.turn}-{uuid.uuid4().hex[:6]}"      # unique across reconnects, so old messages are never overwritten
        sink = SpeechSink(jarvis.voice, emit, tid, enabled=session.voice, title=session.title, lang=session.lang)
        live_sinks[:] = [s for s in live_sinks if s.thread is not None and s.thread.is_alive()]
        live_sinks.append(sink)
        return tid, sink

    def turn_emit(epoch: int):
        """Events of a turn are dropped once the user has switched to another chat."""
        return lambda payload: emit(payload) if session.epoch == epoch or payload.get("type") == "status" else None

    def deliver(reply: Reply, tid: str, sink: SpeechSink):
        payload = reply.payload(session.mode)
        payload["id"] = tid
        emit(payload)
        for ev in reply.events:
            emit(ev)
        spoken = reply.spoken()
        if spoken:
            sink.say(spoken)
        sink.close()

    def do_cancel():
        session.cancel.set()
        for s in live_sinks:                         # every voice still talking: answers, uploads, timers, greetings
            s.stop()
        emit({"type": "stop_speech"})

    # ------------------------------------------------------------------ turns
    async def run_chat(text: str, epoch: int):
        async with turn_lock:
            if session.epoch != epoch:               # asked in a chat the user has since left
                return
            session.cancel.clear()
            # decide the language first, so the voice for this answer is the right one from the first word
            session.lang = session.lang_pref if session.lang_pref in ("en", "hi") else \
                lang.detect(text, previous=session.lang)
            tid, sink = new_turn()
            session.sink = sink
            ctx = StreamCtx(turn_emit(epoch), session.cancel, sink, tid)
            reply = await asyncio.to_thread(jarvis.respond_stream, session, text, ctx)
            session.sink = None
            if session.epoch != epoch:
                sink.stop()
                return
            deliver(reply, tid, sink)
            await asyncio.to_thread(jarvis.after_turn, session)

    async def run_upload(kind: str, msg: dict):
        name = str(msg.get("name") or ("dataset.csv" if kind == "dataset" else "document.txt"))
        try:
            data = _decode(msg)
        except (binascii.Error, ValueError):
            emit({"type": "error", "text": "The upload was corrupted. Please try again."})
            return
        if len(data) > config.MAX_UPLOAD_BYTES:
            emit({"type": "error", "text": f"That file is larger than the {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."})
            return
        print(f"📁 {kind} upload: {name} ({len(data) / 1e6:.2f} MB)")
        emit({"type": "status", "text": f"Reading {name}..."})
        async with turn_lock:
            tid, sink = new_turn()
            try:
                fn = jarvis.load_bytes if kind == "dataset" else jarvis.load_document
                reply = await asyncio.to_thread(fn, session, name, data)
            except Exception as e:
                sink.close()
                print(f"{kind} error:", e)
                emit({"type": "error", "text": f"I couldn't read that file: {e}"})
                return
            deliver(reply, tid, sink)

    async def run_image(msg: dict, epoch: int):
        name = str(msg.get("name") or "picture.png")
        try:
            data = _decode(msg)
        except (binascii.Error, ValueError):
            emit({"type": "error", "text": "The picture was corrupted. Please try again."})
            return
        question = str(msg.get("text") or "")[:1000]
        print(f"🖼  image: {name} ({len(data) / 1e6:.2f} MB)")
        async with turn_lock:
            session.cancel.clear()
            if session.lang_pref in ("en", "hi"):
                session.lang = session.lang_pref
            elif question.strip():
                session.lang = lang.detect(question, previous=session.lang)
            tid, sink = new_turn()
            ctx = StreamCtx(turn_emit(epoch), session.cancel, sink, tid)
            try:
                reply = await asyncio.to_thread(vision.handle_image_upload, jarvis, session, name, data, question, ctx)
            except Exception as e:
                print("image error:", e)
                reply = Reply(f"I couldn't look at that picture: {e}")
            if session.epoch != epoch:
                sink.stop()
                return
            deliver(reply, tid, sink)

    async def run_say(text: str):
        tid, sink = new_turn()
        sink.say(text)
        sink.close()

    # ------------------------------------------------------------------ read loop
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
                if not isinstance(msg, dict):
                    raise ValueError
            except (json.JSONDecodeError, ValueError, TypeError):
                msg = {"type": "chat", "text": raw if isinstance(raw, str) else ""}

            kind = msg.get("type", "chat")
            try:
                if kind == "chat":
                    text = str(msg.get("text", "")).strip()
                    if text.startswith("DATASET_SELECTED:"):
                        continue
                    print("USER:", text[:200])
                    if persona.is_stop(text):                # handled instantly, never queued behind a running answer
                        do_cancel()
                        session.turn += 1
                        hi = session.lang_pref == "hi" or (session.lang_pref == "auto" and
                                                           lang.detect(text, previous=session.lang) == "hi")
                        payload = Reply(lang.hi("Theek hai.", "ठीक है।") if hi else f"Very good, {session.title}.").payload(session.mode)
                        payload["id"] = f"t{session.turn}-{uuid.uuid4().hex[:6]}"
                        emit(payload)
                        continue
                    spawn(run_chat(text, session.epoch))

                elif kind == "cancel":
                    do_cancel()

                elif kind in ("dataset", "document"):
                    spawn(run_upload(kind, msg))

                elif kind == "image":
                    spawn(run_image(msg, session.epoch))

                elif kind == "chart_edit":
                    spec = msg.get("spec") if isinstance(msg.get("spec"), dict) else {}
                    try:
                        chart = await asyncio.to_thread(chart_editor.apply_edit, session, spec)
                        emit({"type": "chart_update", "replace": str(msg.get("replace") or ""), "chart": chart})
                    except Exception as e:
                        emit({"type": "chart_error", "text": f"I couldn't redraw that chart: {e}"})

                elif kind == "chat_switch":
                    do_cancel()
                    jarvis.switch_chat(session, msg.get("history") or [], str(msg.get("summary") or ""))
                    emit({"type": "chat_switched", "id": str(msg.get("id") or "")})

                elif kind == "say":
                    spawn(run_say(str(msg.get("text", ""))[:1200]))

                elif kind == "greet":
                    tid, sink = new_turn()
                    deliver(jarvis.greet(session), tid, sink)

                elif kind == "settings":
                    for key in ("deep", "web", "voice"):
                        if key in msg:
                            setattr(session, key, bool(msg[key]) and (key != "web" or config.WEB_ENABLED))
                    if "cloud" in msg:
                        session.cloud = bool(msg["cloud"]) and cloud.available()
                    if msg.get("lang") in ("auto", "en", "hi"):
                        session.lang_pref = msg["lang"]
                    emit({"type": "settings", "deep": session.deep, "web": session.web, "voice": session.voice,
                          "lang": session.lang_pref, "cloud": getattr(session, "cloud", False)})

                elif kind == "mode":
                    emit({"type": "mode", "mode": jarvis.set_mode(session, str(msg.get("mode", "formal")))})

                elif kind == "ping":
                    emit({"type": "pong"})

            except websockets.exceptions.ConnectionClosed:
                raise
            except Exception as e:
                print("Handler error:", e)
                emit({"type": "error", "text": f"Something went wrong: {e}"})

    except websockets.exceptions.ConnectionClosed:
        print("🔴 Client disconnected")
    except Exception as e:
        print("🔴 Connection error:", e)
    finally:
        for t in list(tasks):
            t.cancel()
        session.cancel.set()
        if session.sink is not None:
            session.sink.stop()
        out_q.put_nowait(None)
        await send_task


async def main():
    async with websockets.serve(
        handler, config.HOST, config.PORT,
        max_size=config.MAX_MESSAGE_BYTES, ping_interval=20, ping_timeout=120,
    ):
        info = jarvis.info()
        print("")
        print("======================================")
        print("🔥 JARVIS SERVER ONLINE")
        print("======================================")
        print(f"WebSocket : ws://{config.HOST}:{config.PORT}")
        print(f"Model     : {info['model']}  ({'loaded' if info['llm'] else 'NOT loaded, fallback mode'})")
        print(f"Voice     : {info['voice']['label']}")
        print(f"Web tools : {'on' if info['web'] else 'off'}")
        print(f"Cloud     : {cloud.status()}")
        vision.warm_up()
        print(f"Vision    : {vision.status()}")
        print("")
        print("Capabilities:")
        print("✓ Streaming answers, spoken sentence by sentence")
        print("✓ Deep step-by-step reasoning mode")
        print("✓ Web research with cited sources, news, weather, currency, units")
        print("✓ Document Q&A (PDF, Word, text) with citations")
        print("✓ Data analysis, charts, forecasts, cleaning, reports")
        print("✓ Timers, reminders, tasks, memory, calculator")
        print("✓ Pictures, screen reading and clipboard (vision model)")
        print("✓ Multiple chats, each with its own memory")
        print("✓ Hindi and English conversation (replies in the language you speak)")
        print("✓ Editable charts: type, colours, metric, grouping, title, theme")
        print("✓ Conversation memory that summarises itself")
        print("======================================")
        print("")
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 JARVIS server stopped.")
