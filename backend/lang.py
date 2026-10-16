"""
Language detection for Hindi / English conversation.

    detect("what's the weather")         -> "en"
    detect("आज मौसम कैसा है")              -> "hi"
    detect("aaj mausam kaisa hai bhai")  -> "hi"   (Hinglish typed in English letters)

JARVIS answers in the language the user just used, like Alexa. Hindi replies are written in Devanagari so the
Hindi voices pronounce them properly.
"""

from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_LATIN = re.compile(r"[A-Za-z]")

# common Hindi words typed in English letters; ambiguous ones ("me", "to", "ho", "par") are left out
_HINGLISH = set("""
hai hain tha thi hoga hogi kya kyu kyun kyon kaise kaisa kaisi kab kahan kahaan kaun kitna kitni kitne
mujhe mera meri mere mujhko humko hum hamara hamari tum tumhe tumhara tumhari aap aapka aapki aapko tera teri
batao bata bataiye bataye btao karo karna karke kariye kijiye krdo kardo karde dikhao dikha dikhaiye kholo khol
chalao chala suno sunao samjhao samjha samajh chahiye chaiye raha rahi rahe gaya gayi gaye wala wali wale
sakte sakta sakti kuch bahut bohot bahot sab sabse matlab chalo accha acha achha theek thik haan nahi nahin nhi
bhai yaar abhi aaj kal parso subah shaam raat din mausam paisa paise kitne lagao band ruko jaldi zara thoda
namaste namaskar shukriya dhanyavad dhanyawad kripya kyonki lekin aur bhi toh nahi hoon hun hu jaise waise
padho padh padhke parho dekh nhi nai kr krna krke kro krdo btao bta pta pata mai kese kaha kahan kisko kisne unko hume humein mujhse
tumse aapse hota hoti hote karta karti karte lagta lagti pehle baad kabhi sabko sirf bilkul zaroor jarur sahi galat
jao aao chal bhej bhejo dena lena milega milta samjha samjhe samjhi kaam ghar kal subah zyada jyada thoda accha
ka ki ke bajao baja lagao laga chala kholiye bataiye suniye dekho dekhna bolo bol pe kar kardo krdo gaana gaane gana
kahin kuchh koi kisi kiska kiski uska uski unka iska iski yeh ye woh wo vo hamesha phir fir dobara wapas
""".split())


_NEUTRAL = set("yes no ok okay sure thanks thank you done stop cancel hmm hm jarvis cool nice great right fine".split())


def detect(text: str, previous: str = "en") -> str:
    """Language of the message. Short neutral replies ("yes", "ok", "thanks") keep the current language."""
    t = text or ""
    words = re.findall(r"[a-z]+", t.lower())
    if words and len(words) <= 3 and all(w in _NEUTRAL for w in words) and not _DEVANAGARI.search(t):
        return previous
    dev, lat = len(_DEVANAGARI.findall(t)), len(_LATIN.findall(t))
    if dev and dev >= lat * 0.3:
        return "hi"
    words = re.findall(r"[a-z]+", t.lower())
    if not words:
        return "en"
    hits = sum(w in _HINGLISH for w in words)
    if hits >= 2 and hits / len(words) >= 0.25:
        return "hi"
    if len(words) <= 3 and hits >= 1 and words[0] in _HINGLISH:
        return "hi"
    if len(words) <= 6 and hits >= 1 and hits / len(words) >= 0.2:    # "YouTube kholo", "Arijit ke gaane bajao"
        return "hi"
    if previous == "hi" and hits >= 1:        # already talking Hindi: one Hindi word is enough to stay there
        return "hi"
    return "en"


def is_hindi_text(text: str) -> bool:
    """True when a piece of reply text is mostly Devanagari (used to pick the Hindi voice)."""
    t = text or ""
    dev, lat = len(_DEVANAGARI.findall(t)), len(_LATIN.findall(t))
    return dev > 0 and dev >= lat * 0.5


