import { useEffect, useRef, useState } from "react";
import Markdown from "./Markdown.jsx";
import { VoicePlayer } from "./audio.js";
import { dataUrl, hasWake, isTable, parseWake, speakable } from "./utils.js";
import { Mic, micSupported } from "./mic.js";
import { HearMic, hearSupported } from "./hear.js";

const WS_URL = "ws://localhost:8765";
const FULL_TEXT = "JUST A RATHER VERY INTELLIGENT SYSTEM";
const MODES = ["FORMAL", "WITTY", "SAVAGE"];
const MAX_UPLOAD_MB = 38;
const DATA_EXT = [".csv", ".tsv", ".xlsx", ".xls", ".json", ".parquet"];
const DOC_EXT = [
  ".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".log", ".py", ".js", ".ts",
  ".java", ".c", ".cpp", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml",
];
const REC_LANG = { auto: "en-IN", en: "en-GB", hi: "hi-IN" };
const LANG_LABEL = { auto: "AUTO", en: "EN", hi: "हिं" };
const HAS_DEVANAGARI = /[\u0900-\u097F]/;
const CHART_KINDS = [
  ["bar", "Bar"], ["hbar", "Sideways"], ["line", "Line"], ["area", "Area"],
  ["pie", "Pie"], ["donut", "Donut"], ["scatter", "Scatter"], ["histogram", "Spread"],
];
const PALETTES = {
  stark: ["#5cc8ff", "#e8c47c", "#e0524a", "#8f9cff"],
  arc: ["#5cc8ff", "#3b9eff", "#9be7ff", "#2f6bff"],
  gold: ["#e8c47c", "#c99a3c", "#f5d48a", "#a87b2a"],
  crimson: ["#e0524a", "#b3261e", "#f28b82", "#e8c47c"],
  ocean: ["#06b6d4", "#0ea5e9", "#14b8a6", "#22d3ee"],
  sunset: ["#f97316", "#fb7185", "#facc15", "#ef4444"],
  mono: ["#e9edf4", "#b8c0cf", "#8b93a5", "#5f6778"],
  vibrant: ["#5cc8ff", "#f472b6", "#facc15", "#4ade80"],
};
const IMG_EXT = [".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"];
const MAX_IMAGE_MB = 14;
const CHATS_KEY = "jarvis.chats.v1";
const ACTIVE_KEY = "jarvis.activeChat.v1";
const newId = () => `c-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

// keep saved chats small: charts and downloads are not stored, only text and picture thumbnails
const slimMessages = (msgs) =>
  msgs.filter((m) => !String(m.id).startsWith("local-")).map(({ charts, files, ...rest }) => ({ ...rest, hadCharts: (charts || []).length > 0 }));
const loadChats = () => {
  try {
    const list = JSON.parse(localStorage.getItem(CHATS_KEY) || "[]");
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
};
const makeThumb = (dataUrl, max = 640) =>
  new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      const k = Math.min(1, max / Math.max(img.width, img.height));
      const c = document.createElement("canvas");
      c.width = Math.round(img.width * k);
      c.height = Math.round(img.height * k);
      c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
      resolve(c.toDataURL("image/jpeg", 0.72));
    };
    img.onerror = () => resolve(dataUrl);
    img.src = dataUrl;
  });
const timeAgo = (t) => {
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "now";
  if (m < 60) return `${m}m`;
  if (m < 1440) return `${Math.round(m / 60)}h`;
  return `${Math.round(m / 1440)}d`;
};

// "stop", "jarvis stop", "stop jarvis", "shut up", "bas karo", "chup", "रुको" ... silence him at once
const STOP_RE =
  /^(?:(?:hey|ok|okay)[, ]+)?(?:jarvis[, ]+)?(?:please[, ]+)?(?:stop|stop it|stop talking|stop speaking|be quiet|quiet|silence|shut up|shush|enough|cancel|cancel that|never ?mind|that'?s enough|bas|bas bas|bas karo|bas kar|bas kar do|chup|chup karo|chup ho jao|chup raho|ruko|ruk jao|band karo|रुको|रुक जाओ|बस|बस करो|बस बस|चुप|चुप करो|चुप हो जाओ|चुप रहो|बंद करो|स्टॉप)(?:[, ]+(?:jarvis|please))?[.!।? ]*$/i;
const MIC_ERRORS = {
  "audio-capture": "Mic: no microphone found, or another app is using it",
  network: "Mic: Chrome's speech service is unreachable, retrying",
  "language-not-supported": "Mic: this language is not supported for listening",
  "bad-grammar": "Mic: recogniser error, retrying",
  "not-ready": "Hearing is still loading on the Mac",
  stt: "Hearing: the Mac could not transcribe that, try again",
  start: "Mic: could not open the microphone, retrying",
};
const isStopPhrase = (t = "") => STOP_RE.test(t.replace(/(?:हे\s*)?(जार्विस|जारविस|जार्वेस)/g, "jarvis").trim());

const BOOT_LINES = [
  "LOADING NEURAL CORE",
  "CALIBRATING ARC REACTOR",
  "MOUNTING ANALYTICS ENGINE",
  "OPENING SECURE CHANNEL",
];

const QUICK_STARTS = [
  ["👁", "What's on my screen?", "What's on my screen?"],
  ["📋", "Summarise my clipboard", "Summarise my clipboard"],
  ["🔎", "Find a file", "Find my latest PDF"],
  ["🌦", "Weather & news", "What's the weather and the top news today?"],
  ["⏱", "Set a timer", "Set a timer for 10 minutes"],
  ["🧠", "Think deeply", "Think step by step: how do I plan a productive week?"],
];

const ACTIONS = [
  ["Business analysis", "Analyse this dataset and give me a complete business analysis"],
  ["Dashboard", "Show me the dashboard"],
  ["Insights & advice", "Give me strategic business advice based on this data"],
  ["Forecast", "Forecast the next months"],
  ["Customers", "Show customer segments"],
  ["Clean data", "Clean the data"],
  ["Explore (EDA)", "Perform exploratory data analysis"],
  ["Export report", "Export a report"],
];

const stamp = () =>
  new Date().toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });

const fmtClock = (ms) => {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${String(r).padStart(2, "0")}`;
};

const hostOf = (url) => {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
};

function Metric({ label, value, pct, tone = "" }) {
  return (
    <div className="metric">
      <div className="metric-row">
        <span className="metric-label">{label}</span>
        <span className={`metric-value ${tone}`}>{value}</span>
      </div>
      {pct !== undefined && (
        <div className="metric-bar">
          <div className="metric-fill" style={{ width: `${pct}%` }} />
        </div>
      )}
    </div>
  );
}

function Toggle({ label, on, onClick, disabled, hint }) {
  return (
    <button
      type="button"
      className={`toggle ${on ? "on" : ""}`}
      onClick={onClick}
      disabled={disabled}
      aria-pressed={on}
      title={hint}
    >
      <span className="toggle-label">{label}</span>
      <span className="toggle-switch">
        <i />
      </span>
    </button>
  );
}

