"""
JARVIS configuration.
Everything you might want to tweak lives here. Override any value with an environment variable, e.g.

    JARVIS_MODEL="mlx-community/Qwen2.5-14B-Instruct-4bit" python server.py
    JARVIS_VOICE_ENGINE=say python server.py
"""

import os
import subprocess
import sys
from pathlib import Path


def _load_dotenv():
    """Read backend/.env (KEY=VALUE per line) so secrets such as the ElevenLabs key stay out of the code.
    Real environment variables always win. Never commit the .env file."""
    path = Path(__file__).resolve().parent / ".env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except FileNotFoundError:
        pass


_load_dotenv()


# ------------------------------------------------------------
# HARDWARE-AWARE MODEL CHOICE
# ------------------------------------------------------------
def _ram_gb() -> float:
    try:
        if sys.platform == "darwin":
            return int(subprocess.check_output(["sysctl", "-n", "hw.memsize"]).strip()) / 1e9
        import psutil  # optional
        return psutil.virtual_memory().total / 1e9
    except Exception:
        return 8.0


RAM_GB = _ram_gb()

# Bigger model = noticeably smarter. The default is picked from your Mac's memory, and you can override it.
#   >= 48 GB : Qwen2.5 32B     >= 24 GB : Qwen2.5 14B     >= 12 GB : Qwen2.5 7B     otherwise : Qwen2.5 3B
# Qwen3 is the newer family: better reasoning, coding and tool use than Qwen2.5 at the same size.
# The 30B-A3B model is a "mixture of experts": 30B of knowledge but only 3B active per word,
# so it answers about as fast as a 3B model. Override any choice with JARVIS_MODEL in .env.
_AUTO_MODELS = [
    (32, "mlx-community/Qwen3-30B-A3B-4bit"),
    (24, "mlx-community/Qwen3-14B-4bit"),
    (16, "mlx-community/Qwen3-8B-4bit"),
    (0, "mlx-community/Qwen3-4B-4bit"),
]


def _auto_model() -> str:
    for need, name in _AUTO_MODELS:
        if RAM_GB >= need:
            return name
    return _AUTO_MODELS[-1][1]


MODEL_NAME = os.getenv("JARVIS_MODEL") or _auto_model()

# Qwen3 can "think out loud" before answering. Off by default so replies stay fast;
# set JARVIS_THINKING=1 for slower, more careful answers.
THINKING = os.getenv("JARVIS_THINKING", "0") == "1"
DEEP_THINK_TOKENS = int(os.getenv("JARVIS_DEEP_THINK_TOKENS", "1500"))   # extra room for private thinking in deep mode

# Vision model for pictures and "what's on my screen". Loaded only the first time it is needed.
_VISION_MODELS = [
    (32, "mlx-community/Qwen2.5-VL-7B-Instruct-4bit"),   # 5.3 GB; only with lots of RAM next to the chat model
    (0, "mlx-community/Qwen2.5-VL-3B-Instruct-4bit"),    # about 2 GB
]
VISION_MODEL = os.getenv("JARVIS_VISION_MODEL") or next(n for need, n in _VISION_MODELS if RAM_GB >= need)
VISION_PRELOAD = os.getenv("JARVIS_VISION_PRELOAD", "1") == "1"    # load the vision model in the background at startup
VISION_LOCAL_SIZE = int(os.getenv("JARVIS_VISION_SIZE", "1024"))    # longest side the local vision model sees
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".heic"}

CHAT_MAX_TOKENS = int(os.getenv("JARVIS_CHAT_TOKENS", "700"))
STORY_MAX_TOKENS = 1100
DEEP_NOTES_TOKENS = 300
ADVICE_MAX_TOKENS = 600
TEMPERATURE = float(os.getenv("JARVIS_TEMPERATURE", "0.7"))   # Qwen3's recommended settings for chat
TOP_P = 0.8
TOP_K = 20
REPETITION_PENALTY = float(os.getenv("JARVIS_REPETITION_PENALTY", "1.1"))   # stops him repeating the same line
MAX_HISTORY_MESSAGES = 14          # older turns are summarised, not dropped
CONTEXT_CHARS = 14000              # rough budget for history + retrieved context

