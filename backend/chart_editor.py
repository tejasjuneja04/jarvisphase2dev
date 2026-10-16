"""
Editable charts.

Every chart made here carries a "spec" (what to plot and how it looks). The UI's chart editor and plain
sentences both change that spec and redraw the chart:

    "plot revenue by region"            new chart
    "make it a line chart"              chart type
    "make it gold" / "use #ff8800"      colours (also palettes: arc, gold, crimson, ocean, sunset, mono, vibrant)
    "show the average instead"          aggregation (sum, average, count, median, max, min)
    "top 5 only" / "sort ascending"     limits and order
    "use profit instead" / "group by category"   metric and grouping
    "title it Sales 2024"               title
    "light theme" / "show values"       look
"""

from __future__ import annotations

import re
import uuid

import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

import config
from analytics import human
from models import Reply

KINDS = ("bar", "hbar", "line", "area", "pie", "donut", "scatter", "histogram")
AGGS = {"sum": "sum", "total": "sum", "average": "mean", "avg": "mean", "mean": "mean", "count": "count",
        "number": "count", "median": "median", "max": "max", "maximum": "max", "highest": "max",
        "min": "min", "minimum": "min", "lowest": "min"}
PALETTES = {
    "stark": config.THEME["palette"],
    "arc": ["#5cc8ff", "#3b9eff", "#9be7ff", "#2f6bff", "#7dd3fc", "#1e40af", "#bae6fd", "#60a5fa"],
    "gold": ["#e8c47c", "#c99a3c", "#f5d48a", "#a87b2a", "#fde7b0", "#8a6420", "#f0b95a", "#d9b25f"],
    "crimson": ["#e0524a", "#b3261e", "#f28b82", "#8c1d18", "#ffb4ab", "#e8c47c", "#5f1410", "#ff7a6b"],
    "ocean": ["#06b6d4", "#0ea5e9", "#14b8a6", "#22d3ee", "#0284c7", "#2dd4bf", "#155e75", "#67e8f9"],
    "sunset": ["#f97316", "#fb7185", "#facc15", "#ef4444", "#f472b6", "#fdba74", "#e11d48", "#fde047"],
    "mono": ["#e9edf4", "#b8c0cf", "#8b93a5", "#5f6778", "#cbd5e1", "#475569", "#f8fafc", "#64748b"],
    "vibrant": ["#5cc8ff", "#f472b6", "#facc15", "#4ade80", "#a78bfa", "#fb923c", "#f87171", "#2dd4bf"],
}
COLORS = {
    "red": "#e0524a", "crimson": "#c0302a", "blue": "#3b9eff", "cyan": "#5cc8ff", "teal": "#14b8a6",
    "green": "#4ade80", "gold": "#e8c47c", "yellow": "#facc15", "orange": "#fb923c", "purple": "#a78bfa",
    "violet": "#8b5cf6", "pink": "#f472b6", "white": "#e9edf4", "grey": "#94a3b8", "gray": "#94a3b8",
    "black": "#111827", "navy": "#1e3a8a", "silver": "#cbd5e1", "maroon": "#7f1d1d", "lime": "#a3e635",
    "lal": "#e0524a", "neela": "#3b9eff", "hara": "#4ade80", "peela": "#facc15", "kala": "#111827",
    "लाल": "#e0524a", "नीला": "#3b9eff", "हरा": "#4ade80", "पीला": "#facc15",
}
THEMES = {
    "dark": {"bg": config.THEME["bg"], "panel": config.THEME["panel"], "grid": config.THEME["grid"],
             "text": config.THEME["text"], "muted": config.THEME["muted"], "title": config.THEME["gold"]},
    "light": {"bg": "#ffffff", "panel": "#ffffff", "grid": "#e5e7eb", "text": "#111827", "muted": "#6b7280",
              "title": "#111827"},
}

