"""
Business advisor.

The LLM never sees raw data and never does arithmetic. It receives a compact
sheet of facts that analytics.py already computed, and is told to use only those.
If the model is unavailable or fails, a rule-based briefing is returned instead.
"""

from __future__ import annotations

import calendar

import analytics as A
from analytics import Dataset, human, pct
from insights import UNIT, build_insights, top_actions
from reports import _head

ADVISOR_SYSTEM = """You are JARVIS, a careful data analyst advising a business owner.

STRICT RULES
1. Use ONLY the facts provided. Never invent numbers, columns, causes or context.
2. Refer to every column by its exact name from the fact sheet. Never rename a column or call it
   something else. In particular, never call a column revenue, sales or profit unless the fact sheet
   says that role was detected.
3. Do not assume the industry, business model, currency or units. Do not guess them from the file name.
4. An outlier is only an unusual value. Never call outliers errors, fraud or bad data without evidence;
   say they should be reviewed and may be genuine.
5. When the mean and median differ, mention both.
6. If something cannot be determined from the facts, say so plainly.
Write plain text with no markdown symbols (no asterisks, no hashes).
Every recommendation must cite a number that appears in the facts."""


# ------------------------------------------------------------
# FACT SHEET
# ------------------------------------------------------------

def facts_summary(ds: Dataset, max_chars: int = 4200) -> str:
    res = A.full_analysis(ds)
    p = res["profile"]
    lines = [f"DATASET: {ds.name}; {p['rows']:,} rows; {p['columns']} columns; data quality {p['quality_score']}/100",
             "INDUSTRY / CURRENCY: unknown. Do not assume.",
             "COLUMN NAMES (use exactly): " + ", ".join(f"{c} [{ds.kinds.get(c)}]" for c in list(ds.df.columns)[:40])]

    if ds.roles:
        lines.append("DETECTED ROLES: " + ", ".join(f"{r}={c}" for r, c in ds.roles.items()))
    else:
        lines.append("DETECTED ROLES: none.")
    if not ds.primary_metric:
        lines.append("NO revenue, sales or profit column was detected. Do NOT describe any column as revenue, "
                     "sales or profit. This is a profile of attributes, not a performance report.")

    stats = res["numeric"]
    num_bits = [f"{c}: mean {human(d['mean'])}, median {human(d['median'])}, min {human(d['min'])}, max {human(d['max'])}"
                for c, d in list(stats.items())[:8] if ds.kinds.get(c) == "numeric"]
    if num_bits:
        lines.append("NUMERIC COLUMNS: " + " | ".join(num_bits))
    mix = []
    for c in [c for c in ds.dims if 2 <= ds.df[c].nunique() <= 8][:4]:
        vc = ds.df[c].value_counts(normalize=True).head(4) * 100
        mix.append(f"{c}: " + ", ".join(f"{i} ({v:.0f}%)" for i, v in vc.items()))
    if mix:
        lines.append("GROUP MIX: " + " | ".join(mix))

    lines.append("KPIS: " + "; ".join(f"{k['label']}={k['display']}" for k in res["kpis"]))

    t = res.get("time")
    if t:
        st, freq = t["stats"], t["freq"]
        unit = UNIT.get(freq, "period")
        bits = [f"{t['metric']} trend={st['direction']} ({st['slope_pct']:+.1f}% of average per {unit}, R2 {st['r2']:.2f})"]
        if st["yoy_pct"] is not None:
            bits.append(f"YoY {st['yoy_pct']:+.1f}%")
        if st["change_pct"] is not None:
            bits.append(f"last {unit} {st['change_pct']:+.1f}% vs previous")
        if st["seasonality"]:
            s = st["seasonality"]
            bits.append(f"peak month {calendar.month_name[s['peak_month']]} (x{s['peak_value']:.2f}), "
                        f"weakest {calendar.month_name[s['trough_month']]} (x{s['trough_value']:.2f})")
        lines.append("TIME: " + "; ".join(bits))
        fc = t.get("forecast")
        if fc:
            lines.append(f"FORECAST next {fc['h']} {unit}s: total {human(fc['forecast']['yhat'].sum())}"
                         + (f"; backtest error ~{fc['mape']:.0f}%" if fc["mape"] is not None else ""))

    for dim, g in list(res["groups"].items())[:3]:
        parts = []
        for name, r in g.head(3).iterrows():
            s = f"{name}: {human(r['value'])}"
            if "share" in g:
                s += f" ({r['share']:.0f}%)"
            if "margin" in g and r.get("margin") == r.get("margin"):
                s += f", margin {r['margin']:.0f}%"
            parts.append(s)
        lines.append(f"TOP {dim.upper()}: " + " | ".join(parts) + f" (of {len(g)})")
        if len(g) >= 3:
            worst = g.tail(1)
            n = worst.index[0]
            lines.append(f"WEAKEST {dim.upper()}: {n}: {human(worst['value'].iloc[0])}")

    d = res.get("discount")
    if d and "margin" in d["table"]:
        lines.append("DISCOUNT BANDS margin: " + "; ".join(f"{b}={m:.0f}%" for b, m in d["table"]["margin"].items()))

    r = res.get("rfm")
    if r:
        lines.append("CUSTOMER SEGMENTS (share of revenue): " + "; ".join(
            f"{n}={x['revenue_share']:.0f}% ({int(x['customers'])} customers)" for n, x in r["summary"].iterrows()))

    lines.append("FINDINGS (observed = measured fact; interpretation = hedged reading):")
    for i in build_insights(ds)[:9]:
        line = f"- {i['title']}. Observed: {i['detail']}"
        if i.get("meaning"):
            line += f" Interpretation: {i['meaning']}"
        lines.append(line)

    text = "\n".join(lines)
    return text[:max_chars]


