"""
Everyday commands in English, Hindi and Hinglish, with replies in the language you used.

    "YouTube kholo"                    "open YouTube"            "यूट्यूब खोलो"
    "Samay Raina YouTube pe chalao"    "play Samay Raina on YouTube"   "Jarvis YouTube Samay Raina"
    "Arijit Singh ke gaane bajao"      "play Believer"
    "Chrome kholo"                     "open Spotify"            "github.com kholo"
    "Google pe IPL score search karo"  "google best laptops"
    "volume 40 karo"  "awaaz kam karo"  "आवाज़ बढ़ाओ"  "mute karo"

"YouTube ... chalao" plays the top video straight away instead of stopping at the search page.
Runs before the language model, so these never turn into a chat answer that only *says* it opened something.
"""

from __future__ import annotations

import re
import sys
import urllib.request
import webbrowser
from urllib.parse import quote_plus

import config
import lang as lang_mod
import system
from skills import Skills, open_app

WAKE = re.compile(r"^\s*(?:hey\s+|ok\s+|okay\s+|hi\s+)?(?:jarvis|जार्विस|जारविस)\b[\s,.:!-]*", re.I)
FILLER = re.compile(r"^(?:please|plz|pls|zara|jara|bhai|yaar|ज़रा|जरा)\s+|\s+(?:please|plz|pls|na|naa|bhai|yaar|ज़रा|जरा)$",
                    re.I)

OPEN_V = (r"(?:kholo|khol\s+do|khol\s+de|kholiye|khol|open\s+karo|open\s+kar\s+do|open\s+kar\s+de|open\s+kar|open|"
          r"chalu\s+karo|start\s+karo|launch\s+karo|launch|start|खोलो|खोल\s+दो|खोल\s+दीजिए|खोलिए|खोल|ओपन\s+करो|"
          r"ओपन\s+कर\s+दो|चालू\s+करो)")
PLAY_V = (r"(?:chalao|chala\s+do|chala\s+de|chalaiye|chala|lagao|laga\s+do|laga\s+de|bajao|baja\s+do|baja\s+de|bajaiye|"
          r"play\s+karo|play\s+kar\s+do|play\s+kar|play|sunao|suna\s+do|dikhao|dikha\s+do|dikhaiye|"
          r"चलाओ|चला\s+दो|चलाइए|लगाओ|लगा\s+दो|बजाओ|बजा\s+दो|बजाइए|सुनाओ|सुना\s+दो|दिखाओ|दिखा\s+दो|प्ले\s+करो)")
SEARCH_V = r"(?:search\s+karo|search\s+kar\s+do|search\s+kar|search|dhundo|dhoondo|khojo|सर्च\s+करो|सर्च\s+कर\s+दो|ढूंढो|ढूँढो|खोजो)"
ON = r"(?:on|pe|par|mein|me|में|पे|पर)"
YT = r"(?:youtube|you\s+tube|yt|यूट्यूब|यू\s*ट्यूब)"
GOOGLE = r"(?:google|गूगल)"

_ALIASES = {
    "यूट्यूब": "youtube", "गूगल": "google", "क्रोम": "google chrome", "chrome": "google chrome",
    "सफारी": "safari", "व्हाट्सएप": "whatsapp", "व्हाट्सऐप": "whatsapp", "इंस्टाग्राम": "instagram",
    "स्पॉटिफाई": "spotify", "जीमेल": "gmail", "नोट्स": "notes", "कैलकुलेटर": "calculator",
    "vs code": "visual studio code", "vscode": "visual studio code", "settings": "system settings",
    "insta": "instagram", "whatsapp": "whatsapp", "mail": "mail", "calculator": "calculator",
}
_SITE_EXTRA = {"whatsapp web": "https://web.whatsapp.com", "hotstar": "https://www.hotstar.com",
               "prime video": "https://www.primevideo.com", "flipkart": "https://www.flipkart.com",
               "zomato": "https://www.zomato.com", "swiggy": "https://www.swiggy.com", "news": "https://news.google.com"}


