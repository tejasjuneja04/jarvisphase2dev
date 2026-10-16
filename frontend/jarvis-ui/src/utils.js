/* Pure helpers (no React, no browser APIs) so they can be tested in Node. */

export const dataUrl = (data, mime) => `data:${mime};base64,${data}`;

/** Reports and tables contain box-drawing rules; those must not be re-flowed. */
export const isTable = (text = "") => /─{3,}/.test(text);

/* ------------------------------------------------------------------ */
/* Speech text (only used when the browser has to speak by itself)     */
/* ------------------------------------------------------------------ */

export const speakable = (t = "") => {
  let s = String(t)
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/```[\s\S]*$/g, " ")
    .replace(/[\u2500-\u257F]/g, "")
    .replace(/\p{Extended_Pictographic}/gu, "")
    .replace(/\[(\d+)\]/g, "")
    .replace(/https?:\/\/\S+/g, "a link")
    .replace(/[*_`#>]/g, "")
    .replace(/[•◈▲▼]/g, "")
    .replace(/(\d)\s?%/g, "$1 percent")
    .replace(/(\d+(?:\.\d+)?)\s?K\b/g, "$1 thousand")
    .replace(/(\d+(?:\.\d+)?)\s?M\b/g, "$1 million")
    .replace(/\s+/g, " ")
    .trim();
  if (s.length > 360) {
    s = s.slice(0, 360);
    const cut = Math.max(s.lastIndexOf(". "), s.lastIndexOf("! "), s.lastIndexOf("? "));
    if (cut > 120) s = s.slice(0, cut + 1);
  }
  return s;
};

/* ------------------------------------------------------------------ */
/* Wake word                                                           */
/* ------------------------------------------------------------------ */

// speech recognition often mishears the name, so accept the common variants
const WAKE =
  /\b(?:(?:hey|ok|okay|hi|hello|yo)[ ,]+)?(jarvis|jarvas|jarvice|jervis|jarves|jarvus|jarviz|jarvish|jaarvis|jarwis|javis|jar vis)\b[ ,.!:;-]*/i;
const DEV_NAME = /(?:हे\s*)?(जार्विस|जारविस|जार्वेस|जार्विश|जरविस|जार्विज़|जार्वीस)/g;

/** true when the name is in what was heard (any spelling the recogniser uses) */
export const hasWake = (transcript = "") => WAKE.test(transcript.replace(DEV_NAME, "jarvis"));

/**
 * @param transcript  what the recogniser heard
 * @param awake       true while we are inside the follow-up window after an answer
 * @returns {heard: boolean, command: string}  heard=false means ignore the utterance
 */
export function parseWake(transcript, awake = false) {
  // Hindi recognition writes the name in Devanagari
  const text = (transcript || "").replace(DEV_NAME, "jarvis").trim();
  if (!text) return { heard: false, command: "" };
  const m = WAKE.exec(text);
  if (!m) return awake ? { heard: true, command: text } : { heard: false, command: "" };
  const before = text.slice(0, m.index).replace(/[ ,.!:;-]+$/g, "").trim();
  const after = text.slice(m.index + m[0].length).trim();
  const command = [before, after].filter(Boolean).join(" ").trim();
  return { heard: true, command };
}

/* ------------------------------------------------------------------ */
/* Markdown (small, safe subset; no HTML is ever injected)             */
/* ------------------------------------------------------------------ */

const INLINE =
  /(`[^`\n]+`)|(\*\*[^*\n]+\*\*)|(__[^_\n]+__)|(\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))|(\[\d+(?:\s*,\s*\d+)*\])|(\*[^*\s][^*\n]*\*)|(https?:\/\/[^\s<>)]+)/g;

export function parseInline(text = "") {
  const out = [];
  let last = 0;
  let m;
  INLINE.lastIndex = 0;
  while ((m = INLINE.exec(text)) !== null) {
    if (m.index > last) out.push({ t: "text", v: text.slice(last, m.index) });
    const tok = m[0];
    if (m[1]) out.push({ t: "code", v: tok.slice(1, -1) });
    else if (m[2] || m[3]) out.push({ t: "bold", v: tok.slice(2, -2) });
    else if (m[4]) {
      const mm = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(tok);
      out.push({ t: "link", v: mm[1], href: mm[2] });
    } else if (m[5]) {
      out.push({ t: "cite", v: tok, n: tok.slice(1, -1).split(",").map((x) => parseInt(x, 10)) });
    } else if (m[6]) out.push({ t: "italic", v: tok.slice(1, -1) });
    else if (m[7]) out.push({ t: "link", v: tok.replace(/[.,;:!?]+$/, ""), href: tok.replace(/[.,;:!?]+$/, "") });
    last = m.index + tok.length;
    if (m[7] && /[.,;:!?]+$/.test(tok)) last -= tok.length - tok.replace(/[.,;:!?]+$/, "").length;
  }
  if (last < text.length) out.push({ t: "text", v: text.slice(last) });
  return out;
}

/** Split text into blocks: paragraphs, lists, headings, quotes and fenced code (also unfinished, while streaming). */
export function parseMarkdown(text = "") {
  const src = String(text).replace(/\r\n/g, "\n");
  const lines = src.split("\n");
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    const fence = /^\s*```\s*([\w+#.-]*)\s*$/.exec(line);
    if (fence) {
      const code = [];
      i++;
      while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) code.push(lines[i++]);
      const closed = i < lines.length;
      i++; // skip closing fence
      blocks.push({ type: "code", lang: fence[1] || "", code: code.join("\n"), open: !closed });
      continue;
    }

    if (!line.trim()) {
      i++;
      continue;
    }

    const h = /^\s{0,3}(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      blocks.push({ type: "h", level: h[1].length, inline: parseInline(h[2]) });
      i++;
      continue;
    }

    if (/^\s*[-*•]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(parseInline(lines[i].replace(/^\s*[-*•]\s+/, "")));
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    if (/^\s*\d+[.)]\s+/.test(line)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(parseInline(lines[i].replace(/^\s*\d+[.)]\s+/, "")));
        i++;
      }
      blocks.push({ type: "ol", items });
      continue;
    }

    if (/^\s*>\s?/.test(line)) {
      const q = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) q.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push({ type: "quote", inline: parseInline(q.join(" ")) });
      continue;
    }

    const para = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```/.test(lines[i]) &&
      !/^\s{0,3}#{1,4}\s/.test(lines[i]) &&
      !/^\s*[-*•]\s+/.test(lines[i]) &&
      !/^\s*\d+[.)]\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    blocks.push({ type: "p", inline: parseInline(para.join("\n")) });
  }
  return blocks;
}