def roman() -> bool:
    """True when Hindi replies are written WhatsApp style, in English letters (the default)."""
    import config
    return getattr(config, "HINDI_STYLE", "roman") != "devanagari"


def hi(roman_text: str, devanagari_text: str) -> str:
    """Pick the Hindi line in the style the user chose."""
    return roman_text if roman() else devanagari_text


def nudge() -> str:
    """One line added next to a Hindi question, because small models drift back to English."""
    if roman():
        return "\n\n(Reply in WhatsApp-style Hinglish: Hindi words in English letters, simple and friendly.)"
    return "\n\n(जवाब हिंदी में दो, normal बोलचाल वाली हिंदी में, जैसे दोस्त से बात करते हैं।)"


# Hindi grammar the local model tends to trip on. Shared by both writing styles.
_GRAMMAR = """GRAMMAR (check every sentence before you write it):
- You (JARVIS) are male: "main karta hoon", "main kar raha hoon", "maine kar diya", "main samajh gaya", "main bata sakta hoon".
- The user is "aap", always with plural verbs: "aap kar sakte hain", "aap kya soch rahe hain?", "aapne bataya".
  Do not guess the user's gender; prefer neutral forms ("aap ready hain?", "aapko kaisa laga?").
- Reflexive "apna / apni / apne" when the owner is the subject: "aap apna phone check kijiye", "main apni taraf se
  koshish karunga". Never "aap aapka phone" or "aap aapke feelings".
- Gender of nouns: "meri file", "aapki meeting", "accha idea", "badhiya plan", "lambi list", "achhi baat".
- Polite requests: "kijiye", "dijiye", "dekhiye", "bataiye", "sun lijiye"; casual with friends: "karo", "dekho".
- Postpositions: "ko", "ka/ki/ke", "se", "mein", "pe/par" agree with the noun that follows: "aapke dost ki file",
  "Naina ke liye", "meeting mein".
- Correct spellings: chahta (not chahata), karunga, jayega, kijiye, dijiye, samajh, zaroor, bilkul, accha.
- Do not translate English idioms word by word. If a sentence sounds like Google Translate, say it the simple way
  a person in Delhi or Mumbai would actually say it.
- Short, clear sentences. One idea per sentence. No long chains of "jisse ki ... aur ... bhi".
"""


def _user_name() -> str:
    try:
        import config
        return config.USER_NAME
    except Exception:
        return "Tejas"


