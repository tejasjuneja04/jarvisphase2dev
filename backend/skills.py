"""
Everyday assistant skills: memory, tasks, file control, web shortcuts, utilities.
Each handler returns a reply string, or None when the message is not for it.
"""

from __future__ import annotations

import ast
import json
import operator
import os
import re
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

import config


# ============================================================
# JSON PERSISTENCE
# ============================================================

class _Store:
    def __init__(self, path: Path, default):
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            self.data = default

    def save(self):
        with self.lock:
            self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")


# ============================================================
# OS HELPERS
# ============================================================

def open_path(path: str | Path):
    path = str(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", path])


def open_app(name: str) -> bool:
    if sys.platform == "darwin":
        return subprocess.run(["open", "-a", name], capture_output=True).returncode == 0
    return False


# ============================================================
# SAFE CALCULATOR
# ============================================================

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos, ast.FloorDiv: operator.floordiv,
}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("exponent too large")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    raise ValueError("unsupported expression")


def safe_calc(expr: str):
    return _eval(ast.parse(expr, mode="eval"))


# ============================================================
# SKILLS
# ============================================================

# ============================================================
# TIMERS AND REMINDERS
# ============================================================

_UNITS = {"second": 1, "seconds": 1, "sec": 1, "secs": 1, "s": 1, "minute": 60, "minutes": 60, "min": 60, "mins": 60,
          "m": 60, "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600, "h": 3600}


_ONES = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
         "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
         "seventeen": 17, "eighteen": 18, "nineteen": 19}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}


def _numberize(t: str) -> str:
    """'forty five minutes' -> '45 minutes'; 'an hour' -> '1 hour'; 'half an hour' -> '30 minutes'."""
    t = re.sub(r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[ -](one|two|three|four|five|six|seven|eight|nine)\b",
               lambda m: str(_TENS[m.group(1)] + _ONES[m.group(2)]), t)
    words = sorted(list(_TENS) + list(_ONES), key=len, reverse=True)
    t = re.sub(r"\b(" + "|".join(words) + r")\b", lambda m: str(_TENS.get(m.group(1), _ONES.get(m.group(1)))), t)
    t = re.sub(r"\bhalf (?:an? )?(hour|minute)\b", lambda m: "30 minutes" if m.group(1) == "hour" else "30 seconds", t)
    t = re.sub(r"\b(?:a|an)\b(?=\s*(?:hours?|hrs?|minutes?|mins?|seconds?|secs?)(?![a-z]))", "1", t)
    return t


def parse_duration(text: str):
    """'10 minutes', 'an hour and a half', 'half an hour', '1h30m', 'forty five seconds' -> seconds (or None)."""
    t = _numberize(text.lower().replace("-", " ").strip().rstrip(".!?"))
    total, found, last = 0.0, False, None
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)(?![a-z])", t):
        total += float(num) * _UNITS[unit]
        found, last = True, unit
    if found and re.search(r"\band a half\b", t):
        total += 0.5 * _UNITS[last]
    return int(total) if found and total > 0 else None