# ------------------------------------------------------------
# PROMPTS
# ------------------------------------------------------------

def advice_prompt(ds: Dataset, question: str | None = None) -> str:
    facts = facts_summary(ds)
    business = bool(ds.primary_metric)
    scope = ("" if business else
             "IMPORTANT: this dataset has no revenue, sales or profit measure. Do NOT give business strategy "
             "(pricing, marketing, discounts, growth). Recommend only data-quality checks and further analyses.\n")
    if question:
        task = (f"{scope}The user asks: \"{question}\"\n"
                "Answer directly from the facts. Keep observation and interpretation separate, state what the data "
                "cannot tell, and only give a next step if the facts support one.")
    else:
        task = (scope +
                "Write a briefing with exactly these labelled parts:\n"
                "OBSERVATIONS: three to four measured facts, each with its number.\n"
                "INTERPRETATION: what those facts may mean. Use cautious wording and say what is uncertain.\n"
                "NEXT STEPS: only actions the facts justify, each tied to a number. If the facts do not justify "
                "an action, say so instead of inventing one.\n"
                "LIMITS: what this data cannot tell us.\n"
                "Keep it under 300 words.")
    return f"FACTS\n{facts}\n\nTASK\n{task}"


def qa_prompt(ds: Dataset, question: str) -> str:
    cols = ", ".join(f"{c} ({ds.kinds.get(c)})" for c in list(ds.df.columns)[:30])
    sample = ds.df.head(3).astype(str).to_dict("records")
    return (f"FACTS\n{facts_summary(ds, 2600)}\n\nCOLUMNS: {cols}\nSAMPLE ROWS: {sample}\n\n"
            f"QUESTION: {question}\n"
            "Answer using only the facts above. If the answer needs a calculation that is not in the facts, "
            "say which calculation to run instead of guessing.")


# ------------------------------------------------------------
# ADVICE
# ------------------------------------------------------------

def rule_based_advice(ds: Dataset) -> str:
    ins = build_insights(ds)
    lines = [_head(f"BRIEFING: {ds.name}")]

    lines.append("OBSERVED")
    for i in [x for x in ins if x["severity"] in ("critical", "warning", "positive")][:4] or ins[:3]:
        lines.append(f"  • {i['title']}: {i['detail']}")

    means = [i for i in ins if i.get("meaning")][:3]
    if means:
        lines.append("")
        lines.append("WHAT IT MAY MEAN")
        lines += [f"  • {i['title']}: {i['meaning']}" for i in means]

    lines.append("")
    lines.append("NEXT STEPS (only where the data supports them)")
    actions = top_actions(ins, 5)
    if actions:
        lines += [f"  {n}. {a}" for n, a in enumerate(actions, 1)]
    else:
        lines.append("  The data does not support a specific action beyond the checks above.")
    return "\n".join(lines)


def strategic_advice(ds: Dataset, generate, question: str | None = None) -> str:
    """generate(system, user, max_tokens) -> str. Returns advice text."""
    rules = rule_based_advice(ds)
    try:
        import config
        prompt = advice_prompt(ds, question)
        text = generate(ADVISOR_SYSTEM, prompt, config.ADVICE_MAX_TOKENS)
        text = (text or "").strip()
        if len(text) < 80:
            return rules
    except Exception as e:
        print("Advisor LLM failed, using rule-based advice:", e)
        return rules

    if question:
        return text
    return (_head(f"AI STRATEGIC BRIEFING: {ds.name}") + "\n" + text
            + "\n\n(Figures come from calculations on your data. The wording and interpretation are written "
              "by the language model, so check them against the numbers before acting.)")