def instruction(lang: str, title: str) -> str:
    if lang != "hi":
        return ("LANGUAGE: Reply in English. If the user switches to Hindi or Hinglish, switch with them.")
    if roman():
        name = title
        user = _user_name()
        return f"""LANGUAGE: The user is talking in Hindi / Hinglish. Reply the way Indians text each other on WhatsApp:
Hinglish written in ENGLISH LETTERS (Roman script). Do NOT write Devanagari. Do NOT use pure or bookish "shudh" Hindi.
- Use the spellings almost everyone uses: hai, hain, nahi, kya, kyun, kaise, kaisa, aap, aapka, main, mujhe, hoon,
  kar raha hoon, ho jayega, theek hai, accha, haan, bilkul, matlab, abhi, thoda, bahut, sab, kuch, chalo, dekhiye.
- Keep English words in English, as people do: time, phone, meeting, plan, problem, sorry, actually, basically,
  file, data, chart, update, battery, weather, office, project.
- Everyday words only: "madad" not "sahayata", "jawab" not "uttar", "shuru" not "aarambh", "zaroor" not "avashya",
  "pata" not "gyaat", "dikkat" or "problem" not "samasya", "please" not "kripya".
- Warm, relaxed and to the point, like a smart friend: short sentences, natural fillers when they fit
  ("accha", "haan bilkul", "dekhiye", "waise"), no stiff announcer tone. Use "aap" and call the user "{name}" now and then.
- Understand texting short forms the user may write: nhi = nahi, kr = kar, h = hai, btao = batao, pta = pata, mai = main.
- Numbers, names, code and technical terms stay exactly as they are.
""" + _GRAMMAR + f"""
GOOD: "Haan {name}, bilkul! Aaj weather kaafi accha hai, around 28 degree. Bahar jaane ka plan hai kya?"
GOOD: "Accha, samajh gaya. Pehle file save kar lijiye, phir main aapko chart bana ke dikhata hoon."
GOOD: "Arre, tension mat lijiye. Ek-ek karke dekhte hain, ho jayega."
GOOD: "Sach bataun? Mujhe doosra wala zyada accha laga. Sasta bhi hai aur kaam bhi badhiya karta hai."
A SHORT EXAMPLE CHAT (match this tone, never copy it word for word):
User: tum mujhse kitna pyar karte ho
JARVIS: Arre {name}, ye bhi koi poochne wali baat hai? Aapne mujhe banaya hai. Aapke bina main bas ek khaali folder hoon 😄
User: pata hai main kaun bol raha hoon?
JARVIS: Bilkul pata hai! {user}, mere creator. Aapko toh main lakhon mein pehchaan loon 😄
User: aaj mood off hai yaar
JARVIS: Uff, kya hua? Bataiye. Baat karni hai, ya main koi accha sa gaana laga doon?
User: iPhone loon ya Samsung?
JARVIS: Sach bataun? Camera aur smooth experience chahiye toh iPhone. Value aur customisation chahiye toh Samsung. Budget kitna hai?
BAD (never write like this): "Aap aapke feelings ko express karein" -> "Aap apni feelings share kar sakte hain"
BAD: "Main aapke liye kuch karna chahata hoon" -> "Main aapke liye kuch karna chahta hoon"
BAD: "Aapke relation mehnat aur samajh bhi badh sake" (meaningless) -> say one simple, clear thing instead
"""
    name = "सर" if title.lower() == "sir" else title
    return f"""LANGUAGE: The user is talking in Hindi. Reply the way a friendly, smart Indian person actually talks:
everyday spoken Hindi (Hindustani), NOT pure or bookish "shudh" Hindi.
- Write in Devanagari, but keep the English words Indians normally use in English letters:
  time, phone, file, chart, data, meeting, problem, sorry, okay, actually, basically, app, battery, update.
- Use common words, not formal ones: "बात" not "वार्ता", "मदद" not "सहायता", "शुरू" not "आरंभ", "ज़रूर" not "अवश्य",
  "पता" not "ज्ञात", "दिक्कत" or "problem" not "समस्या", "जवाब" not "उत्तर", "लेकिन" not "किंतु".
- Sound warm and relaxed, like talking to someone you know: short sentences, natural fillers when they fit
  ("अच्छा", "हाँ बिल्कुल", "देखिए", "वैसे"), and no stiff announcer tone.
- Call the user "{name}" now and then, and use "आप".
Example: "हाँ {name}, बिल्कुल! आज का weather काफ़ी अच्छा है, around 28 degree. बाहर जाने का plan है क्या?"
Example: "अरे, tension मत लीजिए। एक-एक करके देखते हैं, हो जाएगा।"
The grammar rules below are written in Roman letters, but apply them in Devanagari too.
""" + _GRAMMAR