CHART_WORD = re.compile(r"\b(chart|graph|plot|bars?|pie|donut|line|colou?rs?|legend|axis|title|it)\b", re.I)
CREATE_RE = re.compile(
    r"^(?:please\s+)?(?:plot|chart|graph|draw|visuali[sz]e|show(?: me)?(?: a)?(?: chart| graph| plot)?(?: of)?|make(?: me)? a (?:chart|graph|plot) of)\s+"
    r"(?:the\s+)?(?P<agg>total|sum|average|avg|mean|count|number|median|max|min)?\s*(?:of\s+)?(?P<y>.+?)\s+(?:by|per|across|for each|over)\s+(?P<x>.+?)"
    r"(?:\s+as\s+(?:a\s+|an\s+)?(?P<kind>[a-z ]+?)(?:\s+chart|\s+graph)?)?\s*$", re.I)


def _kind_from(text: str) -> str | None:
    t = text.lower()
    if re.search(r"horizontal|sideways", t):
        return "hbar"
    for k, words in (("donut", "donut|doughnut|ring"), ("pie", "pie"), ("line", r"line|trend"), ("area", "area"),
                     ("scatter", r"scatter|dots?"), ("histogram", r"histogram|distribution"), ("bar", r"bar|column")):
        if re.search(rf"\b(?:{words})\b", t):
            return k
    return None


def default_spec(ds, x=None, y=None) -> dict:
    df = ds.df
    dims = [c for c, k in ds.kinds.items() if k in ("categorical", "discrete")]
    dates = [c for c, k in ds.kinds.items() if k == "datetime"]
    nums = [c for c, k in ds.kinds.items() if k == "numeric"]
    y = y or ds.primary_metric or (nums[0] if nums else None)
    x = x or (dims[0] if dims else (dates[0] if dates else None))
    kind = "line" if x in dates else "bar"
    return {"kind": kind, "x": x, "y": y, "agg": "sum" if y else "count", "top": 12, "sort": "desc",
            "palette": "stark", "color": None, "title": None, "values": False, "theme": "dark"}


def _aggregate(df: pd.DataFrame, spec: dict) -> pd.Series:
    x, y, agg = spec["x"], spec.get("y"), spec.get("agg") or "sum"
    if pd.api.types.is_datetime64_any_dtype(df[x]):
        span = (df[x].max() - df[x].min()).days if df[x].notna().any() else 0
        freq = "D" if span <= 62 else ("W" if span <= 370 else "MS")
        g = df.set_index(x).resample(freq)
        s = g.size() if agg == "count" or not y else getattr(g[y], agg)()
        return s.dropna()
    s = df.groupby(x, observed=True).size() if agg == "count" or not y else df.groupby(x, observed=True)[y].agg(agg)
    s = s.dropna()
    if spec.get("sort") == "asc":
        s = s.sort_values()
    elif spec.get("sort") != "none":
        s = s.sort_values(ascending=False)
    top = int(spec.get("top") or 0)
    return s.head(top) if top > 0 else s


