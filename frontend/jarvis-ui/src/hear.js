/**
 * JARVIS's own ears: the browser records, the Mac transcribes with Whisper (backend/hearing.py).
 *
 * Chrome's built-in speech recognition goes deaf on macOS after audio plays, can only run one at a time
 * and stops on its own. This records the microphone directly instead, notices when you start and stop
 * talking (voice activity detection that adapts to the room), and sends each phrase to the backend.
 *
 * Same interface as Mic in mic.js, so App can use either:
 *   setHands, setStopListen, setLang, togglePtt, refresh, destroy, mode
 * plus setTalking(on): while JARVIS speaks, long sounds are his own voice and are not sent.
 */

const WORKLET = `
class JarvisTap extends AudioWorkletProcessor {
  constructor() { super(); this.buf = new Float32Array(1024); this.n = 0; }
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch) for (let i = 0; i < ch.length; i++) {
      this.buf[this.n++] = ch[i];
      if (this.n === this.buf.length) { this.port.postMessage(this.buf); this.buf = new Float32Array(1024); this.n = 0; }
    }
    return true;
  }
}
registerProcessor("jarvis-tap", JarvisTap);
`;

export const hearSupported = () =>
  typeof window !== "undefined" &&
  !!navigator.mediaDevices?.getUserMedia &&
  !!(window.AudioContext || window.webkitAudioContext) &&
  typeof window.AudioWorkletNode !== "undefined";

const PRE_ROLL_MS = 320; // keep a little audio from before the voice was detected, so first syllables survive
const START_MS = 70; // this much loud audio in a row starts a phrase
const MIN_VOICE_MS = 220; // shorter than this is a click or a bump, not words
const MAX_PHRASE_MS = 14000;
const PTT_WAIT_MS = 8000; // push-to-talk gives up if nothing is said