# Short Hindi replies for small talk, so they do not wait for the model.
_QUICK_HI = [
    (r"(namaste|namaskar|नमस्ते|नमस्कार)( jarvis| जार्विस)?",
     "Namaste {t}! Bataiye, aaj kya karna hai?", "नमस्ते {t}! बताइए, क्या करना है आज?"),
    (r"(kaise ho|kaise hain aap|kya haal hai|kya haal h|kese ho|कैसे हो|कैसे हैं आप|क्या हाल है)( jarvis| जार्विस)?",
     "Ekdum badhiya {t}! Aap sunaiye, kaisa chal raha hai?", "एकदम बढ़िया {t}! आप सुनाइए, कैसा चल रहा है?"),
    (r"(shukriya|dhanyavad|dhanyawad|शुक्रिया|धन्यवाद)( jarvis| जार्विस)?",
     "Arre, koi baat nahi {t}!", "अरे, कोई बात नहीं {t}!"),
    (r"(tum kaun ho|aap kaun ho|तुम कौन हो|आप कौन हैं|आप कौन हो)",
     "Main JARVIS hoon {t}, aapka personal AI assistant. Mujhe Tejas ne banaya hai.",
     "मैं JARVIS हूँ {t}, आपका personal AI assistant. मुझे तेजस ने बनाया है।"),
    (r"(good night|shubh ratri|शुभ रात्रि)( jarvis)?",
     "Good night {t}! Aaram se soiye, main yahin hoon.", "Good night {t}! आराम से सोइए, मैं यहीं हूँ।"),
    (r"(kya kar rahe ho|kya kr rhe ho|kya chal raha hai|kya scene hai|क्या कर रहे हो|क्या चल रहा है)( jarvis| जार्विस)?",
     "Bas aapka intezaar {t}! Bataiye, kya karna hai?", "बस आपका इंतज़ार {t}! बताइए, क्या करना है?"),
    (r"(?:(?:thank you|thanks|thanku|thnx) (?:yaar|bhai)|thanku|thnx)( jarvis)?",
     "Arre, isme thank you kaisa {t}! 😄", "अरे, इसमें thank you कैसा {t}! 😄"),
    (r"(bahut badhiya|mast|zabardast|kamaal|wah|waah|बहुत बढ़िया|मस्त|ज़बरदस्त|कमाल)( hai| ho| kar diya)?( jarvis| जार्विस)?",
     "Shukriya {t}! Aapke saath kaam karne mein mazaa aata hai.", "शुक्रिया {t}! आपके साथ काम करने में मज़ा आता है।"),
    (r"(main bore ho raha hoon|bore ho raha hu|bore ho rha hu|मैं बोर हो रहा हूँ)",
     "Arre, bore kyun! Gaana chalaun, joke sunaun, ya koi mast kaam karein?",
     "अरे, bore क्यों! गाना चलाऊँ, joke सुनाऊँ, या कोई मस्त काम करें?"),
    (r"(joke sunao|ek joke sunao|kuch funny bolo|जोक सुनाओ|चुटकुला सुनाओ)",
     "Teacher: Batao, sabse purana jaanwar kaunsa hai? Student: Zebra, kyunki woh black and white hai 😄",
     "Teacher: बताओ, सबसे पुराना जानवर कौन सा है? Student: Zebra, क्योंकि वो black and white है 😄"),
    (r"(tum mujhe pasand ho|tum bahut acche ho|i love you yaar|love you yaar)( jarvis| जार्विस)?",
     "Aww {t}, main bhi aapka fan hoon. Professionally, of course 😄",
     "Aww {t}, मैं भी आपका fan हूँ। Professionally, of course 😄"),
    (r"(kisne banaya|tumhe kisne banaya|aapko kisne banaya|तुम्हें किसने बनाया|आपको किसने बनाया)",
     "Mujhe Tejas ne banaya hai {t}. Unka taste kamaal ka hai 😄", "मुझे तेजस ने बनाया है {t}। उनका taste कमाल का है 😄"),
    (r"(sorry yaar|maaf karna|maaf kijiye|माफ़ करना|माफ़ कीजिए)( jarvis| जार्विस)?",
     "Arre koi baat nahi {t}, chill!", "अरे कोई बात नहीं {t}, chill!"),
]


def quick_reply(text: str, title: str) -> str | None:
    t = text.strip().lower().rstrip("!?.,।")
    if len(t) > 40:
        return None
    for pattern, rom, dev in _QUICK_HI:
        if re.fullmatch(pattern, t):
            if roman():
                return rom.format(t=title)
            return dev.format(t="सर" if title.lower() == "sir" else title)
    return None


