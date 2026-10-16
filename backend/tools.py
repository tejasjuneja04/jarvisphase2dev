"""
JARVIS tools. Standard library only (urllib), so nothing extra to install.

Network tools return None or [] on any failure, and never raise, so the assistant can say honestly that it
could not reach the web instead of crashing.

    web_search / fetch_text    search the web and read a page
    wikipedia_summary          short, reliable grounding for "who is / what is" questions
    weather / news / currency  live data
    convert_units              offline unit conversion
"""

from __future__ import annotations

import html as htmllib
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import config

import time

_NET = {"down_until": 0.0}


def net_ok() -> bool:
    """False for a short while after a network failure, so offline requests fail fast instead of stalling."""
    return time.time() >= _NET["down_until"]


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"


# ============================================================
# HTTP
# ============================================================

def _is_public_url(url: str) -> bool:
    """Only fetch public http(s) pages, never localhost or private networks."""
    try:
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        for info in socket.getaddrinfo(parts.hostname, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True
    except Exception:
        return False


def http_get(url: str, timeout: float | None = None, max_bytes: int = 1_500_000, check_public: bool = False) -> str | None:
    if not net_ok():
        return None
    if check_public and not _is_public_url(url):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en"})
    try:
        with urllib.request.urlopen(req, timeout=timeout or config.WEB_TIMEOUT) as resp:
            ctype = resp.headers.get("Content-Type", "")
            if any(x in ctype for x in ("pdf", "image/", "video/", "audio/", "zip", "octet-stream")):
                return None
            raw = resp.read(max_bytes)
            enc = resp.headers.get_content_charset() or "utf-8"
            return raw.decode(enc, errors="replace")
    except urllib.error.HTTPError:
        return None                      # the site answered (403/404...): the network itself is fine
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError):
        _NET["down_until"] = time.time() + 45
        return None
    except Exception:
        return None


def http_json(url: str, timeout: float | None = None):
    text = http_get(url, timeout)
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return None


# ============================================================
# HTML -> TEXT
# ============================================================

class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form", "aside", "iframe", "button"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "blockquote"}

    def __init__(self):
        super().__init__()
        self.parts, self.title, self._skip, self._in_title = [], "", 0, False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(page: str) -> tuple[str, str]:
    """Return (title, readable text)."""
    p = _TextExtractor()
    try:
        p.feed(page)
    except Exception:
        pass
    text = htmllib.unescape("".join(p.parts))
    lines = [re.sub(r"[ \t]+", " ", l).strip() for l in text.split("\n")]
    lines = [l for l in lines if len(l) > 25 or (l and len(l.split()) >= 5)]   # drop menu crumbs
    return htmllib.unescape(p.title).strip(), "\n".join(lines)


def fetch_text(url: str, max_chars: int | None = None) -> dict | None:
    max_chars = max_chars or config.SOURCE_CHARS
    page = http_get(url, check_public=True)
    if not page:
        return None
    title, text = html_to_text(page)
    if len(text) < 200:
        return None
    return {"url": url, "title": title or url, "text": text[:max_chars]}


# ============================================================
# SEARCH
# ============================================================