/** Float32 chunks at `rate` -> base64 of 16 kHz mono 16-bit PCM. */
function toPcm16Base64(chunks, rate) {
  let n = 0;
  for (const c of chunks) n += c.length;
  const all = new Float32Array(n);
  let o = 0;
  for (const c of chunks) {
    all.set(c, o);
    o += c.length;
  }
  const ratio = rate / 16000;
  const outLen = Math.floor(n / ratio);
  const pcm = new Int16Array(outLen);
  for (let i = 0; i < outLen; i++) {
    // average the samples that fall into this output sample (a simple low-pass before decimating)
    const a = Math.floor(i * ratio);
    const b = Math.max(a + 1, Math.min(n, Math.floor((i + 1) * ratio)));
    let s = 0;
    for (let j = a; j < b; j++) s += all[j];
    const v = Math.max(-1, Math.min(1, s / (b - a)));
    pcm[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  const bytes = new Uint8Array(pcm.buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}

export class HearMic {
  /**
   * @param transcribe (base64Pcm, lang) => Promise<string>   sends the phrase to the backend
   * @param onResult   (text, isFinal, mode, confidence) for every phrase understood
   * @param onPtt      (on) push-to-talk started / ended
   * @param onDenied   () the user blocked the microphone
   * @param onError    (code) a mic problem worth showing
   * @param onState    (live) the microphone opened / closed
   * @param onHearing  (on, mode) a phrase started / ended (for a live "…" caption)
   */
  constructor({ transcribe, onResult, onPtt, onDenied, onError, onState, onHearing }) {
    Object.assign(this, { transcribe, onResult, onPtt, onDenied, onError, onState, onHearing });
    this.hands = false;
    this.stopListen = false;
    this.ptt = false;
    this.talking = false;
    this.lang = "auto";
    this.denied = false;
    this.ctx = null;
    this.stream = null;
    this.node = null;
    this.opening = null;
    this.closeTimer = null;
    this.retryTimer = null;
    this.pttTimer = null;
    this.chain = Promise.resolve();
    this.floor = 0.004;
    this.ignoreUntil = 0;
    this._resetVad();
  }

  get mode() {
    if (this.ptt) return "ptt";
    if (this.hands) return "hands";
    if (this.stopListen) return "stop";
    return "off";
  }

  setHands(on) {
    this.hands = !!on;
    this._sync();
  }

  setStopListen(on) {
    this.stopListen = !!on;
    this._sync();
  }

  /** "auto" | "en" | "hi", or Chrome-style codes (en-IN = auto, en-GB = en, hi-IN = hi) */
  setLang(code) {
    const c = String(code || "auto");
    this.lang = /^hi/i.test(c) ? "hi" : c === "en" || c === "en-GB" || c === "en-US" ? "en" : "auto";
  }

  setTalking(on) {
    this.talking = !!on;
  }

  refresh() {
    /* a recorded microphone never goes deaf: nothing to do */
  }

  togglePtt() {
    if (!hearSupported()) return false;
    if (this.ptt) {
      this._endPtt();
      return true;
    }
    this.ptt = true;
    this.onPtt?.(true);
    this._resetVad(); // start clean: half a phrase from before belongs to the old mode
    this.ignoreUntil = performance.now() + 450; // let the chime pass
    clearTimeout(this.pttTimer);
    this.pttTimer = setTimeout(() => {
      if (this.ptt && !this.inSpeech) this._endPtt();
    }, PTT_WAIT_MS);
    this._sync();
    return true;
  }

  destroy() {
    clearTimeout(this.closeTimer);
    clearTimeout(this.retryTimer);
    clearTimeout(this.pttTimer);
    this.hands = this.stopListen = this.ptt = false;
    this._close();
  }

  // ---------------------------------------------------------------- internals

  _endPtt() {
    if (!this.ptt) return;
    this.ptt = false;
    clearTimeout(this.pttTimer);
    this.onPtt?.(false);
    this._sync();
  }

  _sync() {
    if (this.denied) return;
    clearTimeout(this.closeTimer);
    if (this.mode !== "off") {
      this._open();
    } else {
      if (this.inSpeech) this._resetVad();
      // stay open a moment: the "stop" listener switches on and off with every answer
      this.closeTimer = setTimeout(() => this.mode === "off" && this._close(), 5000);
    }
  }

  _open() {
    if (this.ctx || this.opening) return;
    this.opening = (async () => {
      let stream = null;
      let ctx = null;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
        const AC = window.AudioContext || window.webkitAudioContext;
        ctx = new AC();
        if (ctx.state === "suspended") await ctx.resume().catch(() => {});
        const url = URL.createObjectURL(new Blob([WORKLET], { type: "application/javascript" }));
        try {
          await ctx.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        const src = ctx.createMediaStreamSource(stream);
        const node = new AudioWorkletNode(ctx, "jarvis-tap");
        const silent = ctx.createGain();
        silent.gain.value = 0; // the graph must reach the speakers to run, but nothing is heard
        src.connect(node).connect(silent).connect(ctx.destination);
        node.port.onmessage = (e) => this._frame(e.data);
        ctx.onstatechange = () => {
          if (ctx.state === "suspended" && this.mode !== "off") ctx.resume().catch(() => {});
        };
        const track = stream.getAudioTracks()[0];
        if (track) {
          track.onended = () => {
            // unplugged, or another app took the microphone: reopen
            this.onError?.("audio-capture");
            this._close();
            clearTimeout(this.retryTimer);
            this.retryTimer = setTimeout(() => this._sync(), 1500);
          };
        }
        this.ctx = ctx;
        this.stream = stream;
        this.node = node;
        this.rate = ctx.sampleRate;
        this.onState?.(true);
        if (this.mode === "off") this._sync(); // switched off while opening
      } catch (e) {
        stream?.getTracks().forEach((t) => t.stop());
        ctx?.close().catch(() => {});
        if (e?.name === "NotAllowedError" || e?.name === "SecurityError") {
          this.denied = true;
          this.hands = this.stopListen = false;
          this._endPtt();
          this.onDenied?.();
        } else {
          this.onError?.(e?.name === "NotFoundError" || e?.name === "NotReadableError" ? "audio-capture" : "start");
          clearTimeout(this.retryTimer);
          this.retryTimer = setTimeout(() => this._sync(), 3000);
        }
      } finally {
        this.opening = null;
      }
    })();
  }

  _close() {
    const { ctx, stream, node } = this;
    this.ctx = this.stream = this.node = null;
    this._resetVad();
    if (node) node.port.onmessage = null;
    stream?.getTracks().forEach((t) => {
      t.onended = null;
      t.stop();
    });
    if (ctx) {
      ctx.onstatechange = null;
      ctx.close().catch(() => {});
      this.onState?.(false);
    }
  }

  _resetVad() {
    this.inSpeech = false;
    this.pre = [];
    this.chunks = [];
    this.loud = 0;
    this.quiet = 0;
    this.voiced = 0;
    this.total = 0;
  }

  _frame(f) {
    const mode = this.mode;
    if (mode === "off") {
      if (this.inSpeech) this._resetVad();
      return;
    }
    const now = performance.now();
    const ms = (f.length / this.rate) * 1000;
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
    const rms = Math.sqrt(sum / f.length);
    if (now < this.ignoreUntil) return;

    // the noise floor follows the room: drops quickly, rises slowly (so speech does not raise it)
    if (!this.inSpeech) {
      this.floor = rms < this.floor ? this.floor * 0.9 + rms * 0.1 : this.floor * 0.997 + rms * 0.003;
      this.floor = Math.min(Math.max(this.floor, 0.0015), 0.04);
    }
    const startAt = Math.max(this.floor * 3.2, 0.012);
    const keepAt = Math.max(this.floor * 2, 0.007);

    if (!this.inSpeech) {
      this.pre.push(f);
      while (this.pre.length * ms > PRE_ROLL_MS) this.pre.shift();
      if (rms > startAt) {
        this.loud += ms;
        if (this.loud >= START_MS) this._begin(mode, ms);
      } else this.loud = 0;
      return;
    }

    this.chunks.push(f);
    this.total += ms;
    if (rms > keepAt) {
      this.voiced += ms;
      this.quiet = 0;
    } else this.quiet += ms;
    // how long a pause ends the phrase
    const hang = this.uttMode === "ptt" ? 900 : this.uttMode === "stop" || this.talking ? 450 : 700;
    if (this.quiet >= hang || this.total >= MAX_PHRASE_MS) this._finish();
  }

  _begin(mode, ms) {
    this.inSpeech = true;
    this.uttMode = mode;
    this.chunks = this.pre;
    this.pre = [];
    this.total = this.chunks.length * ms;
    this.voiced = this.loud;
    this.quiet = 0;
    this.loud = 0;
    this.onHearing?.(true, mode);
  }

  _finish() {
    const { chunks, uttMode: mode, voiced, total } = this;
    const talking = this.talking;
    this._resetVad();
    this.onHearing?.(false, mode);
    if (voiced < MIN_VOICE_MS) {
      // a click, a cough, a door. Push-to-talk keeps waiting for real words a little longer.
      if (mode === "ptt" && this.ptt) {
        clearTimeout(this.pttTimer);
        this.pttTimer = setTimeout(() => this.ptt && !this.inSpeech && this._endPtt(), 4000);
      }
      return;
    }
    if (talking && mode !== "ptt" && total > 3500) return; // a long sound while he talks is his own voice
    if (mode === "ptt") this._endPtt(); // one phrase per press

    let job;
    try {
      job = Promise.resolve(this.transcribe(toPcm16Base64(chunks, this.rate), this.lang));
    } catch (e) {
      job = Promise.reject(e);
    }
    job = job.catch((e) => {
      this.onError?.(/not ready/i.test(String(e?.message)) ? "not-ready" : "stt");
      return "";
    });
    // deliver in the order things were said
    this.chain = this.chain
      .then(() => job)
      .then((text) => {
        const t = String(text || "")
          .replace(/[.。!！]+$/u, "")
          .trim();
        if (t) this.onResult?.(t, true, mode, 0.9);
      })
      .catch(() => {});
  }
}
