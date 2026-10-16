"""
Document Q&A.

Upload PDF / Word / text / HTML / code files, then ask questions or request a summary.
Retrieval is BM25 keyword ranking (pure Python, no embeddings model needed), so it is fast and offline.
Answers are generated from the retrieved excerpts only, and cite them as [1], [2].
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import config

_STOP = set("""a an the and or but if then else of to in on at by for with from as is are was were be been being this that
these those it its i you he she we they them his her our your my me do does did done have has had not no yes so than
too very can could should would will shall may might about into over under out up down what which who whom when where
why how there here also just more most some any each other such only own same both few many much""".split())

MAX_DOCS = 30
MAX_TOTAL_CHARS = 4_000_000


def _tokens(text: str) -> list:
    out = []
    for w in re.findall(r"[a-z0-9]{2,}", text.lower()):
        if w in _STOP:
            continue
        for suf in ("ing", "ed", "es", "s"):
            if len(w) > len(suf) + 3 and w.endswith(suf):
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


# ============================================================
# EXTRACTION
# ============================================================

def _pdf_pages(data: bytes) -> list:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("Reading PDFs needs the pypdf package: pip install pypdf")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError("That PDF is password-protected.")
        pages = [(p.extract_text() or "").strip() for p in reader.pages]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"I couldn't read that PDF: {e}")
    if not any(pages):
        raise ValueError("That PDF has no text layer (it looks scanned), and I can't read images of text.")
    return pages


def _docx_text(data: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception as e:
        raise ValueError(f"I couldn't read that Word file: {e}")
    paras = []
    for p in re.split(r"</w:p>", xml):
        runs = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, flags=re.S)
        line = "".join(runs)
        if line.strip():
            paras.append(re.sub(r"&amp;", "&", re.sub(r"&lt;", "<", re.sub(r"&gt;", ">", line))))
    return "\n".join(paras)


def extract(name: str, data: bytes) -> list:
    """Return a list of (page_number_or_None, text)."""
    ext = Path(name).suffix.lower()
    if ext == ".pdf":
        return [(i + 1, t) for i, t in enumerate(_pdf_pages(data)) if t]
    if ext == ".docx":
        return [(None, _docx_text(data))]
    if ext in (".html", ".htm"):
        from tools import html_to_text
        return [(None, html_to_text(data.decode("utf-8", errors="replace"))[1])]
    return [(None, data.decode("utf-8", errors="replace"))]


# ============================================================
# CHUNKING
# ============================================================

def chunk_text(text: str, size: int = 900, overlap: int = 160) -> list:
    paras = [p.strip() for p in re.split(r"\n\s*\n|\r\n\s*\r\n", text) if p.strip()]
    if len(paras) <= 1:
        paras = [p.strip() for p in text.split("\n") if p.strip()]
    chunks, cur = [], ""
    for p in paras:
        while len(p) > size * 1.5:                        # very long paragraph: cut on a sentence or space
            cut = max(p.rfind(". ", 0, size), p.rfind(" ", 0, size), size // 2)
            piece, p = p[: cut + 1].strip(), p[cut + 1:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(piece)
        if len(cur) + len(p) + 1 <= size:
            cur = f"{cur}\n{p}".strip()
        else:
            if cur:
                chunks.append(cur)
            tail = cur[-overlap:] if cur and overlap else ""
            cur = f"{tail}\n{p}".strip() if tail and len(tail) + len(p) <= size else p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if len(c.strip()) > 20]


@dataclass
class Chunk:
    doc: str
    part: int
    page: int | None
    text: str
    tokens: list


# ============================================================
# STORE
# ============================================================

class DocStore:
    def __init__(self):
        self.chunks: list[Chunk] = []
        self.meta: dict[str, dict] = {}
        self._df: dict[str, int] = {}
        self._avg_len = 1.0

    @property
    def empty(self) -> bool:
        return not self.chunks

    def listing(self) -> list:
        return [{"name": n, **m} for n, m in self.meta.items()]

    def add(self, name: str, data: bytes) -> dict:
        ext = Path(name).suffix.lower()
        if ext not in config.DOCUMENT_EXTENSIONS:
            raise ValueError(f"I can read {', '.join(sorted(set(config.DOCUMENT_EXTENSIONS)))}. "
                             f"For CSV or Excel data files, use UPLOAD DATASET.")
        if len(self.meta) >= MAX_DOCS and name not in self.meta:
            raise ValueError(f"That is the limit of {MAX_DOCS} documents. Remove some first.")
        pages = extract(name, data)
        total_chars = sum(len(t) for _, t in pages)
        if total_chars < 20:
            raise ValueError("That file has almost no readable text.")
        if total_chars + sum(m["chars"] for n, m in self.meta.items() if n != name) > MAX_TOTAL_CHARS:
            raise ValueError("That would exceed my document memory. Remove a document first.")

        self.remove(name)
        n_before = len(self.chunks)
        part = 0
        for page, text in pages:
            for c in chunk_text(text):
                self.chunks.append(Chunk(name, part, page, c, _tokens(c)))
                part += 1
        self.meta[name] = {"chunks": len(self.chunks) - n_before, "chars": total_chars,
                           "words": len(re.findall(r"\w+", " ".join(t for _, t in pages))),
                           "pages": len([p for p, _ in pages if p]) or None}
        self._reindex()
        return {"name": name, **self.meta[name]}

    def remove(self, name: str) -> bool:
        if name not in self.meta:
            return False
        self.chunks = [c for c in self.chunks if c.doc != name]
        del self.meta[name]
        self._reindex()
        return True

    def clear(self):
        self.chunks, self.meta = [], {}
        self._reindex()

    def _reindex(self):
        self._df = {}
        for c in self.chunks:
            for t in set(c.tokens):
                self._df[t] = self._df.get(t, 0) + 1
        self._avg_len = (sum(len(c.tokens) for c in self.chunks) / len(self.chunks)) if self.chunks else 1.0

    # ---- retrieval ----
    def search(self, query: str, k: int = 5) -> list:
        q = _tokens(query)
        n = len(self.chunks)
        if not q or not n:
            return []
        k1, b = 1.5, 0.75
        idf = {t: math.log(1 + (n - self._df.get(t, 0) + 0.5) / (self._df.get(t, 0) + 0.5)) for t in set(q)}
        best_possible = sum(idf[t] * (k1 + 1) for t in idf if self._df.get(t)) or 1.0
        scored = []
        for c in self.chunks:
            if not c.tokens:
                continue
            tf: dict[str, int] = {}
            for t in c.tokens:
                tf[t] = tf.get(t, 0) + 1
            s = 0.0
            matched = 0
            for t in idf:
                f = tf.get(t)
                if f:
                    matched += 1
                    s += idf[t] * f * (k1 + 1) / (f + k1 * (1 - b + b * len(c.tokens) / self._avg_len))
            if s > 0:
                scored.append((s, matched, c))
        scored.sort(key=lambda x: -x[0])
        out = []
        for s, matched, c in scored[:k]:
            out.append({"doc": c.doc, "part": c.part, "page": c.page, "text": c.text,
                        "score": s, "confidence": min(1.0, s / best_possible), "matched": matched})
        return out

    def overview(self, max_chars: int = 7000) -> list:
        """Evenly spaced excerpts across every document, for summaries."""
        if not self.chunks:
            return []
        per_doc = max(1, max_chars // max(len(self.meta), 1))
        out = []
        for name in self.meta:
            cs = [c for c in self.chunks if c.doc == name]
            take = max(1, min(len(cs), per_doc // 900))
            step = len(cs) / take
            for i in range(take):
                c = cs[int(i * step)]
                out.append({"doc": c.doc, "part": c.part, "page": c.page, "text": c.text[:900]})
        return out