def parse_timer(text: str):
    """Return {"clear": True} | {"seconds", "label"} | None."""
    t = text.strip().rstrip(".!?")
    low = t.lower()
    if re.match(r"^(?:cancel|stop|clear|delete|remove)\s+(?:all\s+|my\s+)?(?:timers?|alarms?|reminders?)$", low):
        return {"clear": True}

    # "set a 10 minute timer", "start a 5 minute timer for the pasta", "10 minute timer"
    m = re.match(r"^(?:(?:set|start|create|begin)\s+)?(?:a\s+|an\s+)?(.+?)\s+(?:timer|alarm)(?:\s+(?:for|to)\s+(?:the\s+)?(.+))?$", t, re.I)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            return {"seconds": secs, "label": (m.group(2) or "").strip()}
    m = re.match(r"^(?:set|start|create|begin)\s+(?:a\s+|an\s+)?(?:(.+?)\s+)?(?:timer|alarm)(?:\s+(?:for|of))?\s+(.+)$", t, re.I)
    if m:
        secs = parse_duration(m.group(2))
        if secs:
            return {"seconds": secs, "label": (m.group(1) or "").strip()}
    m = re.match(r"^timer\s+(?:for\s+)?(.+)$", t, re.I)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            return {"seconds": secs, "label": ""}
    m = re.match(r"^(?:remind me|wake me|alert me|tell me|ping me)\s+in\s+(.+?)\s+(?:to|that|about)\s+(.+)$", t, re.I)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            return {"seconds": secs, "label": m.group(2).strip()}
    m = re.match(r"^(?:remind me|wake me|alert me)\s+(?:to\s+)?(.+?)\s+in\s+(.+)$", t, re.I)
    if m:
        secs = parse_duration(m.group(2))
        if secs:
            label = m.group(1).strip()
            return {"seconds": secs, "label": "" if label.lower() == "up" else label}   # "wake me up in ..."
    m = re.match(r"^(?:remind me|alert me|wake me(?: up)?|ping me|tell me)\s+in\s+(.+)$", t, re.I)
    if m:
        secs = parse_duration(m.group(1))
        if secs:
            return {"seconds": secs, "label": ""}
    return None


def format_duration(secs: int) -> str:
    h, rem = divmod(int(secs), 3600)
    m, s = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} hour{'s' if h != 1 else ''}")
    if m:
        parts.append(f"{m} minute{'s' if m != 1 else ''}")
    if s and not h:
        parts.append(f"{s} second{'s' if s != 1 else ''}")
    return " and ".join(parts) or "0 seconds"


