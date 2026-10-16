"""
Find files on this computer by voice or chat, then open them or load them into JARVIS.
 
    "find my resume pdf"            search file names (Spotlight on macOS, a folder walk elsewhere)
    "find files about budget"       search inside files too (Spotlight only)
    "where is my project report"
    "open 2" / "open the first one" open a result with its default app
    "show 1 in finder"              reveal it in Finder instead of opening it
    "load 3" / "read it"            read it into JARVIS: documents become Q&A, spreadsheets become a dataset
 
Read-only by design: nothing here deletes, moves or edits a file. Only the user's home folder is searched,
hidden folders and system/library folders are skipped, and programs and scripts are revealed, never run.
"""
 
from __future__ import annotations
 
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
 
import config
from models import Reply
 
MAX_RESULTS = 8
WALK_FILE_LIMIT = 60000          # fallback search stops after this many files, so it stays quick
WALK_MAX_DEPTH = 7
 
KINDS = {
    "pdf": (".pdf",),
    "pdfs": (".pdf",),
    "word": (".docx", ".doc"),
    "docx": (".docx",),
    "excel": (".xlsx", ".xls", ".csv"),
    "spreadsheet": (".xlsx", ".xls", ".csv"),
    "spreadsheets": (".xlsx", ".xls", ".csv"),
    "xlsx": (".xlsx",),
    "csv": (".csv",),
    "dataset": (".csv", ".xlsx", ".xls", ".json", ".parquet", ".tsv"),
    "python": (".py",),
    "py": (".py",),
    "code": (".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".c", ".cpp", ".go", ".rs", ".sql"),
    "powerpoint": (".pptx", ".ppt", ".key"),
    "ppt": (".pptx", ".ppt"),
    "presentation": (".pptx", ".ppt", ".key"),
    "slides": (".pptx", ".ppt", ".key"),
    "image": (".png", ".jpg", ".jpeg", ".heic", ".gif", ".webp"),
    "photo": (".png", ".jpg", ".jpeg", ".heic"),
    "photos": (".png", ".jpg", ".jpeg", ".heic"),
    "picture": (".png", ".jpg", ".jpeg", ".heic"),
    "screenshot": (".png", ".jpg"),
    "video": (".mp4", ".mov", ".mkv", ".avi"),
    "text": (".txt", ".md"),
    "notes": (".txt", ".md"),
    "zip": (".zip",),
}
 
# programs and scripts: revealed in Finder, never launched
RUNNABLE = {".app", ".command", ".sh", ".bash", ".zsh", ".tool", ".pkg", ".mpkg", ".exe", ".bat", ".scpt",
            ".workflow", ".terminal", ".jar", ".dmg"}
 
SKIP_DIRS = {"Library", "node_modules", "site-packages", "__pycache__", "venv", ".venv", "env", "jarvis-env",
             "Applications", "Pictures Library.photoslibrary", "build", "dist", "target"}
 
# words that make "find ..." a data question, not a file search ("find outliers", "find the top 5 customers")
ANALYSIS_WORDS = re.compile(
    r"\b(outliers?|anomal\w*|correlat\w*|duplicates?|missing values?|trends?|drivers?|segments?|clusters?|"
    r"patterns?|insights?|averages?|mean|median|totals?|top \d+|bottom \d+|highest|lowest|best|worst|"
    r"customers?|revenue|sales|profit|rows?|columns?|churn)\b", re.I)
FILE_HINTS = re.compile(r"\b(files?|folders?|documents?|docs?|my|on my (?:mac|computer|laptop))\b", re.I)
ORDINALS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8,
            "ninth": 9, "tenth": 10, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
 
FIND_RE = re.compile(r"^(?:please\s+|can you\s+|could you\s+|jarvis,?\s+)*"
                     r"(find|locate|look for|search for|search|where is|where's|where are)\s+(.+)$", re.I)
PICK = r"(?:(?:file|result|number|no\.?|#)\s*)?(\d{1,2}|first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|" \
       r"1st|2nd|3rd|4th|5th|last|it|that|this)(?:\s+one)?(?:\s+(?:file|result))?"
OPEN_RE = re.compile(rf"^(?:please\s+)?(open|show|reveal)\s+(?:the\s+)?{PICK}(\s+in\s+finder)?$", re.I)
LOAD_RE = re.compile(rf"^(?:please\s+)?(?:load|read|upload|open and read)\s+(?:the\s+)?{PICK}$", re.I)
 
 
def owns(text: str) -> bool:
    """True when the message starts like a file search, so the older 'find' command must not take it."""
    return bool(FIND_RE.match(text.strip()))
 
 
# ============================================================
# PARSING
# ============================================================
 
def parse_query(raw: str) -> tuple[str, tuple, bool]:
    """'my resume pdf' -> ('resume', ('.pdf',), False). The bool means 'search inside files too'."""
    q = raw.strip().strip("?.!\"'")
    content = False
    m = re.match(r"^(?:files?|documents?|anything)\s+(?:about|containing|that (?:mention|contain)s?|with)\s+(.+)$", q, re.I)
    if m:
        q, content = m.group(1), True
    q = re.sub(r"\s+(?:on|in|from)\s+(?:my|this|the)\s+(?:mac|computer|laptop|machine|pc|disk|drive)$", "", q, flags=re.I)
 
    exts: tuple = ()
    explicit = re.search(r"(?:^|\s)\.?(pdf|docx?|xlsx?|csv|pptx?|py|txt|md|json|png|jpe?g|mp4|mov|zip)$", q, re.I)
    words = q.split()
    kept = []
    for w in words:
        lw = w.lower().strip(",")
        if lw in KINDS and not exts:
            exts = KINDS[lw]
            continue
        if lw in ("my", "the", "a", "an", "file", "files", "called", "named", "document", "documents",
                  "folder", "please", "some", "all", "of", "for", "that", "is", "are"):
            continue
        kept.append(w)
    if not exts and explicit:
        exts = ("." + explicit.group(1).lower(),)
    name = " ".join(kept).strip()
    if "." in name and re.search(r"\.\w{1,5}$", name):          # "report.pdf" typed in full
        stem, ext = os.path.splitext(name)
        if not exts:
            exts = (ext.lower(),)
        name = stem
    return name, exts, content
 
 
def _pick_index(word: str, count: int) -> int | None:
    w = word.lower()
    if w in ("it", "that", "this"):
        return 0 if count >= 1 else None
    if w == "last":
        return count - 1 if count else None
    n = ORDINALS.get(w) or (int(w) if w.isdigit() else None)
    if n is None or not 1 <= n <= count:
        return None
    return n - 1
 
 
# ============================================================
# SEARCHING
# ============================================================
 
def _allowed(path: Path, home: Path) -> bool:
    try:
        rel = path.resolve().relative_to(home)
    except (ValueError, OSError):
        return False
    parts = rel.parts
    if not parts:
        return False
    for p in parts[:-1]:
        if p.startswith(".") or p in SKIP_DIRS or p.endswith((".app", ".photoslibrary", ".bundle", ".framework")):
            return False
    return not parts[-1].startswith(".")
 
 
def _matches(name: str, terms: list, exts: tuple) -> bool:
    low = name.lower()
    if exts and not low.endswith(exts):
        return False
    norm = re.sub(r"[_\-.]+", " ", low)
    return all(t in low or t in norm for t in terms)
 
 
def _spotlight(name: str, exts: tuple, content: bool, home: Path) -> list | None:
    if sys.platform != "darwin":
        return None
    try:
        if content and name:
            cmd = ["mdfind", "-onlyin", str(home), name]
        elif name:
            cmd = ["mdfind", "-onlyin", str(home), "-name", name.split()[0]]
        elif exts:
            cmd = ["mdfind", "-onlyin", str(home), "-name", exts[0]]
        else:
            return []
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=8).stdout
    except Exception:
        return None
    return [Path(line) for line in out.splitlines() if line.strip()]
 
 