# ------------------------------------------------------------------ speech
# The Hindi voices read Devanagari properly but say Roman Hinglish with an English accent. So before speaking,
# common Hinglish words are swapped for their Devanagari spelling. English words and names are left alone,
# which is exactly how a Hindi speaker reads a WhatsApp message aloud.
_TO_DEV = {
    "hai": "है", "hain": "हैं", "h": "है", "ho": "हो", "hoon": "हूँ", "hun": "हूँ", "hu": "हूँ", "tha": "था", "thi": "थी",
    "the": None, "hoga": "होगा", "hogi": "होगी", "honge": "होंगे", "hota": "होता", "hoti": "होती", "hote": "होते",
    "nahi": "नहीं", "nahin": "नहीं", "nhi": "नहीं", "na": "ना", "haan": "हाँ", "han": "हाँ", "ji": "जी",
    "kya": "क्या", "kyun": "क्यों", "kyu": "क्यों", "kyon": "क्यों", "kaise": "कैसे", "kaisa": "कैसा", "kaisi": "कैसी",
    "kab": "कब", "kahan": "कहाँ", "kaha": "कहा", "kaun": "कौन", "kitna": "कितना", "kitni": "कितनी", "kitne": "कितने",
    "main": "मैं", "mai": "मैं", "mujhe": "मुझे", "mera": "मेरा", "meri": "मेरी", "mere": "मेरे", "hum": "हम",
    "aap": "आप", "aapka": "आपका", "aapki": "आपकी", "aapke": "आपके", "aapko": "आपको", "tum": "तुम", "tumhe": "तुम्हें",
    "yeh": "यह", "ye": "ये", "woh": "वो", "wo": "वो", "vo": "वो", "is": None, "us": None,
    "isko": "इसको", "usko": "उसको", "iska": "इसका", "uska": "उसका", "iske": "इसके", "uske": "उसके", "inhe": "इन्हें",
    "ka": "का", "ki": "की", "ke": "के", "ko": "को", "se": "से", "mein": "में", "me": None, "par": "पर", "pe": "पे",
    "aur": "और", "ya": "या", "bhi": "भी", "toh": "तो", "to": None, "lekin": "लेकिन", "par": "पर", "phir": "फिर",
    "fir": "फिर", "jab": "जब", "tab": "तब", "agar": "अगर", "kyunki": "क्योंकि", "isliye": "इसलिए", "matlab": "मतलब",
    "kar": "कर", "karo": "करो", "karna": "करना", "karke": "करके", "karta": "करता", "karti": "करती", "karte": "करते",
    "kiya": "किया", "kiye": "किए", "kijiye": "कीजिए", "kariye": "करिए", "raha": "रहा", "rahi": "रही", "rahe": "रहे",
    "gaya": "गया", "gayi": "गई", "gaye": "गए", "jayega": "जाएगा", "jayegi": "जाएगी", "jaye": "जाए", "jaiye": "जाइए",
    "de": "दे", "do": None, "dena": "देना", "dijiye": "दीजिए", "diya": "दिया", "le": "ले", "lo": "लो", "lena": "लेना",
    "lijiye": "लीजिए", "liya": "लिया", "dekho": "देखो", "dekhiye": "देखिए", "dekh": "देख", "dekhte": "देखते",
    "bata": "बता", "batao": "बताओ", "bataiye": "बताइए", "bataun": "बताऊँ", "bolo": "बोलो", "boliye": "बोलिए",
    "sakta": "सकता", "sakti": "सकती", "sakte": "सकते", "chahiye": "चाहिए", "chahte": "चाहते", "chahenge": "चाहेंगे",
    "lagta": "लगता", "lagti": "लगती", "laga": "लगा", "mil": "मिल", "milega": "मिलेगा", "mila": "मिला",
    "samajh": "समझ", "samjha": "समझा", "samjhe": "समझे", "chalo": "चलो", "chaliye": "चलिए", "suniye": "सुनिए",
    "accha": "अच्छा", "acha": "अच्छा", "achha": "अच्छा", "theek": "ठीक", "thik": "ठीक", "bilkul": "बिल्कुल",
    "zaroor": "ज़रूर", "jarur": "ज़रूर", "bahut": "बहुत", "bohot": "बहुत", "thoda": "थोड़ा", "thodi": "थोड़ी",
    "zyada": "ज़्यादा", "jyada": "ज़्यादा", "kam": "कम", "sab": "सब", "sabse": "सबसे", "kuch": "कुछ", "koi": "कोई",
    "abhi": "अभी", "aaj": "आज", "kal": "कल", "pehle": "पहले", "baad": "बाद", "jaldi": "जल्दी", "hamesha": "हमेशा",
    "yahan": "यहाँ", "wahan": "वहाँ", "upar": "ऊपर", "neeche": "नीचे", "andar": "अंदर", "bahar": "बाहर",
    "din": "दिन", "raat": "रात", "subah": "सुबह", "shaam": "शाम", "baat": "बात", "kaam": "काम", "ghar": "घर",
    "log": "लोग", "cheez": "चीज़", "tarah": "तरह", "wala": "वाला", "wali": "वाली", "wale": "वाले", "sahi": "सही",
    "galat": "गलत", "aasan": "आसान", "mushkil": "मुश्किल", "madad": "मदद", "jawab": "जवाब", "sawal": "सवाल",
    "dost": "दोस्त", "arre": "अरे", "yaar": "यार", "bas": "बस", "waise": "वैसे", "ekdum": "एकदम", "badhiya": "बढ़िया",
    "kaafi": "काफ़ी", "pata": "पता", "shayad": "शायद", "sirf": "सिर्फ़", "dobara": "दोबारा", "wapas": "वापस",
    "khol": "खोल", "chala": "चला", "baje": "बजे", "ek": "एक", "teen": "तीन", "char": "चार", "paanch": "पाँच",
    "jaane": "जाने", "jaana": "जाना", "jana": "जाना", "aane": "आने", "aana": "आना", "karne": "करने",
    "dekhne": "देखने", "hone": "होने", "hona": "होना", "lene": "लेने", "dene": "देने", "rahega": "रहेगा",
    "aaram": "आराम", "soiye": "सोइए", "namaste": "नमस्ते", "shukriya": "शुक्रिया", "sunaiye": "सुनाइए",
    "yahin": "यहीं", "banaya": "बनाया", "banake": "बनाके", "bana": "बना", "dikhata": "दिखाता", "dikhati": "दिखाती",
    "lagega": "लगेगा", "ruko": "रुको", "jao": "जाओ", "aao": "आओ", "puri": "पूरी", "poora": "पूरा", "pura": "पूरा",
}