def strip_wake(text: str) -> str:
    """Drop "Jarvis", "hey Jarvis", "please", "zara" from around a command."""
    t = WAKE.sub("", text.strip())
    for _ in range(2):
        t = FILLER.sub("", t.strip())
    return t.strip().rstrip("?.!।,")


def _say(lang: str, en: str, hi: str) -> str:
    return hi if lang == "hi" else en


# ------------------------------------------------------------------ actions

def youtube_play(query: str) -> str:
    """Open the top YouTube video for the query (falls back to the results page). Returns the URL opened."""
    results = "https://www.youtube.com/results?search_query=" + quote_plus(query)
    url = results
    try:
        req = urllib.request.Request(results, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en"})
        html = urllib.request.urlopen(req, timeout=5).read().decode("utf-8", "ignore")
        m = re.search(r'"videoId":"([\w-]{11})"', html)
        if m:
            url = f"https://www.youtube.com/watch?v={m.group(1)}"
    except Exception as e:
        print(f"[youtube] search failed, opening the results page: {e}")
    webbrowser.open(url)
    return url


def _open_target(target: str) -> str | None:
    """Open an app, a known website or a domain. Returns what was opened, or None."""
    raw = target.strip().strip("\"'")
    key = _ALIASES.get(raw.lower(), raw.lower())
    key_site = key.replace(".com", "")
    sites = {**Skills.WEBSITES, **_SITE_EXTRA}
    if key_site in sites:
        webbrowser.open(sites[key_site])
        return key_site.title() if key_site not in ("youtube", "github") else {"youtube": "YouTube", "github": "GitHub"}[key_site]
    if re.match(r"^(https?://)?[\w\-]+(\.[\w\-]+)+(/\S*)?$", raw):
        webbrowser.open(raw if raw.startswith("http") else "https://" + raw)
        return raw
    if len(key.split()) <= 4 and open_app(key):
        return key.title()
    if key != raw.lower() and open_app(raw):
        return raw
    return None


# ------------------------------------------------------------------ parsing

def _yt_query(t: str) -> str | None:
    """Return the YouTube search query ('' = just open YouTube), or None when this is not a YouTube command."""
    pats = [
        rf"^{YT}\s*(?:{OPEN_V}|{PLAY_V})?$",                                             # youtube / youtube kholo
        rf"^(?:{OPEN_V}|{PLAY_V})\s+{YT}$",                                               # open youtube
        rf"^(?:{PLAY_V}\s+)?(?P<q>.+?)\s+{ON}\s+{YT}(?:\s+(?:{PLAY_V}|{SEARCH_V}|{OPEN_V}))?$",  # X youtube pe chalao
        rf"^(?P<q>.+?)\s+{YT}\s+{ON}(?:\s+(?:{PLAY_V}|{SEARCH_V}|{OPEN_V}))?$",          # X youtube pe chalao
        rf"^{YT}\s+(?:{ON}\s+)?(?P<q>.+?)(?:\s+(?:{PLAY_V}|{SEARCH_V}|{OPEN_V}))?$",       # youtube pe X chalao / youtube X
        rf"^(?:{PLAY_V}|search)\s+(?P<q>.+?)\s+{YT}$",                                    # play X youtube
        rf"^(?P<q>.+?)\s+{YT}\s+(?:{PLAY_V}|{SEARCH_V}|{OPEN_V})$",                       # X youtube chalao
    ]
    for p in pats:
        m = re.match(p, t, re.I)
        if m:
            q = (m.groupdict().get("q") or "").strip()
            q = re.sub(rf"^(?:{OPEN_V}|{PLAY_V})$", "", q, flags=re.I).strip()
            return q
    return None


def _music_query(t: str) -> str | None:
    """'play X', 'X bajao', 'X ke gaane chalao', 'X का गाना लगाओ'."""
    m = re.match(rf"^(?:play|{PLAY_V})\s+(?P<q>.+?)(?:\s+on\s+spotify)?$", t, re.I)
    if m and len(t.split()) <= 9 and not re.search(r"\b(chart|graph|plot|data|game|role)\b", t, re.I):
        return m.group("q")
    m = re.match(rf"^(?P<q>.+?)\s+(?:{PLAY_V})$", t, re.I)
    if m and len(t.split()) <= 9:
        q = m.group("q")
        q = re.sub(r"\s+(?:ka|ki|ke|का|की|के)\s+(?:gaana|gana|gaane|gane|song|songs|गाना|गाने)$", " songs", q, flags=re.I)
        q = re.sub(r"\s+(?:gaana|gana|gaane|gane|गाना|गाने)$", " song", q, flags=re.I)
        q = re.sub(r"^(?:koi|कोई)\s+", "", q, flags=re.I)
        return q.strip() or None
    return None


def _open_query(t: str) -> str | None:
    t = re.split(r"\s+(?:and|aur|और|then|phir)\s+", t, maxsplit=1, flags=re.I)[0]   # "open Chrome and ..."
    m = re.match(rf"^(?P<x>.+?)\s+{OPEN_V}$", t, re.I) or re.match(rf"^(?:open|{OPEN_V})\s+(?P<x>.+)$", t, re.I)
    if m:
        x = re.sub(r"\s+(?:app|ko|को|ऐप)$", "", m.group("x").strip(), flags=re.I)
        if x and len(x.split()) <= 4:
            return x
    return None


def _google_query(t: str) -> str | None:
    pats = [rf"^{GOOGLE}\s+{ON}\s+(?P<q>.+?)(?:\s+{SEARCH_V})?$",
            rf"^(?P<q>.+?)\s+{GOOGLE}\s+(?:{ON}\s+)?(?:{SEARCH_V}|karo|kar\s+do|करो)$",
            rf"^(?P<q>.+?)\s+{GOOGLE}\s+{ON}\s+{SEARCH_V}$",
            rf"^(?:search\s+google\s+for|{GOOGLE}(?:\s+for)?)\s+(?P<q>.+)$"]
    for p in pats:
        m = re.match(p, t, re.I)
        if m:
            return m.group("q").strip()
    return None


def _volume(t: str, lang: str, title: str) -> str | None:
    if not re.search(r"\b(volume|awaaz|awaz|aawaz|sound)\b|आवाज़|आवाज|वॉल्यूम|म्यूट|\bmute\b|\bunmute\b", t, re.I):
        return None
    if sys.platform != "darwin":
        return _say(lang, f"Volume control works on macOS only, {title}.", lang_mod.hi("Volume control sirf Mac par chalta hai.", "Volume control सिर्फ़ Mac पर चलता है।"))
    if re.search(r"\bunmute\b|awaaz\s+(?:chalu|wapas)|आवाज़?\s+(?:चालू|वापस)", t, re.I):
        return _say(lang, f"Sound restored, {title}.", lang_mod.hi("Awaaz wapas chalu kar di.", "आवाज़ वापस चालू कर दी।")) if system.set_mute(False) else None
    if re.search(r"\bmute\b|म्यूट|(?:awaaz|awaz|आवाज़|आवाज)\s+(?:band|बंद)", t, re.I):
        return _say(lang, f"Muted, {title}.", lang_mod.hi("Theek hai, mute kar diya.", "ठीक है, mute कर दिया।")) if system.set_mute(True) else None
    m = re.search(r"(\d{1,3})", t)
    if m:
        level = max(0, min(100, int(m.group(1))))
        if system.set_volume(level):
            return _say(lang, f"Volume set to {level} percent, {title}.", lang_mod.hi(f"Volume {level} percent kar diya.", f"Volume {level} percent कर दिया।"))
        return None
    up = re.search(r"\b(up|badhao|badha\s+do|tez|zyada|jyada|increase|louder)\b|बढ़ाओ|बढ़ा\s+दो|तेज़|तेज|ज़्यादा", t, re.I)
    down = re.search(r"\b(down|kam|dheere|dhire|halka|decrease|lower|quieter)\b|कम|धीरे", t, re.I)
    if not (up or down):
        return None
    cur = system.get_volume()
    if cur is None:
        return None
    new = max(0, min(100, cur + (12 if up else -12)))
    if not system.set_volume(new):
        return None
    if up:
        return _say(lang, f"Volume up to {new} percent, {title}.", lang_mod.hi(f"Volume badha diya, ab {new} percent hai.", f"Volume बढ़ा दिया, अब {new} percent है।"))
    return _say(lang, f"Volume down to {new} percent, {title}.", lang_mod.hi(f"Volume kam kar diya, ab {new} percent hai.", f"Volume कम कर दिया, अब {new} percent है।"))



# ------------------------------------------------------------------ WhatsApp

WA = r"(?:whatsapp|whats\s*app|watsapp|व्हाट्सएप|व्हाट्सऐप)"
MSG = r"(?:message|msg|text|मैसेज)"


def _wa_open(text: str = "", phone: str = "") -> None:
    """Open WhatsApp with the message typed in. Without a phone number WhatsApp asks which chat to send it to."""
    if text or phone:
        q = []
        if phone:
            q.append("phone=" + phone)
        if text:
            q.append("text=" + quote_plus(text).replace("+", "%20"))
        url = "whatsapp://send?" + "&".join(q)
        if sys.platform == "darwin":
            import subprocess
            if subprocess.run(["open", url], capture_output=True).returncode == 0:
                return
        webbrowser.open("https://web.whatsapp.com/send?" + "&".join(q))
        return
    if not open_app("WhatsApp"):
        webbrowser.open("https://web.whatsapp.com")


def _wa_parse(t: str):
    """Pull (recipient, phone, message) out of a WhatsApp request. Any part may be empty."""
    phone = ""
    m = re.search(r"(\+?\d[\d\s-]{8,}\d)", t)
    if m:
        phone = re.sub(r"\D", "", m.group(1))
        if len(phone) == 10:
            phone = "91" + phone                    # Indian mobile number without the country code
    msg = ""
    m = re.search(r"(?:\bsaying\b|\bthat\b|\bki\b|\bke\b|कि|:)\s*(?P<m>.+)$", t, re.I)
    if m:
        msg = m.group("m").strip().strip("\"'")
    who = ""
    m = re.search(r"\bto\s+(?P<w>[A-Za-z][\w ]{0,30}?)(?:\s+(?:on|saying|that)\b|:|$)", t, re.I) or \
        re.search(r"(?P<w>[A-Za-z][\w]{1,20})\s+(?:ko|को)\b", t, re.I)
    if m:
        who = m.group("w").strip()
    return who, phone, msg


def _whatsapp(session, t: str, lang: str, title: str, raw: str = "") -> str | None:
    raw = raw or t                                 # the words exactly as said: "bhai", "yaar" belong in the message
    say = lambda en, rom: _say(lang, en, lang_mod.hi(rom, rom))
    if getattr(session, "wa_pending", False):
        session.wa_pending = False
        if not re.search(r"\b(cancel|rehne do|chhodo|nahi|no)\b", t, re.I):
            who, phone, msg = _wa_parse(raw)
            _wa_open(msg or raw, phone)
            return say(f"Done, {title}. WhatsApp is open with your message typed in. Pick the chat and press send.",
                       "Ho gaya! WhatsApp mein message type kar diya hai. Bas chat chuniye aur send dabaiye.")
        return say("All right, no message.", "Theek hai, rehne dete hain.")
    if not re.search(WA, t, re.I):
        return None
    if re.fullmatch(rf"(?:{OPEN_V}\s+)?{WA}(?:\s+(?:{OPEN_V}|app))?", t, re.I):
        _wa_open()
        return say(f"Opening WhatsApp, {title}.", "WhatsApp khol raha hoon.")
    if re.search(rf"\b{MSG}\b|\bsend\b|\bbhej|\bbol\b|\bbolo\b|भेज|:|\d{{10}}", t, re.I):
        who, phone, msg = _wa_parse(raw)
        if msg:
            _wa_open(msg, phone)
            to = f" for {who}" if who else ""
            return say(f"WhatsApp is open with the message{to} typed in, {title}. Pick the chat and press send.",
                       f"WhatsApp mein message type kar diya{(' ' + who + ' ke liye') if who else ''}. "
                       "Bas chat chuniye aur send dabaiye.")
        _wa_open()
        session.wa_pending = True
        return say(f"WhatsApp is open, {title}. What should the message say?",
                   "WhatsApp khol diya. Message mein kya likhna hai?")
    _wa_open()
    return say(f"Opening WhatsApp, {title}.", "WhatsApp khol raha hoon.")


def handle(session, text: str) -> str | None:
    """Run the command if the text is one. Returns the reply, or None to let the rest of JARVIS answer."""
    t = strip_wake(text)
    if not t or len(t) > 120 or "?" in text:
        return None
    if re.search(r"\b(screen|clipboard|picture|photo|image)\b|स्क्रीन|फोटो|तस्वीर", t, re.I):
        return None                                 # "meri screen dikhao" belongs to vision, not YouTube
    lang, title = session.lang, session.title

    w = _whatsapp(session, t, lang, title, raw=WAKE.sub("", text.strip()).strip())
    if w:
        return w

    # just the name of an app or site: open it ("WhatsApp", "Netflix", "Chrome")
    key = _ALIASES.get(t.lower(), t.lower())
    if key in Skills.WEBSITES or key in _SITE_EXTRA or t.lower() in _ALIASES:
        opened = _open_target(t)
        if opened:
            return _say(lang, f"Opening {opened}, {title}.", lang_mod.hi(f"{opened} khol raha hoon.", f"{opened} खोल रहा हूँ।"))

    v = _volume(t, lang, title)
    if v:
        return v

    q = _yt_query(t)
    if q is not None:
        if not q:
            webbrowser.open("https://www.youtube.com")
            return _say(lang, f"Opening YouTube, {title}.", lang_mod.hi("YouTube khol raha hoon.", "YouTube खोल रहा हूँ।"))
        youtube_play(q)
        return _say(lang, f"Playing {q} on YouTube, {title}.", lang_mod.hi(f"YouTube par {q} chala raha hoon.", f"YouTube पर {q} चला रहा हूँ।"))

    q = _google_query(t)
    if q:
        webbrowser.open("https://www.google.com/search?q=" + quote_plus(q))
        return _say(lang, f"Searching Google for {q}.", lang_mod.hi(f"Google par {q} search kar raha hoon.", f"Google पर {q} search कर रहा हूँ।"))

    x = _open_query(t)
    if x:
        opened = _open_target(x)
        if opened:
            return _say(lang, f"Opening {opened}, {title}.", lang_mod.hi(f"{opened} khol raha hoon.", f"{opened} खोल रहा हूँ।"))
        if len(x.split()) <= 2:
            return _say(lang, f"I couldn't find an app or website called {x}, {title}.",
                        lang_mod.hi(f"Sorry, mujhe {x} naam ka koi app ya website nahi mila.", f"Sorry, मुझे {x} नाम का कोई app या website नहीं मिला।"))
        return None

    q = _music_query(t)
    if q:
        if config.MUSIC_SERVICE == "spotify":
            webbrowser.open("https://open.spotify.com/search/" + quote_plus(q).replace("+", "%20"))
            return _say(lang, f"Opening Spotify for {q}.", lang_mod.hi(f"Spotify par {q} khol raha hoon.", f"Spotify पर {q} खोल रहा हूँ।"))
        youtube_play(q)
        return _say(lang, f"Playing {q}, {title}.", lang_mod.hi(f"{q} chala raha hoon.", f"{q} चला रहा हूँ।"))
    return None