class Skills:
    WEBSITES = {
        "github": "https://github.com", "gmail": "https://mail.google.com",
        "linkedin": "https://www.linkedin.com", "instagram": "https://www.instagram.com",
        "facebook": "https://www.facebook.com", "reddit": "https://www.reddit.com",
        "amazon": "https://www.amazon.in", "chatgpt": "https://chatgpt.com",
        "netflix": "https://www.netflix.com", "youtube": "https://www.youtube.com",
        "spotify": "https://open.spotify.com", "google": "https://www.google.com",
        "maps": "https://maps.google.com", "drive": "https://drive.google.com",
        "calendar": "https://calendar.google.com", "twitter": "https://x.com", "x": "https://x.com",
        "stackoverflow": "https://stackoverflow.com", "kaggle": "https://www.kaggle.com",
    }

    def __init__(self):
        self.memory_store = _Store(config.MEMORY_FILE, {"name": None, "facts": []})
        self.task_store = _Store(config.TASKS_FILE, {"tasks": []})
        self.memory = self.memory_store.data
        self.memory.setdefault("facts", [])
        self.memory.setdefault("name", None)
        self.task_store.data.setdefault("tasks", [])

    # -------------------------------------------------------- memory

    def memory_context(self) -> str:
        bits = []
        if self.memory.get("name"):
            bits.append(f"User's name: {self.memory['name']}")
        for f in self.memory["facts"][-8:]:
            bits.append(f"Known fact: {f}")
        return "\n".join(bits) if bits else "None"

    def handle_memory(self, text: str):
        t = text.strip()
        low = t.lower()

        m = re.match(r"^(?:my name is|call me|i am called)\s+([A-Za-z][A-Za-z '\-]{0,40})$", t, re.I)
        if m:
            self.memory["name"] = m.group(1).strip().title()
            self.memory_store.save()
            return f"Noted. I'll call you {self.memory['name']}."

        m = re.match(r"^(?:remember(?: that)?|note that|keep in mind(?: that)?)\s+(.+)$", t, re.I)
        if m:
            fact = m.group(1).strip().rstrip(".")
            if fact and fact not in self.memory["facts"]:
                self.memory["facts"].append(fact)
                self.memory["facts"] = self.memory["facts"][-50:]
                self.memory_store.save()
            return f"Understood. I'll remember that {fact}."

        if re.search(r"what (do you|can you) (remember|know) about me|what do you remember", low):
            lines = []
            if self.memory.get("name"):
                lines.append(f"Your name: {self.memory['name']}")
            lines += [f"• {f}" for f in self.memory["facts"]]
            return "\n".join(lines) if lines else "I don't have anything stored about you yet."

        if re.match(r"^forget (everything|all)\b", low):
            self.memory["facts"], self.memory["name"] = [], None
            self.memory_store.save()
            return "Memory cleared."

        m = re.match(r"^forget (?:that )?(.+)$", low)
        if m:
            needle = m.group(1).strip()
            before = len(self.memory["facts"])
            self.memory["facts"] = [f for f in self.memory["facts"] if needle not in f.lower()]
            self.memory_store.save()
            removed = before - len(self.memory["facts"])
            return f"Forgotten ({removed} item{'s' if removed != 1 else ''})." if removed else "I don't have that stored."
        return None

    # -------------------------------------------------------- tasks

    @property
    def tasks(self) -> list:
        return self.task_store.data["tasks"]

    def handle_tasks(self, text: str):
        t = text.strip()
        low = t.lower()

        if low.startswith("remind me to"):
            task = t[len("remind me to"):].strip()
            if task:
                self.tasks.append({"text": task, "done": False, "added": datetime.now().isoformat(timespec="minutes")})
                self.task_store.save()
                return f"I'll remember to {task}."

        if re.search(r"\b(my tasks|show tasks|list tasks|task list|to-?do list)\b", low):
            if not self.tasks:
                return "You have no tasks."
            return "Your tasks:\n" + "\n".join(
                f"{i}. [{'x' if x['done'] else ' '}] {x['text']}" for i, x in enumerate(self.tasks, 1))

        m = re.match(r"^(?:done|complete|finish|tick)(?: task)?\s+(\d+)$", low)
        if m:
            i = int(m.group(1)) - 1
            if 0 <= i < len(self.tasks):
                self.tasks[i]["done"] = True
                self.task_store.save()
                return f"Marked done: {self.tasks[i]['text']}"
            return "There is no task with that number."

        if low.startswith("clear tasks"):
            self.tasks.clear()
            self.task_store.save()
            return "Tasks cleared."
        return None

    # -------------------------------------------------------- files

    def _desktop_path(self, name: str):
        base = (Path.home() / "Desktop").resolve()
        target = (base / name).resolve()
        if base not in target.parents and target != base:
            return None  # blocks ../ escapes
        return target

    def search_files(self, query: str, limit: int = 8) -> list:
        results = []
        roots = [Path.home() / d for d in ("Desktop", "Documents", "Downloads")]
        q = query.lower()
        for root in roots:
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for name in filenames + dirnames:
                    if q in name.lower():
                        results.append(os.path.join(dirpath, name))
                        if len(results) >= limit:
                            return results
        return results

    def handle_files(self, text: str, session):
        t = text.strip()
        low = t.lower()

        folders = {"open downloads": "Downloads", "open desktop": "Desktop", "open documents": "Documents"}
        if low in folders:
            open_path(Path.home() / folders[low])
            return f"Opening {folders[low]}."

        if low.startswith("create folder"):
            name = t[len("create folder"):].strip()
            if not name:
                return "Tell me the folder name."
            target = self._desktop_path(name)
            if target is None:
                return "That folder name is not allowed."
            target.mkdir(parents=True, exist_ok=True)
            return f"Folder '{name}' created on Desktop."

        m = re.match(r"^delete\s+(.+)$", t, re.I)
        if m and not re.match(r"^delete (the|my|all|this|that|these|those|row|column|data)\b", low):
            name = m.group(1).strip().strip("\"'")
            target = self._desktop_path(name)
            if target is None or not target.exists():
                return "I couldn't find that on your Desktop."
            session.pending = {"action": "delete", "path": str(target)}
            return f"You are about to delete '{target.name}' from your Desktop. Say 'confirm' to proceed or 'cancel' to abort."

        m = re.match(r"^find\s+(?:file\s+|folder\s+)?(.+)$", t, re.I)
        if m and not low.startswith("find out"):
            query = m.group(1).strip()
            results = self.search_files(query)
            return "Found:\n" + "\n".join(results) if results else "No matching files found."
        return None

    def handle_confirmation(self, text: str, session):
        pending = getattr(session, "pending", None)
        if not pending:
            return None
        low = text.strip().lower()
        if low in ("cancel", "no", "abort", "stop", "never mind"):
            session.pending = None
            return "Cancelled. Nothing was deleted."
        if low in ("confirm", "yes", "confirm delete", "do it", "proceed"):
            session.pending = None
            path = Path(pending["path"])
            try:
                if path.is_file():
                    path.unlink()
                else:
                    path.rmdir()
                return f"Deleted '{path.name}'."
            except Exception:
                return "I couldn't delete it. If it's a folder, it may not be empty."
        return None

    # -------------------------------------------------------- web

    def handle_web(self, text: str):
        t = text.strip()
        low = t.lower()

        if low in ("open news", "open the news"):
            webbrowser.open("https://news.google.com")
            return "Opening Google News."

        # "google X" / "search google for X" opens the browser; plain "search for X" is answered in-app
        m = re.match(r"^(?:search\s+google\s+for|google(?:\s+for)?)\s+(.+)$", t, re.I)
        if m:
            q = m.group(1).strip()
            webbrowser.open("https://www.google.com/search?q=" + quote_plus(q))
            return f"Searching Google for {q}."

        m = re.match(r"^youtube\s+(.+)$", t, re.I)
        if m:
            q = m.group(1).strip()
            webbrowser.open("https://www.youtube.com/results?search_query=" + quote_plus(q))
            return f"Opening YouTube for {q}."

        m = re.match(r"^play\s+(.+)$", t, re.I)
        if m and "?" not in t and len(t.split()) <= 8:
            song = re.sub(r"\s+on spotify$", "", m.group(1).strip(), flags=re.I)
            webbrowser.open("https://open.spotify.com/search/" + quote_plus(song).replace("+", "%20"))
            return f"Opening Spotify for {song}."

        m = re.match(r"^open\s+(.+)$", t, re.I)
        if m:
            target = m.group(1).strip().strip("\"'")
            key = target.lower().replace(".com", "")
            if key in self.WEBSITES:
                webbrowser.open(self.WEBSITES[key])
                return f"Opening {target.title()}."
            if re.match(r"^(https?://)?[\w\-]+(\.[\w\-]+)+(/\S*)?$", target):
                webbrowser.open(target if target.startswith("http") else "https://" + target)
                return f"Opening {target}."
            if " " not in target or len(target.split()) <= 3:
                if open_app(target):
                    return f"Opening {target}."
        return None

    # -------------------------------------------------------- utilities

    def handle_utils(self, text: str):
        low = text.strip().lower().rstrip("?!. ")

        if low in ("time", "what time is it", "what's the time", "whats the time", "current time", "tell me the time"):
            return datetime.now().strftime("It is %I:%M %p.")
        if low in ("date", "what's the date", "whats the date", "today's date", "what is today's date",
                   "what day is it", "what's today's date", "what is the date today"):
            return datetime.now().strftime("Today is %A, %d %B %Y.")

        m = re.match(r"^(?:calculate|calc|compute|what is|what's|whats)\s+([\d\s+\-*/().^%x×÷,]+)$", low)
        if m:
            expr = m.group(1).replace("^", "**").replace("x", "*").replace("×", "*").replace("÷", "/").replace(",", "")
            if re.search(r"\d", expr) and re.search(r"[+\-*/%]", expr):
                try:
                    value = safe_calc(expr)
                    return f"{value:,.6g}" if isinstance(value, float) else f"{value:,}"
                except Exception:
                    return "I couldn't evaluate that expression."
        return None