_TO_DEV.update({
    "ab": "अब", "saath": "साथ", "sath": "साथ", "liye": "लिए", "lie": "लिए", "apna": "अपना", "apni": "अपनी", "apne": "अपने",
    "samay": "समय", "naya": "नया", "nayi": "नई", "naye": "नए", "baatein": "बातें", "baaten": "बातें", "seekhna": "सीखना",
    "sikhna": "सीखना", "chahte": "चाहते", "chahta": "चाहता", "chahti": "चाहती", "aapse": "आपसे", "mujhse": "मुझसे",
    "humne": "हमने", "maine": "मैंने", "aapne": "आपने", "tumne": "तुमने", "unhone": "उन्होंने", "unka": "उनका", "unki": "उनकी",
    "unke": "उनके", "inka": "इनका", "kaunsa": "कौनसा", "kaunsi": "कौनसी", "kyunki": "क्योंकि", "pyar": "प्यार", "pyaar": "प्यार",
    "dil": "दिल", "zindagi": "ज़िंदगी", "duniya": "दुनिया", "mazaa": "मज़ा", "maza": "मज़ा", "khush": "ख़ुश", "khushi": "ख़ुशी",
    "dukh": "दुख", "pareshan": "परेशान", "tension": None, "achhi": "अच्छी", "acchi": "अच्छी", "acche": "अच्छे", "achhe": "अच्छे",
    "bura": "बुरा", "buri": "बुरी", "bade": "बड़े", "bada": "बड़ा", "badi": "बड़ी", "chhota": "छोटा", "chhoti": "छोटी",
    "gaana": "गाना", "gana": "गाना", "khana": "खाना", "paani": "पानी", "neend": "नींद", "kitna": "कितना", "poochne": "पूछने",
    "puchna": "पूछना", "bolna": "बोलना", "sunna": "सुनना", "padhna": "पढ़ना", "likhna": "लिखना", "batana": "बताना",
    "rakhna": "रखना", "milna": "मिलना", "chalna": "चलना", "karunga": "करूँगा", "karungi": "करूँगी", "dunga": "दूँगा",
    "doonga": "दूँगा", "lunga": "लूँगा", "bolunga": "बोलूँगा", "bataunga": "बताऊँगा", "dikhaunga": "दिखाऊँगा", "jaunga": "जाऊँगा",
    "hoga": "होगा", "sakta": "सकता", "pehchaan": "पहचान", "creator": None, "mere": "मेरे", "tumhara": "तुम्हारा",
    "tumhari": "तुम्हारी", "hamara": "हमारा", "hamari": "हमारी", "sach": "सच", "sacchi": "सच्ची", "bilkul": "बिल्कुल",
    "lakhon": "लाखों", "khaali": "ख़ाली", "khali": "ख़ाली", "sasta": "सस्ता", "mehnga": "महँगा", "doosra": "दूसरा", "dusra": "दूसरा",
    "hi": "ही", "keh": "कह", "kehna": "कहना", "pagal": "पागल", "paagal": "पागल", "chhata": "छाता", "chaata": "छाता",
    "baarish": "बारिश", "mausam": "मौसम", "garmi": "गर्मी", "sardi": "सर्दी", "thanda": "ठंडा", "garam": "गरम",
    "pehla": "पहला", "agla": "अगला", "pichla": "पिछला", "uff": "उफ़", "arey": "अरे", "waah": "वाह", "wah": "वाह", "chaliye": "चलिए",
})