def render(ds, spec: dict) -> dict:
    """Draw a chart from a spec. Returns the UI chart dict (with the spec, so it can be edited again)."""
    counting = spec.get("agg") == "count" and not spec.get("y")
    spec = {**default_spec(ds), **{k: v for k, v in spec.items() if v not in (None, "")}}
    if counting:
        spec["y"] = None
    df, kind = ds.df, spec["kind"] if spec["kind"] in KINDS else "bar"
    th = THEMES.get(spec.get("theme"), THEMES["dark"])
    pal = list(PALETTES.get(spec.get("palette"), PALETTES["stark"]))
    if spec.get("color"):
        pal = [spec["color"]] + [c for c in pal if c != spec["color"]]
    if kind in ("histogram", "scatter") and not spec.get("y"):
        raise ValueError("pick a numeric column for this chart type")
    for col in (spec.get("x"), spec.get("y")):
        if col and col not in df.columns:
            raise ValueError(f"there is no column called '{col}'")

    agg_word = {"sum": "Total", "mean": "Average", "count": "Count", "median": "Median", "max": "Max", "min": "Min"}
    y, x = spec.get("y"), spec.get("x")
    title = spec.get("title") or (
        f"Distribution of {y}" if kind == "histogram" else
        f"{y} vs {x}" if kind == "scatter" else
        f"Number of rows by {x}" if not y else f"{agg_word.get(spec['agg'], '')} {y} by {x}".strip())

    fig = Figure(figsize=(9.0, 4.6), dpi=130, facecolor=th["bg"])
    ax = fig.subplots()
    ax.set_facecolor(th["panel"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(th["grid"])
    ax.tick_params(colors=th["muted"], labelsize=8, length=3)
    ax.set_title(title, color=th["title"], fontsize=12, fontweight="bold", loc="left", pad=12)
    fmt = FuncFormatter(lambda v, _: human(v, 1))

    def grid(axis="y"):
        ax.grid(axis=axis, color=th["grid"], linewidth=0.6, alpha=0.8)
        ax.set_axisbelow(True)

    if kind == "histogram":
        vals = df[y].dropna()
        ax.hist(vals, bins=min(40, max(8, int(np.sqrt(len(vals))))), color=pal[0], edgecolor=th["bg"], linewidth=0.5)
        grid()
        ax.xaxis.set_major_formatter(fmt)
    elif kind == "scatter":
        d = df[[x, y]].dropna()
        d = d.sample(3000, random_state=1) if len(d) > 3000 else d
        ax.scatter(d[x], d[y], color=pal[0], alpha=0.6, s=14, edgecolors="none")
        grid("both")
        ax.set_xlabel(spec.get("xlabel") or x, color=th["muted"], fontsize=8)
        ax.set_ylabel(spec.get("ylabel") or y, color=th["muted"], fontsize=8)
    else:
        s = _aggregate(df, spec)
        if s.empty:
            raise ValueError("nothing to plot with those settings")
        is_time = pd.api.types.is_datetime64_any_dtype(df[x])
        long_span = is_time and len(s) > 1 and (s.index.max() - s.index.min()).days > 62
        labels = [i.strftime("%b %Y" if long_span else "%d %b") if hasattr(i, "strftime") else str(i) for i in s.index]
        labels = [lb if len(lb) <= 16 else lb[:15] + "…" for lb in labels]
        if kind in ("pie", "donut"):
            s = s.clip(lower=0).head(10)
            ax.set_facecolor(th["bg"])
            wedges, *_ = ax.pie(s.values, labels=labels[:len(s)], colors=pal * 3, startangle=90, counterclock=False,
                                autopct="%1.0f%%" if spec.get("values") else None, pctdistance=0.78,
                                wedgeprops={"width": 0.42 if kind == "donut" else 1, "edgecolor": th["bg"], "linewidth": 2},
                                textprops={"color": th["text"], "fontsize": 8})
            ax.axis("equal")
        elif kind in ("line", "area"):
            xs = range(len(s))
            ax.plot(xs, s.values, color=pal[0], linewidth=2.2, marker="o" if len(s) <= 24 else None, markersize=4)
            if kind == "area":
                ax.fill_between(xs, s.values, color=pal[0], alpha=0.22)
            step = max(1, len(s) // 12)
            ax.set_xticks(list(xs)[::step])
            ax.set_xticklabels(labels[::step], rotation=30 if len(s) > 6 else 0, ha="right" if len(s) > 6 else "center",
                               fontsize=7.5)
            grid()
            ax.yaxis.set_major_formatter(fmt)
            if spec.get("values") and len(s) <= 24:
                for i, v in enumerate(s.values):
                    ax.annotate(human(v, 1), (i, v), textcoords="offset points", xytext=(0, 7), ha="center",
                                color=th["text"], fontsize=7)
        elif kind == "hbar":
            s = s.iloc[::-1]
            colors = [spec["color"]] * len(s) if spec.get("color") else (pal * 3)[:len(s)][::-1]
            ax.barh([labels[len(labels) - 1 - i] for i in range(len(s))], s.values, color=colors, height=0.62)
            grid("x")
            ax.xaxis.set_major_formatter(fmt)
            if spec.get("values"):
                for i, v in enumerate(s.values):
                    ax.text(v, i, " " + human(v, 1), va="center", color=th["text"], fontsize=7.5)
        else:
            colors = [pal[0]] * len(s) if (spec.get("color") or spec.get("palette") in (None, "stark")) else (pal * 3)[:len(s)]
            ax.bar(labels, s.values, color=colors, width=0.64)
            rot = 30 if len(s) > 5 else 0
            ax.tick_params(axis="x", rotation=rot)
            for lb in ax.get_xticklabels():
                lb.set_ha("right" if rot else "center")
            grid()
            ax.yaxis.set_major_formatter(fmt)
            if spec.get("values"):
                for i, v in enumerate(s.values):
                    ax.text(i, v, human(v, 1), ha="center", va="bottom", color=th["text"], fontsize=7.5)
        if kind not in ("pie", "donut"):
            if spec.get("xlabel"):
                ax.set_xlabel(spec["xlabel"], color=th["muted"], fontsize=8)
            if spec.get("ylabel"):
                ax.set_ylabel(spec["ylabel"], color=th["muted"], fontsize=8)

    import base64
    import io
    fig.tight_layout(pad=1.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    spec["title"] = spec.get("title") or None
    cid = spec.get("id") or f"edit_{uuid.uuid4().hex[:8]}"
    spec["id"] = cid
    return {"id": cid, "title": title, "caption": f"{ds.name} · editable", "spec": spec,
            "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}


# ------------------------------------------------------------ sentences

_SUGGEST = ["Make it a line chart", "Show the average instead", "Top 5 only", "Use the gold palette"]


def _col(ds, phrase: str):
    phrase = re.sub(r"^(?:the|my|total|average|avg|mean)\s+", "", phrase.strip(), flags=re.I)
    return ds.find_column(phrase)


def handle(session, text: str) -> Reply | None:
    ds = session.dataset
    if ds is None:
        return None
    t = text.strip().rstrip(".!")
    low = t.lower()

    m = CREATE_RE.match(t)
    if m:
        counting = re.fullmatch(r"(?:the\s+)?(?:count|number(?: of \w+)?|rows|records|how many)", m.group("y").strip().lower())
        y = None if counting else _col(ds, m.group("y"))
        x = _col(ds, m.group("x"))
        if x and (y or counting) and x != y:
            spec = default_spec(ds, x=x, y=y)
            if m.group("agg"):
                spec["agg"] = AGGS[m.group("agg").lower()]
            if counting:
                spec["y"], spec["agg"] = None, "count"
            if m.group("kind"):
                spec["kind"] = _kind_from(m.group("kind")) or spec["kind"]
            return _draw(session, spec, "Here you are. Ask me to change the colours, type or metric, or use ✎ Edit.")

    spec = dict(session.last_chart or {})
    age = getattr(session, "chart_age", 99)
    session.chart_age = age + 1
    # edits apply to the last chart: right after it was drawn, or whenever the sentence mentions a chart
    if not spec or not (age <= 3 or CHART_WORD.search(low)) or len(t) > 90 or "?" in t:
        return None

    changed = []
    kind = _kind_from(re.sub(r"\b(?:chart|graph|plot)\b", "", low)) if re.search(
        r"\b(?:make|change|turn|switch|convert|as|into|to)\b", low) else None
    if kind and kind != spec.get("kind"):
        spec["kind"] = kind
        changed.append(f"{kind} chart")
    hexes = re.findall(r"#[0-9a-f]{6}\b", low)
    named = [COLORS[w] for w in re.findall(r"[\wऀ-ॿ]+", low) if w in COLORS]
    pal = next((p for p in PALETTES if re.search(rf"\b{p}\b", low) and re.search(r"palette|theme|colou?rs|style", low)), None)
    if pal:
        spec["palette"], spec["color"] = pal, None
        changed.append(f"{pal} palette")
    elif hexes or named:
        spec["color"] = (hexes or named)[0]
        changed.append("colour")
    am = re.search(r"\b(sum|total|average|avg|mean|count|median|max(?:imum)?|min(?:imum)?|highest|lowest)\b(?!\s+\d)", low)
    if am and re.search(r"instead|show|use|make|change|switch", low) and not re.search(r"\btop\b", low):
        spec["agg"] = AGGS.get(am.group(1), spec.get("agg"))
        changed.append(f"{am.group(1)} values")
    tm = re.search(r"\b(?:top|only|first|show)\s+(\d{1,3})\b", low)
    if tm:
        spec["top"] = int(tm.group(1))
        changed.append(f"top {tm.group(1)}")
    if re.search(r"\b(?:show all|all of them|everything)\b", low):
        spec["top"] = 0
        changed.append("all values")
    if re.search(r"ascending|smallest first|lowest first", low):
        spec["sort"] = "asc"
        changed.append("ascending order")
    elif re.search(r"descending|largest first|biggest first|highest first", low):
        spec["sort"] = "desc"
        changed.append("descending order")
    if re.search(r"\blight (?:theme|mode|background)|white background", low):
        spec["theme"] = "light"
        changed.append("light theme")
    elif re.search(r"\bdark (?:theme|mode|background)|black background", low):
        spec["theme"] = "dark"
        changed.append("dark theme")
    if re.search(r"\b(?:show|add)\s+(?:the\s+)?(?:values|numbers|labels|data labels|percentages)", low):
        spec["values"] = True
        changed.append("value labels")
    elif re.search(r"\b(?:hide|remove)\s+(?:the\s+)?(?:values|numbers|labels|data labels|percentages)", low):
        spec["values"] = False
        changed.append("no value labels")
    tt = re.search(r"\b(?:title|rename|call|name)\s+(?:it|the chart|the graph|this)?\s*(?:to|as)?\s*[\"“']?(.+?)[\"”']?$", t, re.I)
    if tt and re.match(r"^(?:now\s+)?(?:set the title|title|rename|call|name)\b", low) and len(tt.group(1)) > 1:
        spec["title"] = tt.group(1).strip()
        changed.append("title")
    gm = re.search(r"\b(?:group(?:ed)? by|by|per|split by)\s+([\w ]+?)(?:\s+instead)?$", low)
    if gm and ds.find_column(gm.group(1)) and not tt:
        spec["x"] = ds.find_column(gm.group(1))
        changed.append(f"grouped by {spec['x']}")
    um = re.search(r"\b(?:use|show|plot)\s+([\w ]+?)\s+instead\b|\bchange (?:the )?(?:metric|value) to\s+([\w ]+)$", low)
    if um:
        col = ds.find_column((um.group(1) or um.group(2) or "").strip())
        if col and col != spec.get("x"):
            spec["y"] = col
            if spec.get("agg") == "count":
                spec["agg"] = "sum"
            changed.append(f"metric {col}")
    if not changed:
        return None
    return _draw(session, spec, "Done: " + ", ".join(changed) + ".")


def _draw(session, spec: dict, text: str) -> Reply:
    try:
        chart = render(session.dataset, spec)
    except Exception as e:
        return Reply(f"I couldn't draw that, {session.title}: {e}")
    session.last_chart = chart["spec"]
    session.chart_age = 0
    return Reply(text, charts=[chart], speech=text, suggestions=_SUGGEST)


def apply_edit(session, spec: dict) -> dict:
    """From the UI editor. Returns the redrawn chart (raises ValueError with a readable message)."""
    if session.dataset is None:
        raise ValueError("load a dataset first")
    clean = {k: spec.get(k) for k in ("kind", "x", "y", "agg", "top", "sort", "palette", "color", "title",
                                      "xlabel", "ylabel", "values", "theme", "id")}
    if clean.get("color") and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(clean["color"])):
        clean["color"] = None
    chart = render(session.dataset, clean)
    session.last_chart = chart["spec"]
    return chart