def _walk(terms: list, exts: tuple, home: Path) -> list:
    roots = [home / d for d in ("Desktop", "Documents", "Downloads")] + \
            [p for p in home.iterdir() if p.is_dir() and not p.name.startswith(".")
             and p.name not in SKIP_DIRS and p.name not in ("Desktop", "Documents", "Downloads")]
    found, seen = [], 0
    for root in roots:
        if not root.exists():
            continue
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS
                           and not d.endswith((".app", ".photoslibrary", ".bundle"))]
            if len(Path(dirpath).parts) - base_depth >= WALK_MAX_DEPTH:
                dirnames[:] = []
            for fn in filenames:
                seen += 1
                if _matches(fn, terms, exts):
                    found.append(Path(dirpath) / fn)
            if seen > WALK_FILE_LIMIT:
                return found
    return found
 
 
def search(name: str, exts: tuple = (), content: bool = False, home: Path | None = None) -> list:
    home = (home or Path.home()).resolve()
    terms = [t.lower() for t in name.split() if t]
    hits = _spotlight(name, exts, content, home)
    if hits is None:
        hits = _walk(terms, exts, home)
    elif not content:
        hits = [h for h in hits if _matches(h.name, terms, exts)]
    elif exts:
        hits = [h for h in hits if h.name.lower().endswith(exts)]
    results, seen = [], set()
    for h in hits:
        if h in seen or not _allowed(h, home):
            continue
        seen.add(h)
        try:
            if h.is_file() or h.suffix == ".app":
                results.append(h)
        except OSError:
            continue
    results.sort(key=lambda p: _mtime(p), reverse=True)
    return results[:MAX_RESULTS]
 
 
