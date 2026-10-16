/**
 * VoicePlayer: plays the speech the server sends, one sentence at a time, without gaps.
 *
 * Signal chain (the "JARVIS" treatment, switchable):
 *   voice -> high-pass -> chest (low shelf) -> presence (peaking) -> air (high shelf)
 *         -> dry ------------------------------.
 *         -> slapback delay (a faint digital double) -> mix -> compressor -> analyser -> speakers
 *         -> small room reverb -----------------'
 *
 * The analyser is what makes the reactor pulse with the real voice.
 */

function base64ToArrayBuffer(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

export class VoicePlayer {
  /** @param onChange  called with true/false whenever "JARVIS is speaking" changes */
  constructor(onChange = () => {}) {
    this.onChange = onChange;
    this.ctx = null;
    this.fxOn = true;
    this.queue = [];
    this.active = new Set();
    this.open = new Set(); // turns whose audio has started but whose audio_end has not arrived
    this.decoding = 0;
    this.chain = Promise.resolve();
    this.gen = 0; // bumped by stop(): anything decoded for an older generation is dropped
    this.nextStart = 0;
    this._speaking = false;
    this._watchdog = null;
  }

  get speaking() {
    return this.active.size > 0 || this.queue.length > 0 || this.decoding > 0 || this.open.size > 0;
  }

  get supported() {
    return typeof window !== "undefined" && !!(window.AudioContext || window.webkitAudioContext);
  }

  /** Must be called from a user gesture (click / key) the first time. */
  async unlock() {
    if (!this.supported) return false;
    if (!this.ctx) {
      const AC = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AC();
      this._build();
    }
    if (this.ctx.state === "suspended") {
      try {
        await this.ctx.resume();
      } catch {
        return false;
      }
    }
    return this.ctx.state === "running";
  }

  _impulse(seconds = 0.45) {
    const c = this.ctx;
    const len = Math.floor(c.sampleRate * seconds);
    const buf = c.createBuffer(2, len, c.sampleRate);
    for (let ch = 0; ch < 2; ch++) {
      const d = buf.getChannelData(ch);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.pow(1 - i / len, 3.2);
    }
    return buf;
  }

  _build() {
    const c = this.ctx;
    const filter = (type, freq, gain = 0, q = 0.9) => {
      const f = c.createBiquadFilter();
      f.type = type;
      f.frequency.value = freq;
      f.gain.value = gain;
      f.Q.value = q;
      return f;
    };

    this.input = c.createGain();
    const hp = filter("highpass", 70);
    this.chest = filter("lowshelf", 190, 0);
    this.presence = filter("peaking", 2800, 0, 0.9);
    this.air = filter("highshelf", 7000, 0);

    this.input.connect(hp).connect(this.chest).connect(this.presence).connect(this.air);

    const mix = c.createGain();
    this.air.connect(mix); // dry

    this.delay = c.createDelay(0.1);
    this.delay.delayTime.value = 0.014;
    this.slap = c.createGain();
    this.air.connect(this.delay).connect(this.slap).connect(mix);

    this.room = c.createConvolver();
    this.room.buffer = this._impulse();
    this.roomGain = c.createGain();
    this.air.connect(this.room).connect(this.roomGain).connect(mix);

    const comp = c.createDynamicsCompressor();
    comp.threshold.value = -20;
    comp.ratio.value = 3;
    comp.attack.value = 0.005;
    comp.release.value = 0.2;

    this.master = c.createGain();
    this.master.gain.value = 1.0;
    this.analyser = c.createAnalyser();
    this.analyser.fftSize = 128;
    this.analyser.smoothingTimeConstant = 0.7;
    this.freq = new Uint8Array(this.analyser.frequencyBinCount);

    mix.connect(comp).connect(this.master).connect(this.analyser).connect(c.destination);
    this.setFx(this.fxOn);
  }

  setFx(on) {
    this.fxOn = !!on;
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    const set = (param, v) => param.setTargetAtTime(v, t, 0.03);
    set(this.chest.gain, on ? 2.5 : 0);
    set(this.presence.gain, on ? 3 : 0);
    set(this.air.gain, on ? 2 : 0);
    set(this.slap.gain, on ? 0.16 : 0);
    set(this.roomGain.gain, on ? 0.1 : 0);
  }

  /** Frequency data for the reactor, or null when nothing is playing. */
  frequencyData() {
    if (!this.analyser || !this.speaking) return null;
    this.analyser.getByteFrequencyData(this.freq);
    return this.freq;
  }

  /** Queue one chunk of speech (base64 audio). Chunks play in the order they are enqueued. */
  enqueue(turn, b64) {
    this.open.add(turn);
    this.decoding++;
    this._resetWatchdog();
    this._emit();
    const gen = this.gen;
    let bytes;
    try {
      bytes = base64ToArrayBuffer(b64);
    } catch {
      this.decoding--;
      this._emit();
      return;
    }
    this.chain = this.chain.then(async () => {
      try {
        if (!this.ctx) await this.unlock();
        if (!this.ctx) return;
        const buf = await this.ctx.decodeAudioData(bytes);
        if (gen === this.gen) this.queue.push(buf);
      } catch (e) {
        console.warn("JARVIS: could not decode audio chunk", e);
      } finally {
        this.decoding--;
        this._pump();
      }
    });
  }

  /** The server has sent everything for this turn. */
  end(turn) {
    this.open.delete(turn);
    this._emit();
  }

  _pump() {
    const c = this.ctx;
    if (!c) return this._emit();
    while (this.queue.length) {
      const buf = this.queue.shift();
      const src = c.createBufferSource();
      src.buffer = buf;
      src.connect(this.input);
      const startAt = Math.max(c.currentTime + 0.03, this.nextStart);
      src.start(startAt);
      this.nextStart = startAt + buf.duration;
      this.active.add(src);
      src.onended = () => {
        this.active.delete(src);
        this._emit();
      };
    }
    this._emit();
  }

  stop() {
    this.gen++;
    this.queue = [];
    for (const s of this.active) {
      try {
        s.onended = null;
        s.stop();
      } catch {
        /* already stopped */
      }
    }
    this.active.clear();
    this.open.clear();
    this.nextStart = 0;
    this._emit();
  }

  /** A short two-note chime (timers, wake word). */
  async beep() {
    if (!(await this.unlock())) return;
    const c = this.ctx;
    const t0 = c.currentTime + 0.02;
    [880, 1318.5].forEach((f, i) => {
      const o = c.createOscillator();
      const g = c.createGain();
      o.type = "sine";
      o.frequency.value = f;
      g.gain.setValueAtTime(0.0001, t0 + i * 0.16);
      g.gain.exponentialRampToValueAtTime(0.25, t0 + i * 0.16 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, t0 + i * 0.16 + 0.32);
      o.connect(g).connect(c.destination);
      o.start(t0 + i * 0.16);
      o.stop(t0 + i * 0.16 + 0.35);
    });
  }

  _resetWatchdog() {
    clearTimeout(this._watchdog);
    // if audio_end never arrives (dropped connection), do not stay "speaking" forever
    this._watchdog = setTimeout(() => {
      if (!this.open.size) return;
      if (!this.active.size && !this.queue.length && !this.decoding) {
        this.open.clear();
        this._emit();
      } else this._resetWatchdog(); // still playing: check again later instead of giving up
    }, 25000);
  }

  _emit() {
    const s = this.speaking;
    if (s !== this._speaking) {
      this._speaking = s;
      this.onChange(s);
    }
  }
}