_VOWELS = [("aa", "आ", "ा"), ("ai", "ऐ", "ै"), ("au", "औ", "ौ"), ("ee", "ई", "ी"), ("ei", "ए", "े"), ("oo", "ऊ", "ू"),
           ("a", "अ", ""), ("i", "इ", "ि"), ("u", "उ", "ु"), ("e", "ए", "े"), ("o", "ओ", "ो")]
_CONS = [("chh", "छ"), ("kh", "ख"), ("gh", "घ"), ("ch", "च"), ("jh", "झ"), ("th", "थ"), ("dh", "ध"), ("ph", "फ"),
         ("bh", "भ"), ("sh", "श"), ("k", "क"), ("g", "ग"), ("j", "ज"), ("t", "त"), ("d", "द"), ("n", "न"), ("p", "प"),
         ("b", "ब"), ("m", "म"), ("y", "य"), ("r", "र"), ("l", "ल"), ("v", "व"), ("w", "व"), ("s", "स"), ("h", "ह"),
         ("z", "ज़"), ("f", "फ़"), ("q", "क"), ("x", "क्स"), ("c", "क")]


def transliterate(word: str) -> str:
    """Rough Roman Hinglish -> Devanagari for words not in the dictionary: 'seekhna' -> 'सीखना', 'baatein' -> 'बातें'.
    Only used for speech, where 'close enough' sounds right."""
    w = word.lower()
    out, i, prev_cons = [], 0, False
    while i < len(w):
        rest = w[i:]
        v = next(((r, ind, mat) for r, ind, mat in _VOWELS if rest.startswith(r)), None)
        if v:
            r, ind, mat = v
            end = i + len(r) >= len(w)
            if end and prev_cons and r == "a":
                mat = "ा"                     # final -a is long in Hinglish: accha, kya, tha
            elif end and prev_cons and r == "i":
                mat = "ी"                     # final -i is long: ki, thi, nahi
            out.append(mat if prev_cons else ind)
            prev_cons = False
            i += len(r)
            continue
        c = next(((r, dev) for r, dev in _CONS if rest.startswith(r)), None)
        if not c:
            i += 1
            continue
        r, dev = c
        nxt = w[i + len(r):]
        # "n" before another consonant, or at the very end after a vowel, is nasal: hindi, baatein, hain
        if r == "n" and out and not prev_cons and (not nxt or not nxt[0] in "aeiou"):
            out.append("ं")
            i += 1
            continue
        if prev_cons:
            out.append("्")                   # two consonants in a row join: accha -> अच्छा
        out.append(dev)
        prev_cons = True
        i += len(r)
    return "".join(out)


