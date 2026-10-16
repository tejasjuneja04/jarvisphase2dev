/**
 * One microphone for everything.
 *
 * Chrome allows a single SpeechRecognition at a time: two running together abort each other, which is
 * what made the mic switch itself off. So JARVIS keeps exactly one recogniser and only changes what the
 * words are used for:
 *
 *   "ptt"    push-to-talk: the next thing you say is sent, then the mic goes back to what it was doing
 *   "hands"  hands-free: always on, acts on "Jarvis ..." (App decides)
 *   "stop"   while he talks: listens only for "stop", "chup", "bas karo" ...
 *   "off"    nothing
 *
 * Push-to-talk wins over hands-free, which wins over the stop listener.
 */

const SR = () => (typeof window === "undefined" ? null : window.SpeechRecognition || window.webkitSpeechRecognition);

export const micSupported = () => !!SR();

export class Mic {
  /**
   * @param onResult  (text, isFinal, mode) for every result
   * @param onPtt     (on: boolean) push-to-talk started / ended
   * @param onDenied  () the user blocked the microphone
   * @param onError   (code) a mic problem worth showing (no microphone, speech service unreachable ...)
   * @param onState   (live: boolean) the recogniser started / stopped
   */
  constructor({ onResult, onPtt, onDenied, onError, onState }) {
    this.onResult = onResult;
    this.onPtt = onPtt;
    this.onDenied = onDenied;
    this.onError = onError;
    this.onState = onState;
    this.hands = false;
    this.stopListen = false;
    this.ptt = false;
    this.lang = "en-IN";
    this.rec = null;
    this.running = false;
    this.starting = false;
    this.recLang = null;
    this.timer = null;
    this.pttTimer = null;
    this.fails = 0;
    this.denied = false;
    this.lastActivity = Date.now();
    this.startedAt = 0;
    this.restartAt = 0;
    this.watchdog = null;
  }

  // Chrome sometimes ends without telling us, never finishes starting, or keeps "running" but goes deaf
  // (common on macOS after audio has played). The watchdog restarts a dead or stuck session and
  // refreshes a silent one. It is (re)armed whenever the mic is wanted, so it survives React remounts.
  _arm() {
    if (this.watchdog) return;
    this.watchdog = setInterval(() => {
      if (this.mode === "off") return;
      const now = Date.now();
      if (this.starting && now - this.startedAt > 5000) this._restart(50); // start never completed
      else if (this.running && this.mode === "hands" && now - this.lastActivity > 15000) this.refresh();
      else this._sync();
    }, 3000);
  }

  get mode() {
    if (this.ptt) return "ptt";
    if (this.hands) return "hands";
    if (this.stopListen) return "stop";
    return "off";
  }

  setHands(on) {
    this.hands = !!on;
    this.fails = 0;
    this._sync();
  }

  setStopListen(on) {
    this.stopListen = !!on;
    this._sync();
  }

  setLang(code) {
    if (code && code !== this.lang) {
      this.lang = code;
      if (this.running) this._restart(); // a new language needs a fresh session
    }
  }

  /** Start a fresh session (after JARVIS has spoken, Chrome's old one is often deaf). */
  refresh(delay = 120) {
    if (this.mode === "off" || this.denied) return;
    this.lastActivity = Date.now();
    this._restart(delay);
  }

  /** Toggle push-to-talk. Returns false when speech recognition is not available. */
  togglePtt() {
    if (!SR()) return false;
    if (this.ptt) {
      this._endPtt();
      return true;
    }
    this.ptt = true;
    this.fails = 0;
    this.onPtt?.(true);
    clearTimeout(this.pttTimer);
    this.pttTimer = setTimeout(() => this._endPtt(), 10000); // nothing said: give up quietly
    this._sync(); // if already listening (hands-free), results simply start counting as push-to-talk
    return true;
  }

  destroy() {
    clearInterval(this.watchdog);
    this.watchdog = null;
    clearTimeout(this.timer);
    clearTimeout(this.pttTimer);
    this.restartAt = 0;
    this.hands = this.stopListen = this.ptt = false;
    this._stop();
  }

  // ---------------------------------------------------------------- internals

  _endPtt() {
    if (!this.ptt) return;
    this.ptt = false;
    clearTimeout(this.pttTimer);
    this.onPtt?.(false);
    this._sync();
  }

  _make() {
    const Rec = SR();
    const rec = new Rec();
    rec.continuous = true;
    rec.interimResults = true;
    rec.maxAlternatives = 1;
    rec.onstart = () => {
      this.running = true;
      this.starting = false;
      this.lastActivity = Date.now();
      this.onState?.(true);
    };
    rec.onsoundstart = () => {
      this.lastActivity = Date.now();
    };
    rec.onresult = (e) => {
      this.fails = 0;
      this.lastActivity = Date.now();
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const text = e.results[i][0].transcript;
        const confidence = e.results[i][0].confidence || 0;
        const isFinal = e.results[i].isFinal;
        const mode = this.mode;
        this.onResult?.(text, isFinal, mode, confidence);
        if (mode === "ptt" && isFinal && text.trim()) this._endPtt();
        // a finished phrase in hands-free: start a clean session so the next one is heard too
        else if (mode === "hands" && isFinal && text.trim()) this._restart(150);
      }
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        this.denied = true;
        this.hands = this.stopListen = false;
        this._endPtt();
        this.onDenied?.();
        return;
      }
      if (e.error !== "no-speech" && e.error !== "aborted") {
        this.fails += 1;
        this.onError?.(e.error);
      }
    };
    rec.onend = () => {
      this.running = false;
      this.starting = false;
      this.onState?.(false);
      if (this.mode !== "off" && !this.denied) {
        clearTimeout(this.timer);
        this.restartAt = 0;
        // quick restart normally; back off if Chrome keeps failing (network, audio device busy)
        this.timer = setTimeout(() => this._sync(), Math.min(250 + this.fails * 700, 5000));
      }
    };
    return rec;
  }

  _sync() {
    if (this.denied || !SR()) return;
    const want = this.mode !== "off";
    if (want) this._arm();
    if (want && !this.running && !this.starting) this._start();
    else if (!want && (this.running || this.starting)) this._stop();
  }

  _start() {
    if (!this.rec || this.recLang !== this.lang) {
      this._detach();
      this.rec = this._make();
      this.recLang = this.lang;
    }
    this.rec.lang = this.lang;
    this.starting = true;
    this.startedAt = Date.now();
    try {
      this.rec.start();
    } catch {
      this.starting = false; // "already started": onstart/onend will sort it out
    }
  }

  _stop() {
    if (!this.rec) return;
    try {
      this.rec.abort();
    } catch {
      /* ignore */
    }
    this.running = false;
    this.starting = false;
  }

  _detach() {
    const old = this.rec;
    this.rec = null;
    if (old) {
      old.onend = null;
      old.onerror = null;
      old.onresult = null;
      old.onstart = null;
      old.onsoundstart = null;
      try {
        old.abort();
      } catch {
        /* ignore */
      }
    }
  }

  _restart(delay = 300) {
    this._detach();
    this.running = false;
    this.starting = false;
    // if a later restart is already planned (e.g. waiting for the wake chime to finish), keep it
    const at = Date.now() + delay;
    if (this.restartAt > at) return;
    clearTimeout(this.timer);
    this.restartAt = at;
    this.timer = setTimeout(() => {
      this.restartAt = 0;
      this._sync();
    }, delay);
  }
}