def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0
 
 
def _size(p: Path) -> str:
    try:
        n = p.stat().st_size
    except OSError:
        return ""
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""
 
 
def _when(p: Path) -> str:
    t = _mtime(p)
    if not t:
        return ""
    days = (time.time() - t) / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    if days < 7:
        return f"{int(days)} days ago"
    return datetime.fromtimestamp(t).strftime("%d %b %Y")
 
 
def _short(p: Path, home: Path) -> str:
    try:
        return "~/" + str(p.parent.resolve().relative_to(home))
    except ValueError:
        return str(p.parent)
 
 
def _loadable(p: Path) -> str | None:
    ext = p.suffix.lower()
    if ext in config.SUPPORTED_EXTENSIONS and ext not in (".txt",):
        return "dataset"
    if ext in config.DOCUMENT_EXTENSIONS:
        return "document"
    return None
 
 
# ============================================================
# ACTIONS
# ============================================================
 
def _open(path: Path, reveal: bool) -> None:
    if sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if reveal else ["open", str(path)])
    elif sys.platform.startswith("win"):
        if reveal:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path.parent if reveal else path)])
 
 
def _is_runnable(p: Path) -> bool:
    if p.suffix.lower() in RUNNABLE:
        return True
    try:
        return p.is_file() and os.access(p, os.X_OK) and not p.suffix
    except OSError:
        return False
 
 