# ------------------------------------------------------------
# PERSONA
# ------------------------------------------------------------
USER_NAME = os.getenv("JARVIS_USER_NAME", "Tejas")   # who usually talks to JARVIS (his creator); change if that is not you
USER_TITLE = os.getenv("JARVIS_TITLE", "sir")      # how JARVIS addresses you: sir, boss, ma'am ...
HOME_CITY = os.getenv("JARVIS_CITY", "")           # default city for "what's the weather"

# ------------------------------------------------------------
# VOICE
# ------------------------------------------------------------
# auto: Kokoro (offline neural, best) -> edge-tts (online neural) -> macOS "say" -> browser voice
# ---------- cloud brain (optional): Claude through the Anthropic API ----------
# Put ANTHROPIC_API_KEY in backend/.env. Cloud mode sends your messages to Anthropic and costs money per use.
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
CLOUD_MODEL = os.getenv("JARVIS_CLOUD_MODEL", "claude-opus-5-5")
CLOUD_EFFORT = os.getenv("JARVIS_CLOUD_EFFORT", "medium")          # low = faster and cheaper, high = most careful
CLOUD_MAX_TOKENS = int(os.getenv("JARVIS_CLOUD_TOKENS", "4000"))
CLOUD_DEFAULT = os.getenv("JARVIS_BRAIN", "local").lower() == "cloud"   # start with the cloud brain switched on

# ---------- free cloud brain: Google Gemini ----------
# Free key (with daily limits) from https://aistudio.google.com/apikey -> GEMINI_API_KEY in backend/.env.
# Free-tier chats may be used by Google to improve its products.
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("JARVIS_GEMINI_MODEL", "gemini-3.8-flash")
# ---------- free cloud brain: Groq (very fast) ----------
# Free key (with daily limits) from https://console.groq.com/keys -> GROQ_API_KEY in backend/.env.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.getenv("JARVIS_GROQ_MODEL", "openai/gpt-oss-120b")
# which cloud brain to use when several keys are set: auto (Claude, then Groq, then Gemini) | claude | groq | gemini
CLOUD_PROVIDER = os.getenv("JARVIS_CLOUD_PROVIDER", "auto").lower()

# Hearing (speech to text): auto = Whisper on this Mac when mlx-whisper is installed, chrome = the browser's own
HEARING_ENGINE = os.getenv("JARVIS_HEARING", "auto").lower()
STT_MODEL = os.getenv("JARVIS_STT_MODEL", "mlx-community/whisper-large-v3-turbo")   # about 1.6 GB, fast and good at Hindi

HINDI_STYLE = os.getenv("JARVIS_HINDI_STYLE", "roman").lower()   # roman = WhatsApp-style Hinglish, devanagari = हिंदी

MUSIC_SERVICE = os.getenv("JARVIS_MUSIC", "youtube").lower()   # where "play X" / "X bajao" goes: youtube | spotify

VOICE_ENGINE = os.getenv("JARVIS_VOICE_ENGINE", "auto")     # auto | elevenlabs | kokoro | edge | say | off