def _strip_tags(s: str) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse_ddg(page: str, n: int = 6) -> list:
    """Parse DuckDuckGo's HTML results page."""
    results = []
    links = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
    snippets = re.findall(r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', page, re.S)
    for i, (href, title) in enumerate(links):
        href = htmllib.unescape(href)
        if href.startswith("//"):
            href = "https:" + href
        if "uddg=" in href:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            href = urllib.parse.unquote(q.get("uddg", [href])[0])
        if not href.startswith("http") or "duckduckgo.com/y.js" in href:
            continue
        results.append({"title": _strip_tags(title), "url": href,
                        "snippet": _strip_tags(snippets[i]) if i < len(snippets) else ""})
        if len(results) >= n:
            break
    return results


def wikipedia_search(query: str, n: int = 5) -> list:
    url = ("https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=%d&srsearch=%s"
           % (n, urllib.parse.quote(query)))
    data = http_json(url)
    out = []
    for r in (data or {}).get("query", {}).get("search", []):
        title = r.get("title", "")
        out.append({"title": title, "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
                    "snippet": _strip_tags(r.get("snippet", ""))})
    return out


def web_search(query: str, n: int = 6) -> list:
    page = http_get("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query))
    results = parse_ddg(page, n) if page else []
    return results or wikipedia_search(query, n)


def wikipedia_summary(query: str) -> dict | None:
    data = http_json("https://en.wikipedia.org/w/api.php?action=opensearch&limit=1&format=json&search="
                     + urllib.parse.quote(query), timeout=5)
    try:
        title = data[1][0]
    except Exception:
        return None
    s = http_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_")),
                  timeout=5)
    if not s or not s.get("extract") or s.get("type") == "disambiguation":
        return None
    return {"title": s.get("title", title), "extract": s["extract"],
            "url": s.get("content_urls", {}).get("desktop", {}).get("page", "")}


# ============================================================
# LIVE DATA
# ============================================================

def weather(city: str) -> dict | None:
    data = http_json("https://wttr.in/%s?format=j1" % urllib.parse.quote(city), timeout=8)
    return parse_weather(data, city)


def parse_weather(data, city: str) -> dict | None:
    try:
        cur = data["current_condition"][0]
        days = data.get("weather", [])
        out = {
            "city": city.title(),
            "temp_c": cur["temp_C"], "feels_c": cur["FeelsLikeC"],
            "desc": cur["weatherDesc"][0]["value"], "humidity": cur["humidity"], "wind_kmh": cur["windspeedKmph"],
            "days": [{"date": d["date"], "max": d["maxtempC"], "min": d["mintempC"],
                      "desc": d["hourly"][4]["weatherDesc"][0]["value"] if d.get("hourly") else ""} for d in days[:3]],
        }
        return out
    except Exception:
        return None


def parse_news(xml_text: str, n: int = 6) -> list:
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        source = (it.findtext("source") or "").strip()
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3]
        items.append({"title": htmllib.unescape(title), "url": (it.findtext("link") or "").strip(),
                      "source": source, "date": (it.findtext("pubDate") or "").strip()})
        if len(items) >= n:
            break
    return items


def news(query: str | None = None, n: int = 6) -> list:
    base = "https://news.google.com/rss"
    url = (base + "/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en" % urllib.parse.quote(query)) if query else \
          (base + "?hl=en-IN&gl=IN&ceid=IN:en")
    page = http_get(url)
    return parse_news(page, n) if page else []


CURRENCY_WORDS = {"dollar": "USD", "dollars": "USD", "usd": "USD", "bucks": "USD", "rupee": "INR", "rupees": "INR",
                  "inr": "INR", "euro": "EUR", "euros": "EUR", "eur": "EUR", "pound": "GBP", "pounds": "GBP",
                  "gbp": "GBP", "yen": "JPY", "jpy": "JPY", "yuan": "CNY", "cny": "CNY", "aud": "AUD",
                  "cad": "CAD", "chf": "CHF", "franc": "CHF", "dirham": "AED", "aed": "AED", "won": "KRW",
                  "krw": "KRW", "sgd": "SGD", "rmb": "CNY"}


def currency(amount: float, frm: str, to: str) -> float | None:
    data = http_json("https://api.frankfurter.app/latest?amount=%s&from=%s&to=%s" % (amount, frm, to))
    try:
        return float(data["rates"][to])
    except Exception:
        return None


def parse_currency_request(text: str):
    """'convert 100 usd to inr', '250 dollars in rupees' -> (amount, FROM, TO) or None."""
    m = re.search(r"(\d[\d,]*\.?\d*)\s*([a-z]+)\s+(?:to|in|into)\s+([a-z]+)", text.lower())
    if not m:
        return None
    frm, to = CURRENCY_WORDS.get(m.group(2)), CURRENCY_WORDS.get(m.group(3))
    if not (frm and to):
        return None
    return float(m.group(1).replace(",", "")), frm, to


# ============================================================
# UNITS (offline)
# ============================================================

_LEN = {"mm": 0.001, "cm": 0.01, "m": 1, "meter": 1, "meters": 1, "metre": 1, "metres": 1, "km": 1000,
        "kilometer": 1000, "kilometers": 1000, "kilometre": 1000, "kilometres": 1000, "in": 0.0254, "inch": 0.0254,
        "inches": 0.0254, "ft": 0.3048, "foot": 0.3048, "feet": 0.3048, "yd": 0.9144, "yard": 0.9144, "yards": 0.9144,
        "mi": 1609.344, "mile": 1609.344, "miles": 1609.344}
_MASS = {"mg": 1e-6, "g": 0.001, "gram": 0.001, "grams": 0.001, "kg": 1, "kilogram": 1, "kilograms": 1, "kilo": 1,
         "kilos": 1, "lb": 0.45359237, "lbs": 0.45359237, "pound": 0.45359237, "pounds": 0.45359237,
         "oz": 0.0283495, "ounce": 0.0283495, "ounces": 0.0283495, "tonne": 1000, "tonnes": 1000}
_VOL = {"ml": 0.001, "l": 1, "liter": 1, "liters": 1, "litre": 1, "litres": 1, "gal": 3.78541, "gallon": 3.78541,
        "gallons": 3.78541, "cup": 0.236588, "cups": 0.236588, "tbsp": 0.0147868, "tsp": 0.00492892}