def handle(client, session, text: str) -> Reply | None:
    """Entry point from main.py. Returns a Reply, or None when the message isn't about finding files."""
    t = text.strip()
    title = session.title
    found: list = getattr(session, "found_files", None) or []
 
    # ---- follow-ups on the last search ----
    if found:
        m = OPEN_RE.match(t.rstrip(".!"))
        if m:
            i = _pick_index(m.group(2), len(found))
            if i is None:
                return Reply(f"I listed {len(found)} files, {title}. Say a number from 1 to {len(found)}.")
            p = found[i]
            if not p.exists():
                return Reply(f"{p.name} seems to have been moved or deleted, {title}.")
            reveal = bool(m.group(3)) or m.group(1).lower() == "reveal" or _is_runnable(p)
            _open(p, reveal)
            if reveal and _is_runnable(p) and not m.group(3):
                return Reply(f"{p.name} is a program or script, so I've shown it in Finder rather than running it, {title}.")
            return Reply(f"{'Showing' if reveal else 'Opening'} **{p.name}**, {title}.",
                         speech=f"{'Showing' if reveal else 'Opening'} {p.stem}.")
 
        m = LOAD_RE.match(t.rstrip(".!"))
        if m:
            i = _pick_index(m.group(1), len(found))
            if i is None:
                return Reply(f"I listed {len(found)} files, {title}. Say a number from 1 to {len(found)}.")
            p = found[i]
            kind = _loadable(p)
            if kind is None:
                return Reply(f"I can't read {p.suffix or 'that'} files, {title}. I can load documents "
                             f"(PDF, Word, text, code) and datasets (CSV, Excel, JSON). Say \"open {i + 1}\" to open it instead.")
            try:
                if p.stat().st_size > config.MAX_UPLOAD_BYTES:
                    return Reply(f"{p.name} is larger than the {config.MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit, {title}.")
                if kind == "dataset":
                    return client.load_path(session, p)
                return client.load_document(session, p.name, p.read_bytes())
            except Exception as e:
                return Reply(f"I found {p.name} but couldn't read it: {e}")
 
    # ---- a new search ----
    m = FIND_RE.match(t.rstrip("?.!"))
    if not m:
        return None
    verb, rest = m.group(1).lower(), m.group(2)
    if re.match(r"^(out|me a|me an|information|info|news|a way|how|why|what|who|when)\b", rest, re.I):
        return None
    if getattr(session, "dataset", None) is not None and ANALYSIS_WORDS.search(rest) and not FILE_HINTS.search(rest):
        return None
    name, exts, content = parse_query(rest)
    # "search for X" and "where is X" are web or general questions unless they clearly mean a file
    typed_filename = bool(re.search(r"\w\.(?:pdf|docx?|xlsx?|csv|pptx?|py|txt|md|json|png|jpe?g|mp4|mov|zip)\b", rest, re.I))
    if verb in ("search", "search for", "where is", "where's", "where are") and not (FILE_HINTS.search(rest) or typed_filename):
        return None
    if not name and not exts:
        return Reply(f"What should I look for, {title}? For example: \"find my resume pdf\".")
 
    home = Path.home().resolve()
    results = search(name, exts, content)
    session.found_files = results
    what = (name or "") + (f" ({', '.join(exts)})" if exts else "")
    if not results:
        tip = "" if content or sys.platform != "darwin" else " Try \"find files about " + (name or "it") + "\" to search inside files too."
        return Reply(f"I couldn't find any files matching **{what.strip()}** in your home folder, {title}.{tip}",
                     speech=f"I couldn't find anything matching {name or 'that'}.")
 
    lines = [f"I found {len(results)} file{'s' if len(results) != 1 else ''} matching **{what.strip()}**, newest first:\n"]
    for n, p in enumerate(results, 1):
        meta = " · ".join(x for x in (_size(p), _when(p)) if x)
        lines.append(f"{n}. **{p.name}**  \n   {_short(p, home)}" + (f" · {meta}" if meta else ""))
    lines.append("\nSay \"open 1\" to open a file, \"show 1 in finder\" to see where it is"
                 + (", or \"load 1\" to read it into JARVIS." if any(_loadable(p) for p in results) else "."))
    first = results[0]
    chips = [f"open 1", f"show 1 in finder"] + (["load 1"] if _loadable(first) else [])
    speech = (f"I found {len(results)} files. The newest is {first.stem}." if len(results) > 1
              else f"I found {first.stem}.")
    return Reply("\n".join(lines), speech=speech, suggestions=chips)
 