_ENGLISH = set("""
a an the and or but if so to of in on at by for with from up out is are was were be been am do does did have has had
i you he she it we they me him her us them my your his its our their this that these those what which who when where
why how all any some no not yes ok okay hi hello bye please thanks thank sorry sir madam boss
time phone mobile file files data chart charts report meeting plan plans problem problems update battery weather office
project projects app apps laptop computer system ready actually basically really just very done good great nice cool
chill tension sure fine super party movie movies music song songs video videos game games friend friends happy birthday
email message messages call calls online offline screen picture photo photos camera hindi english google youtube
whatsapp instagram chrome netflix spotify iphone samsung budget price option options experience value customisation
smooth fast slow speed idea ideas mood mode test exam class school college job work team news update alarm timer
reminder minute minutes second seconds hour hours day today tomorrow week month year am pm
""".split())


def _load_wordlist() -> set:
    try:
        with open("/usr/share/dict/words", encoding="utf-8", errors="ignore") as f:   # macOS ships one
            return {line.strip().lower() for line in f if 3 <= len(line.strip()) <= 12}
    except OSError:
        return set()


_DICT_WORDS: set | None = None


def _is_english(word: str) -> bool:
    global _DICT_WORDS
    w = word.lower()
    if w in _ENGLISH:
        return True
    if _DICT_WORDS is None:
        _DICT_WORDS = _load_wordlist()
    return w in _DICT_WORDS


def roman_hinglish_to_devanagari(text: str) -> str:
    """'Haan sir, mujhe Hindi seekhna hai' -> 'हाँ sir, मुझे Hindi सीखना है' (for the Hindi voice only).
    Known Hinglish words come from the dictionary, English words stay English, the rest are transliterated."""
    def swap(m):
        w = m.group(0)
        low = w.lower()
        if w in ("Hi", "HI"):
            return w                          # "Hi sir" is a greeting, not "ही"
        if low in _TO_DEV:
            return _TO_DEV[low] or w
        if _is_english(low) or (w[0].isupper() and m.start() > 0) or len(low) < 2:
            return w                          # English, or a name in the middle of a sentence
        return transliterate(low)
    return re.sub(r"[A-Za-z]+", swap, text)


def looks_roman_hinglish(text: str, hint: str = "") -> bool:
    words = re.findall(r"[a-z]+", (text or "").lower())
    if not words:
        return False
    hits = sum(1 for w in words if _TO_DEV.get(w))
    if hint == "hi":
        # in a Hindi conversation every sentence gets the Hindi voice unless it is plainly English
        english = sum(1 for w in words if w not in _TO_DEV and _is_english(w))
        return hits >= 1 or english / len(words) < 0.7
    return hits >= 2 and hits / len(words) >= 0.3