_TEMP = {"c": "c", "celsius": "c", "centigrade": "c", "f": "f", "fahrenheit": "f", "k": "k", "kelvin": "k"}


def convert_units(text: str) -> str | None:
    m = re.search(r"(-?\d[\d,]*\.?\d*)\s*°?\s*([a-z]+)\s+(?:to|in|into|as)\s+([a-z]+)\b", text.lower())
    if not m:
        return None
    val, a, b = float(m.group(1).replace(",", "")), m.group(2), m.group(3)

    if a in _TEMP and b in _TEMP and a != b:
        c = val if _TEMP[a] == "c" else (val - 32) * 5 / 9 if _TEMP[a] == "f" else val - 273.15
        out = c if _TEMP[b] == "c" else c * 9 / 5 + 32 if _TEMP[b] == "f" else c + 273.15
        return f"{val:g}°{_TEMP[a].upper()} is {out:.1f}°{_TEMP[b].upper()}."
    for table in (_LEN, _MASS, _VOL):
        if a in table and b in table:
            out = val * table[a] / table[b]
            return f"{val:g} {a} is {out:,.4g} {b}."
    return None


# ============================================================
# QUERY UNDERSTANDING
# ============================================================

def extract_weather_city(text: str) -> tuple[bool, str | None]:
    """(is_weather_question, city_or_None)"""
    low = text.lower()
    if not re.search(r"\b(weather|temperature|forecast|is it (raining|sunny|cold|hot)|will it rain)\b", low):
        return False, None
    m = re.search(r"\b(?:in|for|at)\s+([a-z][a-z .'-]{1,40}?)(?:\s+(?:today|tomorrow|tonight|now|right now|this week)|[?.!,]|$)", low)
    return True, (m.group(1).strip() if m else None)


def extract_news_topic(text: str):
    """(is_news_request, topic_or_None)"""
    low = text.lower().strip().rstrip("?.!")
    if re.fullmatch(r"(?:the\s+)?(?:latest\s+|top\s+|today'?s\s+)?(?:news|headlines)(?:\s+today)?|what'?s in the news|what'?s happening|news please",
                    low):
        return True, None
    m = re.match(r"^(?:(?:give me|show me|tell me|get me|any|what'?s)\s+)?(?:the\s+)?(?:latest\s+|top\s+|recent\s+)?"
                 r"(?:news|headlines)\s+(?:about|on|for|regarding)\s+(.+)$", low)
    if m:
        return True, m.group(1).strip()
    m = re.match(r"^what'?s (?:the )?latest (?:on|about|with)\s+(.+)$", low)
    if m:
        return True, m.group(1).strip()
    return False, None


_SEARCH_PREFIX = re.compile(
    r"^(?:please\s+|can you\s+|could you\s+)?(?:search(?:\s+the\s+(?:web|internet))?(?:\s+online)?(?:\s+for)?|"
    r"look\s*up|look\s+for|research|find\s+out(?:\s+about)?|find\s+information\s+(?:about|on)|browse\s+for)\s+(.+)$", re.I)

_TIME_SENSITIVE = re.compile(
    r"\b(latest|current(?:ly)?|today|tonight|this (?:week|month|year)|right now|recent(?:ly)?|news|price of|stock price|"
    r"share price|score|who won|who is winning|release date|new(?:est)? (?:version|model|iphone|release)|"
    r"as of|202[5-9]|exchange rate)\b", re.I)


def extract_search_query(text: str):
    """(needs_web, query, explicit)"""
    t = text.strip()
    m = _SEARCH_PREFIX.match(t)
    if m:
        return True, m.group(1).strip(" ?.!"), True
    if _TIME_SENSITIVE.search(t) and (t.endswith("?") or re.match(r"^(what|who|when|where|how|which|is|are|does|did|will)\b", t, re.I)):
        return True, t.rstrip(" ?.!"), False
    return False, None, False


_FACT_Q = re.compile(r"^(?:who (?:is|was|are|were)|what (?:is|was|are|were)|tell me about|when (?:did|was)|where (?:is|was)|"
                     r"how (?:tall|old|big|far) is)\s+(?:a |an |the )?(.{2,60}?)[?.!]*$", re.I)


def extract_fact_topic(text: str) -> str | None:
    m = _FACT_Q.match(text.strip())
    if not m:
        return None
    topic = m.group(1).strip()
    if not re.search(r"[A-Za-z]{2,}", topic) or re.fullmatch(r"[\d\s+\-*/().^%x]+", topic):
        return None   # arithmetic, not a topic
    return topic