# ElevenLabs (used first whenever ELEVENLABS_API_KEY is set). The key goes in backend/.env or the environment.
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip()
ELEVEN_VOICE_ID = os.getenv("JARVIS_ELEVEN_VOICE_ID", "x3gYeuNB0kLLYxOZsaSh").strip()
ELEVEN_MODEL = os.getenv("JARVIS_ELEVEN_MODEL", "eleven_multilingual_v2")   # eleven_flash_v2_5 = fastest, eleven_multilingual_v2 = richest
ELEVEN_STABILITY = float(os.getenv("JARVIS_ELEVEN_STABILITY", "0.5"))        # higher = calmer and more even
ELEVEN_SIMILARITY = float(os.getenv("JARVIS_ELEVEN_SIMILARITY", "0.8"))      # higher = closer to the original voice
ELEVEN_STYLE = float(os.getenv("JARVIS_ELEVEN_STYLE", "0.0"))
ELEVEN_URL = os.getenv("JARVIS_ELEVEN_URL", "https://api.elevenlabs.io")
KOKORO_VOICE = os.getenv("JARVIS_KOKORO_VOICE", "bm_george")   # British male: bm_george, bm_fable, bm_lewis, bm_daniel
KOKORO_SPEED = float(os.getenv("JARVIS_KOKORO_SPEED", "0.94")) # a touch slower sounds more composed
EDGE_VOICE = os.getenv("JARVIS_EDGE_VOICE", "en-GB-RyanNeural")   # also try en-GB-ThomasNeural
EDGE_RATE = os.getenv("JARVIS_EDGE_RATE", "-4%")
EDGE_PITCH = os.getenv("JARVIS_EDGE_PITCH", "-6Hz")
EDGE_VOLUME = "+0%"
SAY_VOICE = os.getenv("JARVIS_SAY_VOICE", "Daniel")         # macOS British male

# Hindi voices (used automatically when the reply is in Hindi)
EDGE_VOICE_HI = os.getenv("JARVIS_EDGE_VOICE_HI", "hi-IN-MadhurNeural")   # male; female: hi-IN-SwaraNeural
KOKORO_VOICE_HI = os.getenv("JARVIS_KOKORO_VOICE_HI", "hm_omega")          # Hindi male; also hm_psi
SAY_VOICE_HI = os.getenv("JARVIS_SAY_VOICE_HI", "Lekha")                  # macOS Hindi voice
SAY_RATE = int(os.getenv("JARVIS_SAY_RATE", "168"))         # words per minute
SPEAK_MAX_SENTENCES = 7            # longer answers are summarised aloud and shown in full on screen

# ------------------------------------------------------------
# WEB
# ------------------------------------------------------------
WEB_ENABLED = os.getenv("JARVIS_WEB", "1") != "0"
WEB_TIMEOUT = 9
RESEARCH_SOURCES = 3
SOURCE_CHARS = 3000

# ------------------------------------------------------------
# SERVER
# ------------------------------------------------------------
HOST = os.getenv("JARVIS_HOST", "localhost")
PORT = int(os.getenv("JARVIS_PORT", "8765"))
MAX_MESSAGE_BYTES = 60 * 1024 * 1024
MAX_UPLOAD_BYTES = 40 * 1024 * 1024

# ------------------------------------------------------------
# STORAGE
# ------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "jarvis_data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "output"
TTS_CACHE_DIR = DATA_DIR / "tts_cache"
MEMORY_FILE = DATA_DIR / "memory.json"
TASKS_FILE = DATA_DIR / "tasks.json"

for _d in (DATA_DIR, UPLOAD_DIR, OUTPUT_DIR, TTS_CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)

SUPPORTED_EXTENSIONS = (".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json", ".parquet")
DOCUMENT_EXTENSIONS = (".pdf", ".docx", ".txt", ".md", ".markdown", ".rst", ".html", ".htm", ".log",
                       ".py", ".js", ".ts", ".java", ".c", ".cpp", ".go", ".rs", ".sql", ".yaml", ".yml", ".toml")

# ------------------------------------------------------------
# ANALYTICS
# ------------------------------------------------------------
MAX_ROWS_FOR_CHARTS = 200_000
MAX_CATEGORY_LEVELS = 40
FORECAST_PERIODS = 6

# ------------------------------------------------------------
# CHART THEME (matches the React HUD)
# ------------------------------------------------------------
THEME = {
    "bg": "#0b0d12",
    "panel": "#10131a",
    "grid": "#222836",
    "text": "#e9edf4",
    "muted": "#8b93a5",
    "red": "#e0524a",
    "ember": "#5cc8ff",      # arc-reactor blue: the main accent
    "gold": "#e8c47c",       # champagne gold: titles and highlights
    "green": "#4ade80",
    "palette": ["#5cc8ff", "#e8c47c", "#e0524a", "#8f9cff", "#4ade80",
                "#f2a07b", "#9be7ff", "#c79bff", "#f5d48a", "#94a3b8"],
}