export default function App() {
  /* =========================
     STATE
     ========================= */
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [streamingId, setStreamingId] = useState(null);

  const [booting, setBooting] = useState(true);
  const [bootReady, setBootReady] = useState(false);
  const [voiceLive, setVoiceLive] = useState(false); // server voice playing
  const [voiceBrowser, setVoiceBrowser] = useState(false); // browser fallback voice
  const [listening, setListening] = useState(false); // push to talk
  const [handsAwake, setHandsAwake] = useState(false); // hands-free heard the wake word
  const [busy, setBusy] = useState(false);
  const [statusText, setStatusText] = useState("");

  const [time, setTime] = useState("");
  const [date, setDate] = useState("");
  const [subtitle, setSubtitle] = useState("");

  const [connection, setConnection] = useState("CONNECTING");
  const [info, setInfo] = useState({ llm: false, model: "", voice: null, web: true, title: "sir" });
  const [aiMode, setAiMode] = useState(MODES[0]);
  const [settings, setSettings] = useState(() => {
    let lang = "auto";
    try {
      lang = localStorage.getItem("jarvis.lang") || "auto";
    } catch {}
    return { voice: true, deep: false, web: true, cloud: false, lang };
  });
  const [fxOn, setFxOn] = useState(true);
  const [handsFree, setHandsFree] = useState(false);
  const [voiceStop, setVoiceStop] = useState(() => {
    try {
      return localStorage.getItem("jarvis.voiceStop") !== "0";
    } catch {
      return true;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem("jarvis.voiceStop", voiceStop ? "1" : "0");
    } catch {}
  }, [voiceStop]);
  const [responseTime, setResponseTime] = useState("0.0s");

  const [cpu, setCpu] = useState(null);
  const [memory, setMemory] = useState(null);
  const [battery, setBattery] = useState(null);
  const [logs, setLogs] = useState([]);

  const [dataInfo, setDataInfo] = useState(null);
  const [datasetStatus, setDatasetStatus] = useState("NO DATASET");
  const [docs, setDocs] = useState([]);
  const [timers, setTimers] = useState([]);
  const [, setTick] = useState(0);
  const [lightbox, setLightbox] = useState(null);

  const speaking = voiceLive || voiceBrowser;

  /* =========================
     REFS
     ========================= */
  const [studio, setStudio] = useState(null); // chart editor: {chart, spec, msgId, busy, error}
  const [rightOpen, setRightOpen] = useState(true);
  const [leftOpen, setLeftOpen] = useState(false); // small screens only
  const [chats, setChats] = useState(loadChats);
  const [activeChat, setActiveChat] = useState(() => {
    try {
      return localStorage.getItem(ACTIVE_KEY) || newId();
    } catch {
      return newId();
    }
  });
  const [leftTab, setLeftTab] = useState("chats");
  const [pendingImage, setPendingImage] = useState(null); // {name, data (full), thumb}
  const [dragging, setDragging] = useState(false);
  const [renaming, setRenaming] = useState(null);
  const messagesRef = useRef([]);
  const activeChatRef = useRef(activeChat);
  const imageInputRef = useRef(null);

  const socketRef = useRef(null);
  const reconnectRef = useRef(null);
  const runRef = useRef(0);
  const playerRef = useRef(null);

  const canvasRef = useRef(null);
  const reactorRef = useRef(null);
  const analyserRef = useRef(null);
  const dataRef = useRef(null);
  const rafRef = useRef(null);

  const micRef = useRef(null);
  const chatRef = useRef(null);
  const voicesRef = useRef([]);
  const audioContextRef = useRef(null);
  const micStreamRef = useRef(null);

  const sentAtRef = useRef(0);
  const speakingRef = useRef(false);
  const listeningRef = useRef(false);
  const busyRef = useRef(false);
  const settingsRef = useRef(settings);
  const titleRef = useRef("sir");
  const serverVoiceRef = useRef(false);
  const timersRef = useRef([]);
  const greetPendingRef = useRef(false);
  const awakeUntilRef = useRef(0);
  const ignoreUntilRef = useRef(0);
  const sendMessageRef = useRef(null);
  const handsFreeRef = useRef(false);
  const inflightRef = useRef(0); // questions sent and not yet answered
  const lastAudioTurnRef = useRef(null);
  const mutedTurnsRef = useRef(new Set()); // turns silenced by "stop": late audio for them is dropped

  useEffect(() => {
    speakingRef.current = speaking;
    if (!speaking) {
      ignoreUntilRef.current = Date.now() + 900; // do not hear our own tail-end
    }
  }, [speaking]);
  useEffect(() => {
    listeningRef.current = listening || handsAwake;
  }, [listening, handsAwake]);
  useEffect(() => {
    busyRef.current = busy;
  }, [busy]);
  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);
  useEffect(() => {
    handsFreeRef.current = handsFree;
  }, [handsFree, settings.lang]);

  useEffect(() => {
    messagesRef.current = messages;
  }, [messages]);
  useEffect(() => {
    activeChatRef.current = activeChat;
    try {
      localStorage.setItem(ACTIVE_KEY, activeChat);
    } catch {}
  }, [activeChat]);
  // restore the open chat on first load (the stored transcript for the active chat)
  useEffect(() => {
    const saved = chats.find((c) => c.id === activeChat);
    if (saved?.messages?.length) setMessages(saved.messages);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // save the active chat whenever its messages change (not while a reply is still streaming)
  useEffect(() => {
    if (!messages.some((m) => m.from === "user") || streamingId) return;
    setChats((prev) => {
      const first = messages.find((m) => m.from === "user");
      const existing = prev.find((c) => c.id === activeChat);
      const title =
        existing?.custom ? existing.title : (first?.text || "Picture").replace(/^Uploaded \w+: /, "").slice(0, 38) || "New chat";
      const entry = { ...(existing || {}), id: activeChat, title, updated: Date.now(), messages: slimMessages(messages) };
      const next = [entry, ...prev.filter((c) => c.id !== activeChat)].sort((a, b) => b.updated - a.updated);
      try {
        localStorage.setItem(CHATS_KEY, JSON.stringify(next.slice(0, 40)));
      } catch {
        // storage full: drop the pictures from older chats and try once more
        const lean = next.slice(0, 40).map((c, i) => (i === 0 ? c : { ...c, messages: c.messages.map(({ image, ...r }) => r) }));
        try {
          localStorage.setItem(CHATS_KEY, JSON.stringify(lean));
        } catch {}
      }
      return next;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [messages, streamingId]);

  const historyFor = (msgs) =>
    msgs
      .filter((m) => m.text && !String(m.id).startsWith("local-"))
      .slice(-16)
      .map((m) => ({ role: m.from === "user" ? "user" : "assistant", content: m.text }));

  const syncServerChat = (msgs, id) => sendJson({ type: "chat_switch", id, history: historyFor(msgs) });

  const startNewChat = () => {
    stopAllSpeech();
    inflightRef.current = 0;
    setPendingImage(null);
    setMessages([]);
    setBusy(false);
    setStreamingId(null);
    const id = newId();
    setActiveChat(id);
    syncServerChat([], id);
    setLeftTab("chats");
  };
  const openChat = (id) => {
    if (id === activeChat) return;
    const c = chats.find((x) => x.id === id);
    if (!c) return;
    stopAllSpeech();
    inflightRef.current = 0;
    setPendingImage(null);
    setBusy(false);
    setStreamingId(null);
    setMessages(c.messages || []);
    setActiveChat(id);
    syncServerChat(c.messages || [], id);
    scrollToBottom();
  };
  const deleteChat = (id) => {
    const rest = chats.filter((c) => c.id !== id);
    setChats(rest);
    try {
      localStorage.setItem(CHATS_KEY, JSON.stringify(rest));
    } catch {}
    if (id === activeChat) startNewChat();
  };
  const renameChat = (id, title) => {
    const t = title.trim();
    setRenaming(null);
    if (!t) return;
    setChats((prev) => {
      const next = prev.map((c) => (c.id === id ? { ...c, title: t.slice(0, 48), custom: true } : c));
      try {
        localStorage.setItem(CHATS_KEY, JSON.stringify(next));
      } catch {}
      return next;
    });
  };
  const exportChat = () => {
    const text = messages
      .filter((m) => m.text)
      .map((m) => `${m.from === "user" ? "YOU" : "JARVIS"}: ${m.text}`)
      .join("\n\n");
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    a.download = `${(chats.find((c) => c.id === activeChat)?.title || "jarvis-chat").replace(/[^\w-]+/g, "_")}.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  const addLog = (text) => setLogs((prev) => [`${stamp()}  ${text}`, ...prev].slice(0, 8));

  if (!playerRef.current) playerRef.current = new VoicePlayer((s) => setVoiceLive(s));

  /* =========================
     INITIALISATION
     ========================= */
  useEffect(() => {
    const runId = ++runRef.current;
    const alive = () => runRef.current === runId;

    const bootTimer = setTimeout(() => alive() && setBootReady(true), 2600);

    const tick = () => {
      const now = new Date();
      setTime(now.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" }));
      setDate(
        now
          .toLocaleDateString([], { weekday: "short", day: "2-digit", month: "short", year: "numeric" })
          .toUpperCase()
      );
    };
    tick();
    const clockTimer = setInterval(tick, 1000);

    const systemTimer = null; // real metrics arrive from the server

    let index = 0;
    const subtitleTimer = setInterval(() => {
      if (index < FULL_TEXT.length) {
        index++;
        setSubtitle(FULL_TEXT.slice(0, index));
      } else clearInterval(subtitleTimer);
    }, 45);

    // timers: fire when due, and refresh the countdowns
    const timerLoop = setInterval(() => {
      const now = Date.now();
      const due = timersRef.current.filter((t) => t.endsAt <= now);
      if (due.length) {
        timersRef.current = timersRef.current.filter((t) => t.endsAt > now);
        setTimers(timersRef.current);
        due.forEach(announceTimer);
      } else if (timersRef.current.length) setTick((x) => x + 1);
    }, 500);

    connectSocket(alive);
    loadVoices();
    if ("speechSynthesis" in window) speechSynthesis.onvoiceschanged = loadVoices;

    return () => {
      runRef.current++;
      clearTimeout(bootTimer);
      clearInterval(clockTimer);
      clearInterval(systemTimer);
      clearInterval(subtitleTimer);
      clearInterval(timerLoop);
      clearTimeout(reconnectRef.current);

      if (socketRef.current) {
        socketRef.current.onclose = null;
        socketRef.current.close();
        socketRef.current = null;
      }
      if (micStreamRef.current) {
        micStreamRef.current.getTracks().forEach((t) => t.stop());
        micStreamRef.current = null;
      }
      if (audioContextRef.current) {
        audioContextRef.current.close().catch(() => {});
        audioContextRef.current = null;
      }
      playerRef.current?.stop();
      if ("speechSynthesis" in window) speechSynthesis.cancel();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (booting) return;
    drawVisualizer();
    return () => rafRef.current && cancelAnimationFrame(rafRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting]);

  useEffect(() => {
    if (!lightbox) return;
    const onKey = (e) => e.key === "Escape" && setLightbox(null);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [lightbox]);

  useEffect(() => {
    playerRef.current?.setFx(fxOn);
  }, [fxOn]);

  // Space = talk (when not typing), Esc = stop talking
  useEffect(() => {
    if (booting) return undefined;
    const onKey = (e) => {
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(e.target?.tagName || "") || e.target?.isContentEditable;
      if (e.key === "Escape" && (speakingRef.current || busyRef.current)) {
        hushRef.current?.();
      } else if (e.code === "Space" && !typing && !e.repeat && !e.metaKey && !e.ctrlKey) {
        e.preventDefault();
        toggleMicRef.current?.();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [booting]);

  // any key or click on the boot screen activates (browsers only allow audio after a gesture)
  useEffect(() => {
    if (!booting || !bootReady) return;
    const onKey = (e) => (e.key === "Enter" || e.key === " ") && activate();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [booting, bootReady]);

  /* =========================
     ACTIVATION (unlocks audio, says hello)
     ========================= */
  const activate = async () => {
    await playerRef.current.unlock();
    setupMic();
    setBooting(false);
    if (messagesRef.current.length) startNewChat(); // Initialise always starts a fresh chat
    if (!sendJson({ type: "greet" })) greetPendingRef.current = true;
  };

  /* =========================
     WEBSOCKET
     ========================= */
  const sendJson = (payload) => {
    const ws = socketRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(payload));
    return true;
  };

  const pushJarvis = (text) => {
    setMessages((prev) => [...prev, { from: "jarvis", text, id: `local-${Date.now()}-${Math.random()}` }]);
    scrollToBottom();
  };

  const connectSocket = (alive) => {
    try {
      const ws = new WebSocket(WS_URL);
      socketRef.current = ws;

      ws.onopen = () => {
        if (socketRef.current !== ws) return;
        setConnection("ONLINE");
        addLog("Neural link established");
      };
      ws.onclose = () => {
        if (socketRef.current !== ws) return;
        setConnection("OFFLINE");
        setBusy(false);
        setStreamingId(null);
        addLog("Neural link lost. Retrying...");
        reconnectRef.current = setTimeout(() => alive() && connectSocket(alive), 3000);
      };
      ws.onerror = () => socketRef.current === ws && setConnection("ERROR");
      ws.onmessage = (event) => {
        if (socketRef.current !== ws) return;
        handleServerMessage(event.data);
      };
    } catch (error) {
      console.error(error);
      setConnection("ERROR");
    }
  };

  const applyDataset = (d) => {
    setDataInfo(d);
    setDatasetStatus(/_cleaned/i.test(d.name) ? "CLEANED DATASET READY" : "DATASET READY");
    addLog(`Dataset ready: ${d.name}`);
  };

  const finalizeReply = (msg) => {
    const text = msg.text || "";
    const charts = msg.charts || [];
    const files = msg.files || [];
    if (!text && !charts.length && !files.length) return;
    setMessages((prev) => {
      const base = {
        id: msg.id || `r-${Date.now()}`,
        from: "jarvis",
        text,
        charts,
        files,
        sources: msg.sources || [],
        suggestions: msg.suggestions || [],
      };
      const idx = prev.findIndex((m) => m.id === base.id);
      if (idx === -1) return [...prev, base];
      const next = prev.slice();
      next[idx] = { ...prev[idx], ...base };
      return next;
    });
    scrollToBottom();
  };

  const updateHearing = (h) =>
    setHearing((cur) =>
      cur.ready === !!h.ready && cur.available === !!h.available ? cur : { available: !!h.available, ready: !!h.ready }
    );

  const handleServerMessage = (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch {
      msg = { type: "reply", text: String(raw) };
    }

    switch (msg.type) {
      case "hello": {
        setInfo({
          llm: !!msg.llm,
          model: msg.model || "",
          voice: msg.voice || null,
          web: msg.web !== false,
          title: msg.title || "sir",
          cloudReady: !!msg.cloud_ready,
          cloudModel: msg.cloud_model || "",
        });
        titleRef.current = msg.title || "sir";
        if (msg.hearing) updateHearing(msg.hearing);
        serverVoiceRef.current = !!msg.voice?.enabled;
        if (messagesRef.current.length) syncServerChat(messagesRef.current, activeChatRef.current);
        if (settingsRef.current.lang !== "auto") sendJson({ type: "settings", lang: settingsRef.current.lang });
        if (msg.mode) setAiMode(String(msg.mode).toUpperCase());
        setSettings((s) => ({ ...s, deep: !!msg.deep, web: msg.web !== false, cloud: !!msg.cloud }));
        addLog(msg.llm ? `Model ready: ${msg.model}` : "Language model offline (fallback)");
        addLog(msg.cloud_ready ? "Cloud brain available" : "Cloud brain: not set up");
        addLog(msg.voice?.enabled ? `Voice: ${msg.voice.label}` : "Voice: browser fallback");
        if (greetPendingRef.current) {
          greetPendingRef.current = false;
          sendJson({ type: "greet" });
        }
        break;
      }
      case "stt": {
        const p = sttPendingRef.current.get(msg.id);
        if (!p) break;
        sttPendingRef.current.delete(msg.id);
        clearTimeout(p.timer);
        if (msg.error) p.reject(new Error(msg.error));
        else p.resolve(msg.text || "");
        break;
      }
      case "status":
        setStatusText(msg.text || "");
        addLog(msg.text || "");
        break;
      case "stream_start":
        setStreamingId(msg.id);
        setMessages((prev) =>
          prev.some((m) => m.id === msg.id) ? prev : [...prev, { id: msg.id, from: "jarvis", text: "" }]
        );
        scrollToBottom();
        break;
      case "token":
        setMessages((prev) => prev.map((m) => (m.id === msg.id ? { ...m, text: (m.text || "") + msg.text } : m)));
        scrollToBottom();
        break;
      case "reply": {
        if (msg.mode) setAiMode(String(msg.mode).toUpperCase());
        if (msg.dataset) applyDataset(msg.dataset);
        if (msg.docs) setDocs(msg.docs);
        if (sentAtRef.current) {
          setResponseTime(`${((performance.now() - sentAtRef.current) / 1000).toFixed(1)}s`);
          sentAtRef.current = 0;
        }
        inflightRef.current = Math.max(0, inflightRef.current - 1);
        if (inflightRef.current === 0) {
          setBusy(false);
          setStatusText("");
        }
        setStreamingId((cur) => (cur === msg.id ? null : cur));
        finalizeReply(msg);
        addLog(msg.charts?.length ? `Response + ${msg.charts.length} chart(s)` : "Response received");
        browserFallbackSpeak(msg);
        break;
      }
      case "audio":
        if (mutedTurnsRef.current.has(msg.id)) break; // he was told to stop
        lastAudioTurnRef.current = msg.id;
        if (settingsRef.current.voice) playerRef.current.enqueue(msg.id, msg.data);
        break;
      case "metrics":
        setCpu(msg.cpu ?? null);
        setMemory(msg.mem ?? null);
        setBattery(msg.battery == null ? null : { level: msg.battery, charging: !!msg.charging });
        if (msg.hearing) updateHearing(msg.hearing);
        break;
      case "audio_end":
        playerRef.current.end(msg.id);
        break;
      case "stop_speech":
        stopAllSpeech();
        break;
      case "timer":
        addTimer(msg);
        break;
      case "timers_clear":
        timersRef.current = [];
        setTimers([]);
        break;
      case "settings":
        setSettings((s) => ({ ...s, voice: !!msg.voice, deep: !!msg.deep, web: !!msg.web, lang: msg.lang || s.lang, cloud: !!msg.cloud }));
        break;
      case "chart_update": {
        const chart = msg.chart;
        if (!chart) break;
        setMessages((prev) =>
          prev.map((m) =>
            m.charts?.some((c) => c.id === msg.replace)
              ? { ...m, charts: m.charts.map((c) => (c.id === msg.replace ? chart : c)) }
              : m
          )
        );
        setStudio((st) => (st ? { ...st, chart, spec: { ...st.spec, ...chart.spec }, busy: false, error: "" } : st));
        addLog(`Chart updated: ${chart.title}`);
        break;
      }
      case "chart_error":
        setStudio((st) => (st ? { ...st, busy: false, error: msg.text } : st));
        break;
      case "mode":
        setAiMode(String(msg.mode).toUpperCase());
        addLog(`Mode: ${String(msg.mode).toUpperCase()}`);
        break;
      case "error":
        inflightRef.current = Math.max(0, inflightRef.current - 1);
        if (inflightRef.current === 0) setBusy(false);
        setStreamingId(null);
        setStatusText("");
        setDatasetStatus((s) => (s === "UPLOADING..." ? "NO DATASET" : s));
        pushJarvis(msg.text || "Something went wrong.");
        break;
      default:
        break;
    }
  };

  /* =========================
     SPEECH
     ========================= */
  const stopAllSpeech = () => {
    if (lastAudioTurnRef.current) mutedTurnsRef.current.add(lastAudioTurnRef.current);
    playerRef.current?.stop();
    if ("speechSynthesis" in window) speechSynthesis.cancel();
    setVoiceBrowser(false);
  };

  const loadVoices = () => {
    if (!("speechSynthesis" in window)) return;
    const v = speechSynthesis.getVoices();
    if (v.length) voicesRef.current = v;
  };

  const speakBrowser = (text) => {
    if (!("speechSynthesis" in window) || !text || !settingsRef.current.voice) return;
    const go = () => {
      const voices = voicesRef.current;
      const hindi = HAS_DEVANAGARI.test(text);
      const voice = hindi
        ? voices.find((v) => v.lang === "hi-IN") || voices.find((v) => v.lang?.startsWith("hi"))
        : voices.find((v) => v.lang === "en-GB" && /Daniel|Arthur|Oliver|Google|UK|English/i.test(v.name)) ||
        voices.find((v) => v.lang === "en-GB") ||
        voices.find((v) => /Daniel|Arthur|Oliver/i.test(v.name)) ||
        voices.find((v) => v.lang?.startsWith("en"));
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      if (voice) u.voice = voice;
      u.lang = hindi ? "hi-IN" : "en-GB";
      u.rate = hindi ? 1 : 0.93;
      u.pitch = hindi ? 1 : 0.8;
      // Chrome sometimes never fires onend, which left JARVIS "speaking" forever and hands-free deaf
      let poll = null;
      const done = () => {
        clearInterval(poll);
        setVoiceBrowser(false);
      };
      u.onstart = () => {
        setVoiceBrowser(true);
        clearInterval(poll);
        poll = setInterval(() => {
          if (!window.speechSynthesis.speaking) done();
        }, 700);
      };
      u.onend = done;
      u.onerror = done;
      window.speechSynthesis.speak(u);
    };
    if (!voicesRef.current.length) setTimeout(go, 300);
    else go();
  };

  // only when the server has no voice engine of its own
  const browserFallbackSpeak = (msg) => {
    if (serverVoiceRef.current) return;
    if (/\(stopped\)\*?\s*$/.test(msg.text || "")) return; // he was interrupted: do not read it out
    const text = msg.speech || (msg.streamed ? msg.text : msg.text && !isTable(msg.text) && msg.text.length <= 260 ? msg.text : "");
    if (text) speakBrowser(speakable(text));
  };

  /* =========================
     TIMERS
     ========================= */
  const addTimer = (t) => {
    const timer = { id: t.id, label: t.label || "", seconds: t.seconds, endsAt: Date.now() + t.seconds * 1000 };
    timersRef.current = [...timersRef.current, timer];
    setTimers(timersRef.current);
    addLog(`Timer set: ${fmtClock(t.seconds * 1000)}`);
  };

  const announceTimer = (t) => {
    const title = titleRef.current;
    const text = t.label ? `Reminder, ${title}: ${t.label}.` : `Your timer has finished, ${title}.`;
    playerRef.current?.beep();
    pushJarvis(text);
    addLog("Timer finished");
    if (serverVoiceRef.current && settingsRef.current.voice) sendJson({ type: "say", text });
    else speakBrowser(text);
  };

  const cancelTimer = (id) => {
    timersRef.current = timersRef.current.filter((t) => t.id !== id);
    setTimers(timersRef.current);
  };

  /* =========================
     VOICE INPUT
     ========================= */
  // one recogniser for push-to-talk, hands-free and "stop" (see mic.js)
  const awakeTimerRef = useRef(null);
  const micResultRef = useRef(null);
  const micErrAtRef = useRef(0);
  const micDownTimerRef = useRef(null);
  const [micLive, setMicLive] = useState(false); // the recogniser is actually running
  const [caption, setCaption] = useState(""); // what the mic is hearing right now, shown live
  const captionTimerRef = useRef(null);
  const showCaption = (text, hold = 0) => {
    clearTimeout(captionTimerRef.current);
    setCaption(text);
    if (hold) captionTimerRef.current = setTimeout(() => setCaption(""), hold);
  };
  // Two engines, same interface: JARVIS's own hearing (the Mac transcribes with Whisper, hear.js) when the
  // backend has it, otherwise Chrome's speech recognition (mic.js).
  const engineRef = useRef("chrome");
  const sttPendingRef = useRef(new Map());
  const transcribe = (data, lang) =>
    new Promise((resolve, reject) => {
      const id = `s${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
      if (!sendJson({ type: "stt", id, data, lang })) {
        reject(new Error("offline"));
        return;
      }
      const timer = setTimeout(() => {
        sttPendingRef.current.delete(id);
        reject(new Error("timeout"));
      }, 25000);
      sttPendingRef.current.set(id, { resolve, reject, timer });
    });
  const micCallbacks = () => ({
      onResult: (...a) => micResultRef.current?.(...a),
      onPtt: (on) => {
        setListening(on);
        // a chime only with JARVIS's own hearing: Chrome's listener goes deaf on macOS when a sound plays
        if (on && engineRef.current === "local") playerRef.current?.beep();
        if (!on) setTimeout(() => setCaption((c) => (c === "…" ? "" : c)), 1600);
      },
      onHearing: (on, mode) => {
        // a phrase is being spoken: show "…" until the words arrive
        if (on && (mode === "ptt" || Date.now() < awakeUntilRef.current)) showCaption("…");
        else if (!on) setCaption((c) => (c === "…" ? "" : c));
      },
      onDenied: () => {
        setHandsFree(false);
        alert("JARVIS needs microphone permission. Allow it in Chrome's address bar, then try again.");
      },
      onError: (code) => {
        const now = Date.now();
        if (now - micErrAtRef.current < 8000) return; // once is enough while it retries
        micErrAtRef.current = now;
        addLog(MIC_ERRORS[code] || `Mic: ${code}, retrying`);
      },
      onState: (live) => {
        // restarts between phrases are normal: only say "reconnecting" if it stays down
        clearTimeout(micDownTimerRef.current);
        if (live) {
          setMicLive(true);
          if (micRef.current?.mode === "ptt" && engineRef.current === "chrome") addLog("Mic open: speak now");
        } else micDownTimerRef.current = setTimeout(() => setMicLive(false), 2500);
      },
  });
  const makeMic = (engine) =>
    engine === "local" ? new HearMic({ ...micCallbacks(), transcribe }) : new Mic(micCallbacks());
  if (!micRef.current) micRef.current = makeMic("chrome");
  useEffect(() => () => micRef.current?.destroy(), []);

  // switch engines when the backend's hearing becomes ready (or goes away), keeping what the mic was doing
  const [hearing, setHearing] = useState({ available: false, ready: false });
  const engine = hearing.ready && hearSupported() ? "local" : "chrome";
  useEffect(() => {
    if (engineRef.current === engine) return;
    const old = micRef.current;
    const wasPtt = old.ptt;
    const wasStop = old.stopListen;
    old.destroy();
    if (wasPtt) setListening(false);
    engineRef.current = engine;
    const next = makeMic(engine);
    micRef.current = next;
    next.setLang(REC_LANG[settingsRef.current.lang] || "en-IN");
    next.setTalking?.(speakingRef.current);
    next.setHands(handsFreeRef.current);
    next.setStopListen(wasStop);
    addLog(engine === "local" ? "Hearing: JARVIS (Whisper on this Mac)" : "Hearing: Chrome speech recognition");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine]);

  micResultRef.current = (text, isFinal, mode, confidence = 0) => {
    const heard = (text || "").trim();
    if (!heard) return;
    const filler = /^(?:uh+|um+|hm+|ah+|er+|mm+)[.!? ]*$/i.test(heard); // noise, not words
    if (mode === "ptt") {
      showCaption(heard, isFinal ? 1600 : 0);
      if (isFinal && !filler) sendMessageRef.current?.(heard);
      return;
    }
    // while he is speaking, anything heard is probably his own voice: only "stop" counts.
    // While he is only thinking, hands-free keeps working (a new "Jarvis ..." simply queues up).
    if (speakingRef.current) {
      if (isStopPhrase(heard)) hushRef.current?.(); // mid-sentence, no wake word needed
      return;
    }
    if (busyRef.current && isStopPhrase(heard)) {
      if (isFinal) hushRef.current?.();
      return;
    }
    if (mode !== "hands") return;
    // live caption once he is addressed (after "Jarvis", or while the follow-up window is open)
    const named = hasWake(heard);
    const addressed = named || Date.now() < awakeUntilRef.current;
    if (!isFinal) {
      if (addressed) showCaption(heard);
      return;
    }
    if (Date.now() < ignoreUntilRef.current || isStopPhrase(heard)) return;
    // background noise: "umm", or a very unsure guess in the follow-up window without his name
    if (filler || (!named && confidence > 0 && confidence < 0.2)) return;
    if (addressed) showCaption(heard, 1600);
    const r = parseWake(heard, Date.now() < awakeUntilRef.current);
    if (!r.heard) return;
    if (!r.command) {
      setHandsAwake(true); // the reactor turns gold; no chime, it would make Chrome stop hearing
      awakeUntilRef.current = Date.now() + 9000;
      clearTimeout(awakeTimerRef.current);
      awakeTimerRef.current = setTimeout(() => setHandsAwake(false), 9000);
      addLog("Wake word heard");
      return;
    }
    setHandsAwake(false);
    awakeUntilRef.current = 0; // the next command needs "Jarvis" again
    sendMessageRef.current?.(r.command);
  };

  const toggleMicRef = useRef(null);
  const canListen = () => (engineRef.current === "local" ? hearSupported() : micSupported());
  const toggleMic = () => {
    if (!canListen()) {
      alert("Speech recognition is not supported by this browser. Try Chrome.");
      return;
    }
    if (!listening) stopAllSpeech();
    micRef.current.setLang(REC_LANG[settingsRef.current.lang] || "en-IN");
    micRef.current.togglePtt();
  };
  toggleMicRef.current = toggleMic;

  // silence JARVIS at once (voice "stop", "chup", "bas karo" ...)
  const hushNow = () => {
    stopAllSpeech();
    inflightRef.current = 0;
    sendJson({ type: "cancel" });
    setBusy(false);
    setStreamingId(null);
    addLog("Stopped by voice");
  };
  const hushRef = useRef(null);
  hushRef.current = hushNow;

  // hands-free: always listening, acts on "Jarvis ..." and follow-ups for a while afterwards
  useEffect(() => {
    if (handsFree && !canListen()) {
      alert("Hands-free needs speech recognition, which this browser does not support. Try Chrome.");
      setHandsFree(false);
      return;
    }
    micRef.current.setHands(handsFree);
    if (handsFree) addLog("Hands-free on: say “Jarvis…”");
    else setHandsAwake(false);
  }, [handsFree]);

  useEffect(() => {
    micRef.current.setLang(REC_LANG[settings.lang] || "en-IN");
  }, [settings.lang]);

  // when he finishes talking, give hands-free a fresh session: Chrome's old one often stops hearing
  useEffect(() => {
    micRef.current.setTalking?.(speaking); // JARVIS's own hearing ignores long sounds while he talks
    if (!speaking && handsFreeRef.current) micRef.current.refresh();
  }, [speaking]);

  // without hands-free: while he answers, listen only for "stop", "chup", "bas karo" ...
  const [talkWindow, setTalkWindow] = useState(false);
  useEffect(() => {
    if (speaking || busy) {
      setTalkWindow(true);
      return undefined;
    }
    const id = setTimeout(() => setTalkWindow(false), 1500);
    return () => clearTimeout(id);
  }, [speaking, busy]);
  useEffect(() => {
    micRef.current.setStopListen(voiceStop && talkWindow);
  }, [voiceStop, talkWindow]);

  /* =========================
     MIC VISUALISER
     ========================= */
  const setupMic = async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      micStreamRef.current = stream;
      const AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      const ctx = new AC();
      audioContextRef.current = ctx;
      const analyser = ctx.createAnalyser();
      analyser.fftSize = 128;
      ctx.createMediaStreamSource(stream).connect(analyser);
      analyserRef.current = analyser;
      dataRef.current = new Uint8Array(analyser.frequencyBinCount);
    } catch {
      console.log("Microphone visualizer unavailable. Using simulated wave.");
    }
  };

  /* The reactor: a living core that swells, vibrates and throws shockwaves with JARVIS's real voice,
     turns gold while listening and spins up a scanner while thinking. */
  const drawVisualizer = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const TAU = Math.PI * 2;
    const N = 128; // points on the voice ring
    const wave = new Float32Array(N);
    const ripples = [];
    const sparks = Array.from({ length: 48 }, () => ({
      a: Math.random() * TAU,
      r: 0.64 + Math.random() * 0.32,
      s: 0.002 + Math.random() * 0.006,
      z: Math.random(),
    }));
    let t = 0;
    let level = 0;
    let slow = 0;
    let lastRipple = 0;
    let hue = 200;

    const render = (now = 0) => {
      rafRef.current = requestAnimationFrame(render);
      if (document.hidden) return;
      t += 0.016;

      // crisp on retina, sized by CSS
      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const px = Math.round((canvas.clientWidth || 300) * dpr);
      if (canvas.width !== px) canvas.width = canvas.height = px;
      const S = canvas.width;
      const cx = S / 2;
      const cy = S / 2;
      const body = S / 2 / 1.44; // the reactor itself; the canvas overhangs it by 22% each side

      const listeningNow = listeningRef.current;
      const talkingNow = speakingRef.current;
      const thinkingNow = busyRef.current && !talkingNow;
      const voice = playerRef.current?.frequencyData(); // the real voice when the server voice plays
      const mic =
        listeningNow && analyserRef.current && dataRef.current
          ? (analyserRef.current.getByteFrequencyData(dataRef.current), dataRef.current)
          : null;
      const src = voice || mic;

      let target;
      if (src) {
        let sum = 0;
        const n = Math.min(src.length, 40);
        for (let i = 2; i < n; i++) sum += src[i];
        target = Math.min(1, (sum / ((n - 2) * 255)) * 1.9);
      } else if (talkingNow) {
        // browser voice has no audio data: a syllable-like envelope
        target = 0.3 + 0.4 * Math.abs(Math.sin(t * 9)) * Math.abs(Math.sin(t * 2.3 + 1)) + 0.12 * Math.random();
      } else if (thinkingNow) target = 0.14 + 0.06 * Math.sin(t * 5);
      else target = 0.05 + 0.035 * Math.sin(t * 1.3);
      level += (target - level) * (target > level ? 0.5 : 0.12);
      slow += (level - slow) * 0.05;

      // a shockwave on every punchy syllable
      if ((talkingNow || listeningNow) && level > 0.28 && level - slow > 0.12 && now - lastRipple > 170) {
        ripples.push({ r: body * 0.34, a: Math.min(0.8, level) });
        lastRipple = now;
      }

      const targetHue = listeningNow ? 40 : 200; // gold while listening, arc blue otherwise
      hue += (targetHue - hue) * 0.08;
      const sat = 100 - Math.max(0, 200 - hue) * 0.15;
      const col = (l, a) => `hsla(${hue.toFixed(1)}, ${sat.toFixed(0)}%, ${l}%, ${a})`;

      // the DOM core follows the same level: it swells and trembles with the voice
      const el = reactorRef.current;
      if (el) {
        const shake = talkingNow ? level * 3.2 : 0;
        el.style.setProperty("--lvl", level.toFixed(3));
        el.style.setProperty("--jx", `${((Math.random() - 0.5) * shake).toFixed(2)}px`);
        el.style.setProperty("--jy", `${((Math.random() - 0.5) * shake).toFixed(2)}px`);
      }

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.clearRect(0, 0, S, S);
      if (talkingNow) {
        const j = level * S * 0.005; // the whole reactor vibrates while he speaks
        ctx.translate((Math.random() - 0.5) * j, (Math.random() - 0.5) * j);
      }
      ctx.globalCompositeOperation = "lighter";

      // halo
      const haloR = body * (1 + level * 0.55);
      const halo = ctx.createRadialGradient(cx, cy, body * 0.15, cx, cy, haloR);
      halo.addColorStop(0, col(62, 0.16 + level * 0.34));
      halo.addColorStop(1, col(50, 0));
      ctx.fillStyle = halo;
      ctx.beginPath();
      ctx.arc(cx, cy, haloR, 0, TAU);
      ctx.fill();

      // shockwaves
      for (let i = ripples.length - 1; i >= 0; i--) {
        const rp = ripples[i];
        rp.r += S * 0.0065;
        rp.a *= 0.935;
        if (rp.a < 0.02 || rp.r > S / 2) {
          ripples.splice(i, 1);
          continue;
        }
        ctx.strokeStyle = col(72, rp.a);
        ctx.lineWidth = S * 0.0035;
        ctx.beginPath();
        ctx.arc(cx, cy, rp.r, 0, TAU);
        ctx.stroke();
      }

      // voice ring: the spectrum wrapped around the core, mirrored left/right
      const live = talkingNow || listeningNow;
      for (let i = 0; i < N; i++) {
        const k = i < N / 2 ? i : N - 1 - i;
        const v = src
          ? src[2 + Math.floor((k / (N / 2)) * 34)] / 255
          : level * (0.55 + 0.45 * Math.sin(t * 6 + i * 0.45) * Math.sin(t * 2.1 + i * 0.12));
        wave[i] += (v - wave[i]) * 0.35;
      }
      const ringBase = body * 0.55;
      for (const [amp, alpha, lw, twist] of [
        [0.46, 0.95, 0.0055, 0],
        [0.3, 0.45, 0.0028, 0.5],
      ]) {
        ctx.beginPath();
        for (let i = 0; i <= N; i++) {
          const idx = i % N;
          const ang = (idx / N) * TAU - Math.PI / 2 + twist * Math.sin(t * 0.6);
          const r = ringBase + wave[idx] * body * amp * (live ? 1 : 0.35);
          const x = cx + Math.cos(ang) * r;
          const y = cy + Math.sin(ang) * r;
          if (i) ctx.lineTo(x, y);
          else ctx.moveTo(x, y);
        }
        ctx.closePath();
        ctx.strokeStyle = col(74, alpha);
        ctx.lineWidth = S * lw;
        ctx.shadowColor = col(60, 1);
        ctx.shadowBlur = S * 0.03;
        ctx.stroke();
      }
      ctx.shadowBlur = 0;

      // rotating segments: they spin up with the voice, and race while thinking
      const spin = t * (0.25 + level * 2.6 + (thinkingNow ? 1.8 : 0));
      for (const [rr, count, len, dir, lw] of [
        [0.8, 3, 0.6, 1, 0.004],
        [0.88, 6, 0.16, -0.7, 0.003],
        [0.97, 2, 1.1, 0.4, 0.0018],
      ]) {
        ctx.strokeStyle = col(66, 0.3 + level * 0.55);
        ctx.lineWidth = S * lw;
        for (let i = 0; i < count; i++) {
          const a0 = spin * dir + (i * TAU) / count;
          ctx.beginPath();
          ctx.arc(cx, cy, body * rr, a0, a0 + len);
          ctx.stroke();
        }
      }

      // thinking: a radar sweep
      if (thinkingNow) {
        const a = t * 3.2;
        for (let i = 0; i < 14; i++) {
          ctx.strokeStyle = col(70, 0.35 * (1 - i / 14));
          ctx.lineWidth = S * 0.006;
          ctx.beginPath();
          ctx.arc(cx, cy, body * 0.72, a - i * 0.05, a - i * 0.05 + 0.05);
          ctx.stroke();
        }
      }

      // orbiting sparks
      for (const p of sparks) {
        p.a += p.s * (1 + level * 7) * (p.z > 0.5 ? 1 : -1);
        const r = body * (p.r + level * 0.14 * p.z);
        ctx.fillStyle = col(82, (0.18 + 0.55 * p.z) * (0.35 + level));
        ctx.beginPath();
        ctx.arc(cx + Math.cos(p.a) * r, cy + Math.sin(p.a) * r, S * (0.002 + 0.003 * p.z), 0, TAU);
        ctx.fill();
      }

      // core bloom
      const coreR = body * (0.22 + level * 0.1);
      const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 2.3);
      g.addColorStop(0, `rgba(255, 255, 255, ${0.55 + level * 0.4})`);
      g.addColorStop(0.3, col(84, 0.55 + level * 0.3));
      g.addColorStop(0.65, col(60, 0.18 + level * 0.3));
      g.addColorStop(1, col(50, 0));
      ctx.fillStyle = g;
      ctx.beginPath();
      ctx.arc(cx, cy, coreR * 2.3, 0, TAU);
      ctx.fill();
      ctx.globalCompositeOperation = "source-over";
    };
    rafRef.current = requestAnimationFrame(render);
  };

  /* =========================
     SEND
     ========================= */
  const sendMessage = (override) => {
    const text = (override !== undefined ? override : input).trim();
    if (!text && !pendingImage) return;

    const isStop = isStopPhrase(text);
    // barge-in: talking over JARVIS silences him and abandons the unfinished answer
    stopAllSpeech();
    if (busyRef.current || speakingRef.current || isStop) sendJson({ type: "cancel" });

    const img = pendingImage;
    const sent = img
      ? sendJson({ type: "image", name: img.name, data: img.data, text })
      : sendJson({ type: "chat", text });
    if (!sent) {
      pushJarvis("Neural connection unavailable. Please start the JARVIS backend (python server.py).");
      addLog("Send failed: link offline");
      return;
    }
    setMessages((prev) => [
      ...prev,
      { from: "user", text, image: img?.thumb, id: `u-${Date.now()}-${Math.random()}` },
    ]);
    if (img) setPendingImage(null);
    if (!isStop) {
      sentAtRef.current = performance.now();
      inflightRef.current += 1;
      setBusy(true);
    }
    addLog("Command transmitted");
    setInput("");
    scrollToBottom();
  };
  sendMessageRef.current = sendMessage;

  const runCommand = (command) => sendMessage(command);
  const stopEverything = () => {
    stopAllSpeech();
    sendJson({ type: "cancel" });
    setBusy(false);
  };

  /* =========================
     UPLOADS
     ========================= */
  const readAndSend = (file, kind, okExt) => {
    const lower = file.name.toLowerCase();
    if (!okExt.some((ext) => lower.endsWith(ext))) {
      alert(`Unsupported file type. Supported: ${okExt.join(", ")}`);
      return;
    }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      alert(`That file is larger than ${MAX_UPLOAD_MB} MB.`);
      return;
    }
    if (!socketRef.current || socketRef.current.readyState !== WebSocket.OPEN) {
      pushJarvis("I can't receive files while the neural link is offline. Start the backend first.");
      return;
    }
    if (kind === "dataset") setDatasetStatus("UPLOADING...");
    addLog(`Uploading ${file.name}`);

    const reader = new FileReader();
    reader.onerror = () => {
      if (kind === "dataset") setDatasetStatus("NO DATASET");
      pushJarvis("I couldn't read that file from your disk.");
    };
    reader.onload = () => {
      if (!sendJson({ type: kind, name: file.name, data: reader.result })) {
        if (kind === "dataset") setDatasetStatus("NO DATASET");
        pushJarvis("The connection dropped during upload. Please try again.");
        return;
      }
      setMessages((prev) => [
        ...prev,
        {
          from: "user",
          text: `Uploaded ${kind}: ${file.name} (${(file.size / 1024 / 1024).toFixed(2)} MB)`,
          id: `up-${Date.now()}`,
        },
      ]);
      sentAtRef.current = performance.now();
      setBusy(true);
      scrollToBottom();
    };
    reader.readAsDataURL(file);
  };

  const openStudio = (chart) => {
    const schema = dataInfo?.schema || [];
    const nums = schema.filter((c) => c.kind === "numeric").map((c) => c.name);
    const dims = schema.filter((c) => ["categorical", "discrete", "datetime", "year"].includes(c.kind)).map((c) => c.name);
    const base = chart.spec || {
      kind: "bar",
      x: dims[0] || schema[0]?.name || "",
      y: dataInfo?.main_metric || nums[0] || "",
      agg: "sum",
      top: 12,
      sort: "desc",
      palette: "stark",
      color: "",
      title: chart.title || "",
      values: false,
      theme: "dark",
    };
    setStudio({ chart, spec: { ...base, color: base.color || "" }, busy: false, error: hasData ? "" : "Load a dataset to edit charts." });
  };
  const studioSet = (key, value) => setStudio((st) => ({ ...st, spec: { ...st.spec, [key]: value } }));
  const applyStudio = () => {
    if (!studio) return;
    const spec = { ...studio.spec, color: studio.spec.color || null, top: Number(studio.spec.top) || 0 };
    if (spec.y === "__count__") {
      spec.y = null;
      spec.agg = "count";
    }
    if (!sendJson({ type: "chart_edit", spec, replace: studio.chart.id })) {
      setStudio((st) => ({ ...st, error: "Not connected to JARVIS." }));
      return;
    }
    setStudio((st) => ({ ...st, busy: true, error: "" }));
  };

  const attachImage = (file) => {
    if (!file) return;
    if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
      alert(`That picture is larger than ${MAX_IMAGE_MB} MB.`);
      return;
    }
    const reader = new FileReader();
    reader.onload = async () => {
      const thumb = await makeThumb(reader.result);
      const data = await makeThumb(reader.result, 1600);
      setPendingImage({ name: file.name || "pasted.png", data, thumb });
      addLog(`Picture attached: ${file.name || "pasted image"}`);
    };
    reader.readAsDataURL(file);
  };
  const handleImage = (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) attachImage(f);
  };
  const handlePaste = (e) => {
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
    if (item) {
      e.preventDefault();
      attachImage(item.getAsFile());
    }
  };
  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    for (const f of [...(e.dataTransfer?.files || [])]) {
      const n = f.name.toLowerCase();
      if (IMG_EXT.some((x) => n.endsWith(x)) || f.type.startsWith("image/")) attachImage(f);
      else if (DATA_EXT.some((x) => n.endsWith(x))) readAndSend(f, "dataset", DATA_EXT);
      else if (DOC_EXT.some((x) => n.endsWith(x))) readAndSend(f, "document", DOC_EXT);
      else pushJarvis(`I can't read ${f.name}. I accept pictures, datasets and documents.`);
    }
  };

  const handleDataset = (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) readAndSend(f, "dataset", DATA_EXT);
  };
  const handleDocument = (e) => {
    const f = e.target.files?.[0];
    e.target.value = "";
    if (f) readAndSend(f, "document", DOC_EXT);
  };

  /* =========================
     MISC
     ========================= */
  const scrollToBottom = () => {
    setTimeout(() => {
      chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
    }, 50);
  };

  const cycleMode = () => {
    const next = MODES[(MODES.indexOf(aiMode) + 1) % MODES.length];
    if (!sendJson({ type: "mode", mode: next.toLowerCase() })) setAiMode(next);
  };

  const setSetting = (key, value) => {
    setSettings((s) => ({ ...s, [key]: value })); // optimistic; the server confirms
    if (key === "lang") {
      try {
        localStorage.setItem("jarvis.lang", value);
      } catch {}
    }
    sendJson({ type: "settings", [key]: value });
    if (key === "voice" && !value) stopAllSpeech();
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      sendMessage();
    }
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "m") {
      event.preventDefault();
      toggleMic();
    }
  };

  const online = connection === "ONLINE";
  const active = speaking || listening || handsAwake || busy;
  const hasData = !!dataInfo;
  const qualityTone = !dataInfo ? "" : dataInfo.quality >= 85 ? "green" : dataInfo.quality >= 65 ? "gold" : "red";
  const voiceLabel = info.voice?.enabled ? info.voice.label : "browser voice";

  /* =========================
     BOOT SCREEN
     ========================= */
  if (booting) {
    return (
      <div className="boot-screen">
        <div className="boot-reactor">
          <span />
          <span />
          <span />
          <i />
        </div>
        <div className="boot-logo">JARVIS</div>
        <div className="boot-lines">
          {BOOT_LINES.map((line, i) => (
            <div key={line} className="boot-line" style={{ animationDelay: `${0.3 + i * 0.5}s` }}>
              <span className="boot-ok">OK</span> {line}
            </div>
          ))}
        </div>
        <div className="boot-progress">
          <div />
        </div>
        {bootReady && (
          <button type="button" className="init-button" onClick={activate} autoFocus>
            Start JARVIS
          </button>
        )}
      </div>
    );
  }

  /* =========================
     MAIN UI
     ========================= */
  return (
    <div className="app">
      <div className="background-grid" />
      <div className="ambient-glow" />
      <div className="scanline" />
      <div className="vignette" />

      <header className="topbar">
        <button type="button" className="icon-btn menu-btn" onClick={() => setLeftOpen((v) => !v)} aria-label="Chats">
          ☰
        </button>
        <div className="brand-block">
          <span className={`brand-mark ${active ? "live" : ""}`}>
            <i />
          </span>
          <div>
            <div className="brand">JARVIS</div>
            <div className="subtitle">{subtitle}</div>
          </div>
        </div>

        <div className="top-pills">
          <span className={`pill ${online ? "ok" : "bad"}`} title="Connection to the JARVIS backend">
            <i /> {online ? (info.llm ? info.model.replace(/-Instruct|-4bit|-8bit/g, "") || "Online" : "Online · no model") : "Offline"}
          </span>
          <button
            type="button"
            className="pill pill-btn"
            title="Reply language: Auto follows the language you speak"
            onClick={() => setSetting("lang", { auto: "en", en: "hi", hi: "auto" }[settings.lang] || "auto")}
          >
            🌐 {LANG_LABEL[settings.lang] || "AUTO"}
          </button>
          {info.cloudReady && (
            <button
              type="button"
              className={`pill pill-btn${settings.cloud ? " pill-cloud" : ""}`}
              onClick={() => setSetting("cloud", !settings.cloud)}
              title={settings.cloud ? "Using the cloud brain. Click for the local brain" : "Using the local brain. Click for the cloud brain"}
            >
              {settings.cloud ? "☁ Cloud" : "⌂ Local"}
            </button>
          )}
          <button type="button" className="pill pill-btn" onClick={cycleMode} title="Change tone">
            {aiMode.charAt(0) + aiMode.slice(1).toLowerCase()}
          </button>
        </div>

        <div className="topbar-right">
          <div className="clock">
            {time}
            <span>{date}</span>
          </div>
          <button
            type="button"
            className={`icon-btn ${rightOpen ? "on" : ""}`}
            onClick={() => setRightOpen((v) => !v)}
            title="Show or hide the control panel"
            aria-label="Control panel"
          >
            ◫
          </button>
        </div>
      </header>

      <main className={`main-grid ${rightOpen ? "" : "no-right"} ${leftOpen ? "left-open" : ""}`}>
        {/* LEFT RAIL */}
        <aside className="side-panel left-panel">
          <div className="rail-tabs" role="tablist">
            <button type="button" className={leftTab === "chats" ? "on" : ""} onClick={() => setLeftTab("chats")}>
              Chats
            </button>
            <button type="button" className={leftTab === "tools" ? "on" : ""} onClick={() => setLeftTab("tools")}>
              Data &amp; Docs
            </button>
          </div>

          {leftTab === "chats" && (
            <section className="hud-panel chats-panel">
              <button type="button" className="new-chat" onClick={startNewChat}>
                <span>＋</span> New chat
              </button>
              <div className="chat-list">
                {!chats.some((c) => c.id === activeChat) && (
                  <div className="chat-item active">
                    <div className="chat-item-main">
                      <b>New chat</b>
                      <small>empty</small>
                    </div>
                  </div>
                )}
                {chats.map((c) => (
                  <div
                    key={c.id}
                    className={`chat-item ${c.id === activeChat ? "active" : ""}`}
                    onClick={() => openChat(c.id)}
                  >
                    <div className="chat-item-main">
                      {renaming === c.id ? (
                        <input
                          autoFocus
                          defaultValue={c.title}
                          onClick={(e) => e.stopPropagation()}
                          onBlur={(e) => renameChat(c.id, e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") renameChat(c.id, e.target.value);
                            if (e.key === "Escape") setRenaming(null);
                          }}
                        />
                      ) : (
                        <b title={c.title}>{c.title}</b>
                      )}
                      <small>
                        {(c.messages || []).filter((m) => m.from === "user").length} msgs · {timeAgo(c.updated)}
                      </small>
                    </div>
                    <div className="chat-item-actions">
                      <button
                        type="button"
                        title="Rename"
                        onClick={(e) => {
                          e.stopPropagation();
                          setRenaming(c.id);
                        }}
                      >
                        ✎
                      </button>
                      <button
                        type="button"
                        title="Delete"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (window.confirm("Delete this chat?")) deleteChat(c.id);
                        }}
                      >
                        ✕
                      </button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="chat-foot">
                <button type="button" onClick={exportChat} disabled={!messages.length}>
                  ⬇ Export chat
                </button>
                <small>Saved on this computer only</small>
              </div>
            </section>
          )}

          {leftTab === "tools" && (
          <>
          <section className="hud-panel data-panel">
            <div className="panel-title">Data</div>
            <div className="data-subtitle" title={dataInfo?.name}>
              {hasData ? dataInfo.name : "No dataset loaded"}
            </div>

            {hasData && (
              <>
                <div className="data-facts">
                  <span>{dataInfo.rows.toLocaleString()} rows</span>
                  <span>{dataInfo.columns} cols</span>
                  <span className={qualityTone}>quality {dataInfo.quality}/100</span>
                </div>

                <div className={`mode-chip ${dataInfo.mode}`}>
                  {dataInfo.mode === "business" ? "Business mode" : "Profile mode · no revenue or profit column"}
                </div>

                {dataInfo.role_info?.length > 0 && (
                  <div className="role-list" title="How I read your columns">
                    {dataInfo.role_info.map((r) => (
                      <span
                        key={r.role}
                        className={`role-chip ${r.confidence}`}
                        title={
                          r.confidence === "medium"
                            ? "Weak match: assumed from the column name"
                            : r.confidence === "you"
                            ? "Set by you"
                            : "Matched by column name"
                        }
                      >
                        {r.role} → {r.column}
                      </span>
                    ))}
                  </div>
                )}
                <div className="role-hint">Wrong? Type: use &lt;column&gt; as revenue</div>

                <div className="kpi-grid">
                  {dataInfo.kpis.map((k) => (
                    <div className="kpi-tile" key={k.label}>
                      <b>{k.value}</b>
                      <span>{k.label}</span>
                    </div>
                  ))}
                </div>
              </>
            )}

            <label className="upload-button">
              <span>⬆</span> Upload dataset
              <input type="file" accept={DATA_EXT.join(",")} onChange={handleDataset} hidden />
            </label>

            <div className="data-actions">
              {ACTIONS.map(([label, command]) => (
                <button key={label} type="button" disabled={!hasData || busy} onClick={() => runCommand(command)}>
                  {label}
                </button>
              ))}
            </div>

            <div className="dataset-status">
              <span className={hasData ? "status-dot active" : "status-dot"} />
              {datasetStatus}
            </div>
          </section>

          <section className="hud-panel doc-panel">
            <div className="panel-title">Documents</div>
            {docs.length === 0 ? (
              <div className="data-subtitle">Upload a PDF, Word or text file, then ask about it.</div>
            ) : (
              <ul className="doc-list">
                {docs.map((d) => (
                  <li key={d.name} title={d.name}>
                    <b>{d.name}</b>
                    <span>
                      {d.words?.toLocaleString()} words{d.pages ? ` · ${d.pages} pp` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <label className="upload-button">
              <span>⬆</span> Upload document
              <input type="file" accept={DOC_EXT.join(",")} onChange={handleDocument} hidden />
            </label>
            {docs.length > 0 && (
              <div className="data-actions">
                <button type="button" disabled={busy} onClick={() => runCommand("Summarize the document")}>
                  Summarise
                </button>
                <button type="button" disabled={busy} onClick={() => runCommand("Clear documents")}>
                  Clear all
                </button>
              </div>
            )}
          </section>

          <section className="hud-panel">
            <div className="panel-title">System</div>
            <Metric label="CPU" value={cpu == null ? "—" : `${cpu}%`} pct={cpu ?? undefined} />
            <Metric label="Memory" value={memory == null ? "—" : `${memory}%`} pct={memory ?? undefined} />
            {battery && (
              <Metric
                label="Battery"
                value={`${battery.level}%${battery.charging ? " ⚡" : ""}`}
                pct={battery.level}
                tone={battery.level <= 20 && !battery.charging ? "red" : ""}
              />
            )}
            <Metric label="Network" value={online ? "ACTIVE" : "DOWN"} tone={online ? "green" : "red"} />
            <Metric label="Response" value={responseTime} />
          </section>
          </>
          )}
        </aside>

        {/* CENTER STAGE */}
        <section className={`center-stage ${messages.length || busy ? "compact" : ""}`}>
          <div className="reactor-wrap">
            <div ref={reactorRef} className={`reactor ${active ? "reactor-active" : ""} ${speaking ? "reactor-speaking" : ""} ${listening || handsAwake ? "reactor-listening" : ""} ${busy && !speaking ? "reactor-thinking" : ""}`}>
              <div className="reactor-ring ring-ticks" />
              <div className="reactor-ring ring-one" />
              <div className="reactor-ring ring-two" />
              <div className="reactor-ring ring-three" />
              <div className="reactor-pulse" />
              <canvas ref={canvasRef} width="480" height="480" className="reactor-canvas" />
              <div className="core">
                <div className="core-inner" />
                <div className="core-text">
                  {listening || handsAwake ? "LISTENING" : busy ? "PROCESSING" : speaking ? "SPEAKING" : "STANDBY"}
                </div>
              </div>
            </div>
          </div>

          <div className="reactor-status">
            <span className={active ? "status-dot active" : "status-dot"} />
            <span>
              {listening || handsAwake
                ? "Listening…"
                : busy
                ? statusText || "Thinking…"
                : speaking
                ? "Speaking"
                : handsFree
                ? micLive
                  ? "Say “Jarvis…”"
                  : "Mic reconnecting…"
                : "Ready when you are"}
            </span>
            <span className={`wave ${active ? "wave-active" : ""}`}>
              <i />
              <i />
              <i />
              <i />
              <i />
            </span>
          </div>
          {caption && (
            <div className="live-caption" aria-live="polite">
              <span className="live-caption-mark">🎙</span>
              {caption}
            </div>
          )}

          {/* CHAT */}
          <section
            className={`chat-container hud-panel ${dragging ? "drop-active" : ""}`}
            ref={chatRef}
            onDragOver={(e) => {
              e.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
          >
            {dragging && <div className="drop-hint">DROP A PICTURE, DATASET OR DOCUMENT</div>}
            {messages.length === 0 && !busy && (
              <div className="empty-chat">
                <h2>
                  {new Date().getHours() < 12 ? "Good morning" : new Date().getHours() < 17 ? "Good afternoon" : "Good evening"},{" "}
                  {info.title || "sir"}.
                </h2>
                <p>Ask me anything in English or हिंदी. Drop in a picture, a dataset or a document, or turn on Hands-free and just say “Jarvis”.</p>
                <div className="quick-grid">
                  {QUICK_STARTS.map(([icon, label, cmd]) => (
                    <button key={label} type="button" className="quick-card" onClick={() => runCommand(cmd)}>
                      <span>{icon}</span>
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m) => {
              const isStreaming = m.id === streamingId;
              const rich = m.charts?.length > 0;
              const links = (m.sources || []).map((s, i) => ({ ...s, n: i + 1 }));
              return (
                <div key={m.id} className={`message-row ${m.from}`}>
                  <div className="message-label">{m.from === "user" ? "YOU" : "JARVIS"}</div>
                  <div className={`message ${m.from} ${rich ? "rich" : ""} ${isStreaming ? "typing" : ""}`}>
                    {rich && (
                      <div className="chart-grid">
                        {m.charts.map((c) => (
                          <figure className="chart-card" key={c.id}>
                            <img
                              src={c.image}
                              alt={c.title}
                              loading="lazy"
                              onLoad={scrollToBottom}
                              onClick={() => setLightbox(c)}
                            />
                            <figcaption>
                              <div>
                                <b>{c.title}</b>
                                {c.caption && <span>{c.caption}</span>}
                              </div>
                              <button type="button" className="chart-edit" onClick={() => openStudio(c)} title="Edit this chart">
                                ✎ Edit
                              </button>
                            </figcaption>
                          </figure>
                        ))}
                      </div>
                    )}

                    {m.image && (
                      <img className="msg-image" src={m.image} alt="attached" onClick={() => setLightbox({ image: m.image, title: "Picture" })} />
                    )}

                    {(m.text || isStreaming) &&
                      (m.from === "user" ? (
                        <div className="message-text">{m.text}</div>
                      ) : isTable(m.text) ? (
                        <div className="message-text mono-block">{m.text}</div>
                      ) : (
                        <div className="message-text">
                          <Markdown text={m.text || ""} sources={m.sources} />
                          {isStreaming && <span className="cursor">▌</span>}
                        </div>
                      ))}

                    {links.length > 0 && (
                      <div className="source-row">
                        <span className="source-label">SOURCES</span>
                        {links.map((s) =>
                          s.url ? (
                            <a key={s.n} className="source-chip" href={s.url} target="_blank" rel="noopener noreferrer" title={s.title}>
                              <b>{s.n}</b>
                              {hostOf(s.url) || s.title}
                            </a>
                          ) : (
                            <span key={s.n} className="source-chip" title={s.title}>
                              <b>{s.n}</b>
                              {s.title}
                            </span>
                          )
                        )}
                      </div>
                    )}

                    {m.suggestions?.length > 0 && !busy && m.id === messages[messages.length - 1]?.id && (
                      <div className="chip-row">
                        {m.suggestions.map((sg) => (
                          <button key={sg} type="button" className="chip" onClick={() => runCommand(sg)}>
                            {sg}
                          </button>
                        ))}
                      </div>
                    )}

                    {m.files?.length > 0 && (
                      <div className="file-row">
                        {m.files.map((f) => (
                          <a key={f.name} className="file-chip" href={dataUrl(f.data, f.mime)} download={f.name}>
                            ⬇ {f.name}
                          </a>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}

            {busy && !streamingId && (
              <div className="message-row jarvis">
                <div className="message-label">JARVIS</div>
                <div className="message jarvis thinking">
                  <span />
                  <span />
                  <span />
                  {statusText && <em>{statusText}</em>}
                </div>
              </div>
            )}
          </section>

          {/* COMMAND CENTER */}
          {pendingImage && (
            <div className="attach-preview">
              <img src={pendingImage.thumb} alt="to send" />
              <div>
                <b>{pendingImage.name}</b>
                <small>Ask something about it, or press EXECUTE to describe it</small>
              </div>
              <button type="button" onClick={() => setPendingImage(null)} title="Remove picture">
                ✕
              </button>
            </div>
          )}
          <footer className="command-center">
            <input ref={imageInputRef} type="file" accept="image/*" hidden onChange={handleImage} />
            <button
              type="button"
              className="tool-button"
              onClick={() => imageInputRef.current?.click()}
              title="Attach a picture (or paste / drop one)"
              aria-label="Attach a picture"
            >
              🖼
            </button>
            <button
              type="button"
              className="tool-button"
              onClick={() => runCommand("What's on my screen?")}
              title="Look at my screen"
              aria-label="Look at my screen"
            >
              👁
            </button>
            <button
              type="button"
              className={`mic-button ${listening ? "mic-active" : ""}`}
              onClick={toggleMic}
              title="Push to talk (Space, or Ctrl+M while typing)"
              aria-label="Push to talk"
            >
              {listening ? "■" : "🎙"}
            </button>
            <input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              onPaste={handlePaste}
              placeholder={pendingImage ? "Ask about this picture..." : hasData ? "Ask about your data, or anything else..." : "Command JARVIS..."}
              aria-label="Command input"
              autoComplete="off"
              spellCheck="false"
            />
            {(busy || speaking) && (
              <button type="button" className="stop-button" onClick={stopEverything} title="Stop talking and thinking">
                ■ Stop
              </button>
            )}
            <button type="button" className="execute-button" onClick={() => sendMessage()}>
              ➤
            </button>
          </footer>
        </section>

        {/* RIGHT RAIL */}
        <aside className="side-panel right-panel">
          <section className="hud-panel">
            <div className="panel-title">Status</div>
            <div className="ai-status">
              <div className={`status-orb ${online && info.llm ? "ok" : online ? "warn" : "bad"}`} />
              <div>
                <strong>Cognitive core</strong>
                <span>{!online ? "Offline" : info.llm ? "Ready" : "No language model"}</span>
              </div>
            </div>
            <Metric label="Model" value={info.model ? info.model.replace(/-Instruct|-4bit|-8bit/g, "") : "none"} />
            <Metric label="Tone" value={aiMode} tone="gold" />
            <Metric label="Voice" value={voiceLabel.replace("edge · ", "").replace("macOS say · ", "")} />
          </section>

          <section className="hud-panel controls-panel">
            <div className="panel-title">Controls</div>
            <Toggle
              label="Voice"
              on={settings.voice}
              onClick={() => setSetting("voice", !settings.voice)}
              hint="JARVIS speaks his answers"
            />
            <Toggle
              label="Voice effects"
              on={fxOn}
              onClick={() => setFxOn((v) => !v)}
              hint="Adds the JARVIS EQ, digital double and room"
            />
            <Toggle
              label="Hands-free"
              on={handsFree}
              onClick={() => setHandsFree((v) => !v)}
              hint='Always listening. Say "Jarvis, ..."'
            />
            <Toggle
              label="Say “stop” to interrupt"
              on={voiceStop || handsFree}
              onClick={() => setVoiceStop((v) => !v)}
              disabled={handsFree}
              hint="Listens for stop, chup or bas karo while he talks. Turn off if your mic misbehaves"
            />
            <Toggle
              label="Cloud brain"
              on={settings.cloud && !!info.cloudReady}
              onClick={() => setSetting("cloud", !settings.cloud)}
              disabled={!info.cloudReady}
              hint={info.cloudReady ? "Smarter answers. Chats are sent to the cloud" : "Add GEMINI_API_KEY to backend/.env"}
            />
            <Toggle
              label="Deep think"
              on={settings.deep}
              onClick={() => setSetting("deep", !settings.deep)}
              hint="Reason step by step before answering"
            />
            <Toggle
              label="Web access"
              on={settings.web}
              onClick={() => setSetting("web", !settings.web)}
              disabled={!info.web}
              hint="Search the web, news, weather and currency"
            />
          </section>

          {timers.length > 0 && (
            <section className="hud-panel">
              <div className="panel-title">Timers</div>
              <ul className="timer-list">
                {timers.map((t) => (
                  <li key={t.id}>
                    <span className="timer-time">{fmtClock(t.endsAt - Date.now())}</span>
                    <span className="timer-label">{t.label || "timer"}</span>
                    <button type="button" onClick={() => cancelTimer(t.id)} aria-label="Cancel timer">
                      ✕
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          <section className="hud-panel log-panel">
            <div className="panel-title">Activity</div>
            <ul className="log-list">
              {logs.length === 0 && <li>Awaiting events...</li>}
              {logs.map((line, i) => (
                <li key={`${line}-${i}`}>{line}</li>
              ))}
            </ul>
          </section>
        </aside>
      </main>

      {studio && (
        <div className="studio-backdrop" onClick={() => setStudio(null)}>
          <div className="studio" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Chart studio">
            <div className="studio-head">
              <div>
                <b>Chart studio</b>
                <span>Change anything, then press Apply</span>
              </div>
              <button type="button" className="icon-btn" onClick={() => setStudio(null)} aria-label="Close">
                ✕
              </button>
            </div>
            <div className="studio-body">
              <div className="studio-preview">
                <img src={studio.chart.image} alt={studio.chart.title} />
                {studio.busy && <div className="studio-busy">Redrawing…</div>}
              </div>
              <div className="studio-form">
                <label>Chart type</label>
                <div className="seg">
                  {CHART_KINDS.map(([k, label]) => (
                    <button key={k} type="button" className={studio.spec.kind === k ? "on" : ""} onClick={() => studioSet("kind", k)}>
                      {label}
                    </button>
                  ))}
                </div>
                <div className="row2">
                  <div>
                    <label>Group by (X)</label>
                    <select value={studio.spec.x || ""} onChange={(e) => studioSet("x", e.target.value)}>
                      {(dataInfo?.schema || []).map((c) => (
                        <option key={c.name} value={c.name}>
                          {c.name}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label>Metric (Y)</label>
                    <select
                      value={!studio.spec.y && studio.spec.agg === "count" ? "__count__" : studio.spec.y || ""}
                      onChange={(e) => studioSet("y", e.target.value)}
                    >
                      <option value="__count__">Number of rows</option>
                      {(dataInfo?.schema || [])
                        .filter((c) => c.kind === "numeric" || c.kind === "discrete")
                        .map((c) => (
                          <option key={c.name} value={c.name}>
                            {c.name}
                          </option>
                        ))}
                    </select>
                  </div>
                </div>
                <div className="row2">
                  <div>
                    <label>Calculation</label>
                    <select value={studio.spec.agg || "sum"} onChange={(e) => studioSet("agg", e.target.value)}>
                      <option value="sum">Total</option>
                      <option value="mean">Average</option>
                      <option value="median">Median</option>
                      <option value="count">Count</option>
                      <option value="max">Highest</option>
                      <option value="min">Lowest</option>
                    </select>
                  </div>
                  <div>
                    <label>Order</label>
                    <select value={studio.spec.sort || "desc"} onChange={(e) => studioSet("sort", e.target.value)}>
                      <option value="desc">Largest first</option>
                      <option value="asc">Smallest first</option>
                      <option value="none">Natural order</option>
                    </select>
                  </div>
                </div>
                <label>
                  Show top <b>{Number(studio.spec.top) || "all"}</b>
                </label>
                <input type="range" min="0" max="30" value={Number(studio.spec.top) || 0} onChange={(e) => studioSet("top", e.target.value)} />
                <label>Colours</label>
                <div className="swatches">
                  {Object.entries(PALETTES).map(([name, cols]) => (
                    <button
                      key={name}
                      type="button"
                      title={name}
                      className={`swatch ${studio.spec.palette === name && !studio.spec.color ? "on" : ""}`}
                      onClick={() => setStudio((st) => ({ ...st, spec: { ...st.spec, palette: name, color: "" } }))}
                    >
                      {cols.map((c) => (
                        <i key={c} style={{ background: c }} />
                      ))}
                    </button>
                  ))}
                  <label className={`swatch custom ${studio.spec.color ? "on" : ""}`} title="Pick any colour">
                    <input type="color" value={studio.spec.color || "#5cc8ff"} onChange={(e) => studioSet("color", e.target.value)} />
                    Custom
                  </label>
                </div>
                <label>Title</label>
                <input type="text" value={studio.spec.title || ""} placeholder="Automatic" onChange={(e) => studioSet("title", e.target.value)} />
                <div className="row2">
                  <div>
                    <label>X axis label</label>
                    <input type="text" value={studio.spec.xlabel || ""} onChange={(e) => studioSet("xlabel", e.target.value)} />
                  </div>
                  <div>
                    <label>Y axis label</label>
                    <input type="text" value={studio.spec.ylabel || ""} onChange={(e) => studioSet("ylabel", e.target.value)} />
                  </div>
                </div>
                <div className="row2 toggles">
                  <Toggle label="Value labels" on={!!studio.spec.values} onClick={() => studioSet("values", !studio.spec.values)} />
                  <Toggle
                    label="Light background"
                    on={studio.spec.theme === "light"}
                    onClick={() => studioSet("theme", studio.spec.theme === "light" ? "dark" : "light")}
                  />
                </div>
                {studio.error && <div className="studio-error">{studio.error}</div>}
              </div>
            </div>
            <div className="studio-foot">
              <a className="ghost-btn" href={studio.chart.image} download={`${studio.chart.title || "chart"}.png`}>
                ⬇ Download PNG
              </a>
              <div>
                <button type="button" className="ghost-btn" onClick={() => setStudio(null)}>
                  Close
                </button>
                <button type="button" className="primary-btn" onClick={applyStudio} disabled={studio.busy || !hasData}>
                  {studio.busy ? "Applying…" : "Apply"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {lightbox && (
        <div className="lightbox" onClick={() => setLightbox(null)} role="dialog" aria-label={lightbox.title}>
          <div className="lightbox-inner" onClick={(e) => e.stopPropagation()}>
            <div className="lightbox-bar">
              <span>{lightbox.title}</span>
              <div>
                <a href={lightbox.image} download={`${lightbox.title}.png`}>
                  ⬇ SAVE PNG
                </a>
                <button type="button" onClick={() => setLightbox(null)}>
                  ✕
                </button>
              </div>
            </div>
            <img src={lightbox.image} alt={lightbox.title} />
            {lightbox.caption && <div className="lightbox-caption">{lightbox.caption}</div>}
          </div>
        </div>
      )}
    </div>
  );
}
