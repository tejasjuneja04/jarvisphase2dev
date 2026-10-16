"""
Human-readable text reports. The React UI renders these in a monospace,
pre-wrapped bubble, so simple aligned tables work well.
"""

from __future__ import annotations

import calendar
import re
import textwrap

import pandas as pd

import analytics as A
import drivers as DR
from analytics import Dataset, human, pct
from insights import UNIT, build_insights, top_actions

RULE = "─" * 44
ICON = {"critical": "🔴", "warning": "🟠", "positive": "🟢", "info": "🔵"}


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def table(rows: list, headers: list, align: str | None = None) -> str:
    """Aligned plain-text table. align: string of 'l'/'r' per column."""
    rows = [[str(c) for c in r] for r in rows]
    widths = [max(len(str(h)), *(len(r[i]) for r in rows)) if rows else len(str(h)) for i, h in enumerate(headers)]
    align = align or "l" * len(headers)

    def fmt(cells):
        out = []
        for i, c in enumerate(cells):
            out.append(c.rjust(widths[i]) if align[i] == "r" else c.ljust(widths[i]))
        return "  ".join(out).rstrip()

    lines = [fmt([str(h) for h in headers]), "  ".join("─" * w for w in widths)]
    lines += [fmt(r) for r in rows]
    return "\n".join(lines)


def _clip(text, n=22):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def _period(ts, freq):
    if freq == "MS":
        return f"{calendar.month_abbr[ts.month]} {ts.year}"
    return ts.strftime("%d %b %Y")


def _head(title):
    return f"{title}\n{RULE}"


def wrap_report(text: str, width: int = 82) -> str:
    """Wrap long prose lines in a report, but leave table rows and rules untouched."""
    out = []
    for line in text.split("\n"):
        stripped = line.strip()
        labelled = stripped.startswith(("Observed:", "May mean:", "Next step:"))
        if len(line) <= width or "─" in line or ("  " in stripped and not labelled):
            out.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        bullet = stripped.startswith(("•", "-")) or bool(re.match(r"^\d+\.", stripped))
        hang = 11 if labelled else (2 if bullet else 0)
        out.append(textwrap.fill(
            stripped, width=width,
            initial_indent=" " * indent,
            subsequent_indent=" " * (indent + hang),
            break_long_words=False, break_on_hyphens=False,
        ))
    return "\n".join(out)


# ------------------------------------------------------------
# dataset overview
# ------------------------------------------------------------

def overview(ds: Dataset) -> str:
    p = A.profile(ds)
    lines = [_head(f"DATASET LOADED: {ds.name}")]
    lines.append(f"{p['rows']:,} rows × {p['columns']} columns  ·  {p['memory_mb']} MB")
    lines.append(f"Data quality score: {p['quality_score']}/100 (based only on missing values, duplicates and constant columns)")
    lines.append("")

    kc = p["kind_counts"]
    lines.append(
        f"Column types: {kc.get('numeric', 0)} numeric, {kc.get('categorical', 0)} categorical, "
        f"{kc.get('discrete', 0)} coded (few whole-number values), "
        f"{kc.get('datetime', 0) + kc.get('year', 0)} date/year, {kc.get('id', 0) + kc.get('text', 0)} id/text"
        + (f", {kc.get('sparse', 0)} mostly empty" if kc.get("sparse") else "")
    )
    targets = DR.detect_targets(ds)
    if targets:
        lines.append(f"Outcome column noticed: {', '.join(targets)}. Ask 'what drives {targets[0]}' to see what is "
                     "associated with it.")

    lines.append("")
    if ds.roles:
        lines.append("HOW I READ YOUR COLUMNS")
        lines.append(role_table(ds))
        lines.append("Wrong? Tell me, e.g. 'use profit as revenue' or 'clear roles'.")
    else:
        lines.append("No business columns (revenue, profit, dates...) were recognised, so every column keeps "
                     "its own name and I won't assume what it means.")
    if not ds.primary_metric:
        lines.append("There is no main metric yet. If one column is your key measure, say "
                     "'use <column> as main metric'.")

    if p["notes"]:
        lines.append("")
        lines.append("Auto-fixes applied while loading:")
        lines += [f"  • {n}" for n in p["notes"][:6]]

    issues = []
    if p["missing_total"]:
        issues.append(f"{p['missing_total']:,} missing cells ({pct(p['missing_pct'])})")
    if p["duplicates"]:
        issues.append(f"{p['duplicates']:,} duplicate rows")
    if p["constant_columns"]:
        issues.append(f"{len(p['constant_columns'])} constant columns")
    lines.append("")
    lines.append("Issues: " + ("; ".join(issues) if issues else "none found"))

    ks = A.kpis(ds)[:6]
    if ks:
        lines.append("")
        lines.append("HEADLINE NUMBERS")
        lines.append(table([[k["label"], k["display"]] for k in ks], ["Metric", "Value"], "lr"))

    lines.append("")
    lines.append("Try: 'business analysis', 'show dashboard', 'forecast', 'clean data', or ask about any column.")
    return "\n".join(lines)


def role_table(ds: Dataset) -> str:
    basis = {"high": "matched by name", "medium": "assumed (weak match)", "you": "set by you"}
    rows = []
    for role, col in ds.roles.items():
        conf = ds.role_info.get(role, {}).get("confidence", "high")
        rows.append([role, _clip(col, 26), basis.get(conf, conf)])
    return table(rows, ["Role", "Column", "Basis"], "lll")


def roles_report(ds: Dataset) -> str:
    lines = [_head("HOW I READ YOUR COLUMNS")]
    if ds.roles:
        lines.append(role_table(ds))
    else:
        lines.append("No business roles assigned. Columns are used exactly as named.")
    if ds.primary_metric:
        how = "added up" if ds.is_additive(ds.primary_metric) else "averaged"
        lines.append(f"\nMain metric: {ds.primary_metric} (values are {how} across groups)")
    lines.append("\nChange it with: 'use <column> as revenue|profit|cost|date|customer|product|category|region|"
                 "quantity|discount|order', 'use <column> as main metric', or 'clear roles'.")
    return "\n".join(lines)


def schema(ds: Dataset) -> str:
    rows = []
    for c in ds.df.columns:
        s = ds.df[c]
        rows.append([_clip(c, 26), ds.kinds.get(c, "?"), f"{s.isna().mean() * 100:.1f}%", f"{s.nunique():,}"])
    return _head("COLUMNS") + "\n" + table(rows, ["Column", "Type", "Missing", "Unique"], "llrr")


# ------------------------------------------------------------
# business analysis
# ------------------------------------------------------------

def profile_analysis(ds: Dataset) -> str:
    """For datasets with no revenue/profit: describe the columns exactly as they are."""
    res = A.full_analysis(ds)
    lines = [_head(f"DATASET ANALYSIS: {ds.name}")]
    lines.append("No revenue, sales or profit column was recognised, so I am describing your columns as "
                 "they are rather than measuring business performance. If one column is your main metric, "
                 "say 'use <column> as main metric'.")

    ks = res["kpis"]
    if ks:
        lines.append("")
        lines.append(table([[k["label"], k["display"]] for k in ks], ["Metric", "Value"], "lr"))

    num = res["numeric"]
    cols = [c for c in ds.num_cols if c in num][:10]
    if cols:
        lines.append("")
        lines.append(_head("NUMERIC COLUMNS"))
        out = res["outliers"]
        rows = []
        for c in cols:
            d = num[c]
            rows.append([_clip(c, 18), human(d["mean"]), human(d["median"]), human(d["std"]),
                         human(d["min"]), human(d["max"]), pct(out[c]["pct"]) if c in out else "0%"])
        lines.append(table(rows, ["Column", "Mean", "Median", "Std", "Min", "Max", "Outliers"], "lrrrrrr"))

    if ds.date_col and ds.num_cols:
        rows = []
        for c in ds.num_cols[:4]:
            t = A.time_analysis(ds, c)
            if t:
                st, unit = t["stats"], UNIT.get(t["freq"], "period")
                rows.append([_clip(c, 18), st["direction"], pct(st["slope_pct"], signed=True) + f"/{unit}",
                             f"{st['r2']:.2f}", human(st["last_value"])])
        if rows:
            lines.append("")
            lines.append(_head(f"CHANGE OVER TIME (by {ds.date_col})"))
            lines.append(table(rows, ["Column", "Direction", "Trend", "R²", "Latest"], "llrrr"))
            lines.append("Trend is the fitted change as a share of the average. Say 'forecast <column>' to project one.")

    groups = [c for c in ds.dims if 2 <= ds.df[c].nunique() <= 12][:6]
    if groups:
        lines.append("")
        lines.append(_head("GROUP MIX"))
        for c in groups:
            vc = ds.df[c].value_counts(normalize=True).head(5) * 100
            coded = " (coded values)" if ds.kinds.get(c) == "discrete" else ""
            lines.append(f"{c}{coded}: " + ", ".join(f"{i} ({v:.0f}%)" for i, v in vc.items()))

    seg = res["segments"]
    if seg:
        sp = seg[0]
        lines.append("")
        lines.append(_head(f"AVERAGES BY {sp['dim'].upper()}"))
        m = list(sp["means"].columns)[:4]
        rows = [[str(idx), f"{int(sp['counts'][idx]):,}"] + [human(sp["means"].loc[idx, c]) for c in m]
                for idx in sp["means"].index]
        lines.append(table(rows, [sp["dim"], "Rows"] + [_clip(c, 10) for c in m], "lr" + "r" * len(m)))

    lines.append("")
    lines.append(_head("FINDINGS  (observed → may mean → next step)"))
    lines.append(insights_body(ds, limit=8))

    actions = top_actions(build_insights(ds), 5)
    if actions:
        lines.append("")
        lines.append(_head(NEXT_STEPS_TITLE))
        lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
    return "\n".join(lines)


def business_analysis(ds: Dataset) -> str:
    if not ds.primary_metric:
        return profile_analysis(ds)
    res = A.full_analysis(ds)
    lines = [_head(f"BUSINESS ANALYSIS: {ds.name}")]
    if ds.roles:
        lines.append("Columns used: " + ", ".join(f"{r} = {c}" for r, c in ds.roles.items()
                                                    if r in ("revenue", "profit", "cost", "date", "quantity", "discount")))
        lines.append("(say 'show roles' to review or change how I read your columns)")
        lines.append("")

    ks = res["kpis"]
    if ks:
        lines.append(table([[k["label"], k["display"]] for k in ks], ["Metric", "Value"], "lr"))

    t = res.get("time")
    if t:
        st, freq = t["stats"], t["freq"]
        unit = UNIT.get(freq, "period")
        lines.append("")
        lines.append(_head(f"PERFORMANCE OVER TIME ({t['metric']})"))
        lines.append(f"Direction: {st['direction']}  (trend {pct(st['slope_pct'], signed=True)} of average per {unit}, R² {st['r2']:.2f})")
        if st["yoy_pct"] is not None:
            lines.append(f"Year over year: {pct(st['yoy_pct'], signed=True)}")
        if st["change_pct"] is not None:
            lines.append(f"Latest {unit}: {human(st['last_value'])} ({pct(st['change_pct'], signed=True)} vs previous)")
        lines.append(f"Best {unit}: {_period(st['best_period'], freq)} ({human(st['best_value'])})")
        lines.append(f"Weakest {unit}: {_period(st['worst_period'], freq)} ({human(st['worst_value'])})")
        if t["partial_last"]:
            lines.append(f"Note: the final {unit} is incomplete and was excluded from trend and forecast.")

    for dim, g in list(res["groups"].items())[:3]:
        lines.append("")
        lines.append(_head(f"TOP {dim.upper()}"))
        lines.append(_group_table(g.head(6), dim))

    d = res.get("discount")
    if d:
        lines.append("")
        lines.append(_head("DISCOUNT IMPACT"))
        t_ = d["table"]
        rows = []
        for band, r in t_.iterrows():
            rows.append([band, f"{int(r['rows']):,}", human(r["avg_profit"]),
                         pct(r["margin"]) if "margin" in t_ else "-"])
        lines.append(table(rows, ["Discount", "Orders", "Avg profit", "Margin"], "lrrr"))

    lines.append("")
    lines.append(_head("FINDINGS  (observed → may mean → next step)"))
    lines.append(insights_body(ds, limit=7))

    actions = top_actions(build_insights(ds), 5)
    if actions:
        lines.append("")
        lines.append(_head(NEXT_STEPS_TITLE))
        lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]

    return "\n".join(lines)


def _group_table(g: pd.DataFrame, label: str = "Name", value_label: str = "Value") -> str:
    cols = [label, value_label]
    has_share = "share" in g
    has_margin = "margin" in g and g["margin"].notna().any()
    if has_share:
        cols.append("Share")
    if has_margin:
        cols.append("Margin")
    rows = []
    for name, r in g.iterrows():
        row = [_clip(name), human(r["value"])]
        if has_share:
            row.append(pct(r["share"]))
        if has_margin:
            row.append(pct(r["margin"]) if pd.notna(r["margin"]) else "-")
        rows.append(row)
    return table(rows, cols, "l" + "r" * (len(cols) - 1))


def insights_body(ds: Dataset, limit: int = 10) -> str:
    """Each finding keeps observation, interpretation and recommendation apart."""
    ins = build_insights(ds)[:limit]
    if not ins:
        return "Nothing notable stands out."
    out = []
    for i in ins:
        block = [f"{ICON[i['severity']]} {i['title']}", f"   Observed:  {i['detail']}"]
        if i.get("meaning"):
            block.append(f"   May mean:  {i['meaning']}")
        if i.get("action"):
            block.append(f"   Next step: {i['action']}")
        out.append("\n".join(block))
    return "\n\n".join(out)


NEXT_STEPS_TITLE = "SUGGESTED NEXT STEPS (only where the data supports them)"


def insights_report(ds: Dataset) -> str:
    ins = build_insights(ds)
    lines = [_head(f"FINDINGS: {ds.name}")]
    lines.append("Each finding shows what was observed, what it may mean, and a next step only if one is justified.")
    lines.append("")
    lines.append(insights_body(ds, limit=12))
    actions = top_actions(ins, 6)
    lines.append("")
    lines.append(_head(NEXT_STEPS_TITLE))
    if actions:
        lines += [f"{i}. {a}" for i, a in enumerate(actions, 1)]
    else:
        lines.append("The data does not support a specific action beyond the checks above.")
    return "\n".join(lines)


# ------------------------------------------------------------
# forecast / segments / stats
# ------------------------------------------------------------

def forecast_report(ds: Dataset, metric: str | None = None) -> str:
    if metric is None and not ds.primary_metric:
        if ds.date_col and ds.num_cols:
            cols = ", ".join(ds.num_cols[:6])
            return (f"I can forecast any numeric column over time, but I don't know which one you mean. "
                    f"Try: forecast {ds.num_cols[0]}. Numeric columns: {cols}.")
        return ("I can't forecast this dataset: it needs a date column and a numeric column to project.")
    t = A.time_analysis(ds, metric)
    if not t or not t.get("forecast"):
        return ("I can't forecast that. A forecast needs a date column, a numeric column and at least six "
                "time periods of history.")
    fc, freq, metric = t["forecast"], t["freq"], t["metric"]
    unit = UNIT.get(freq, "period")
    f = fc["forecast"]

    rows = [[_period(idx, freq), human(r["yhat"]), f"{human(r['lo'])} – {human(r['hi'])}"]
            for idx, r in f.iterrows()]
    lines = [_head(f"FORECAST: {metric}, next {len(f)} {unit}s")]
    lines.append(table(rows, [unit.title(), "Expected", "95% range"], "lrr"))
    lines.append("")
    lines.append(f"Total expected: {human(f['yhat'].sum())}")
    lines.append(f"Method: {fc['method']}")
    if fc["mape"] is not None:
        lines.append(f"Backtest: on recent history this method was off by about {pct(fc['mape'], 0)} on average.")
    lines.append("This is a statistical projection, not a guarantee. Promotions, pricing changes and market shocks are not modelled.")
    return "\n".join(lines)


def segments_report(ds: Dataset) -> str:
    r = A.rfm(ds)
    if not r:
        return ("Customer segmentation needs a customer column, a date column and a revenue column "
                "with at least 20 customers.")
    s = r["summary"]
    rows = [[name, f"{int(x['customers']):,}", pct(x["customer_share"]), human(x["revenue"]),
             pct(x["revenue_share"]), f"{x['avg_recency']:.0f}d"] for name, x in s.iterrows()]
    lines = [_head("CUSTOMER SEGMENTS (RFM)")]
    lines.append(table(rows, ["Segment", "Customers", "% cust", "Revenue", "% rev", "Last buy"], "lrrrrr"))
    lines.append("")
    lines.append("RFM scores every customer on how recently, how often and how much they buy.")
    return "\n".join(lines)


def correlation_report(ds: Dataset) -> str:
    pairs = A.correlations(ds, top=10)["pairs"]
    if not pairs:
        return "I need at least two numeric columns to compute correlations."
    rows = [[_clip(p["a"], 20), _clip(p["b"], 20), f"{p['r']:+.2f}", _strength(p["r"])] for p in pairs]
    return (_head("STRONGEST CORRELATIONS") + "\n"
            + table(rows, ["Column A", "Column B", "r", "Strength"], "llrl")
            + "\n\nCorrelation shows columns move together; it does not prove one causes the other.")


def _strength(r):
    a = abs(r)
    if a >= 0.8:
        return "very strong"
    if a >= 0.6:
        return "strong"
    if a >= 0.4:
        return "moderate"
    if a >= 0.2:
        return "weak"
    return "negligible"


def outlier_report(ds: Dataset) -> str:
    o = A.outliers(ds)
    if not o:
        return "No outliers detected using the standard IQR rule."
    rows = [[_clip(c, 22), f"{v['count']:,}", pct(v["pct"]), human(v["lower"]), human(v["upper"]), human(v["max"])]
            for c, v in sorted(o.items(), key=lambda kv: -kv[1]["pct"])[:10]]
    return (_head("OUTLIERS (IQR RULE)") + "\n"
            + table(rows, ["Column", "Count", "% rows", "Lower fence", "Upper fence", "Max"], "lrrrrr"))


def describe_column(ds: Dataset, col: str) -> str:
    s = ds.df[col]
    lines = [_head(f"COLUMN: {col}")]
    lines.append(f"Type: {ds.kinds.get(col, '?')}  ·  missing {s.isna().mean() * 100:.1f}%  ·  {s.nunique():,} unique")
    if ds.kinds.get(col) == "year":
        d = s.dropna()
        lines.append(f"Ranges from {int(d.min())} to {int(d.max())}; the most common is {int(d.mode().iloc[0])}.")
    elif ds.kinds.get(col) == "numeric":
        d = s.dropna()
        lines.append(table(
            [["Mean", human(d.mean())], ["Median", human(d.median())], ["Std dev", human(d.std())],
             ["Min", human(d.min())], ["Max", human(d.max())], ["Sum", human(d.sum())],
             ["Skew", f"{d.skew():.2f}"]],
            ["Stat", "Value"], "lr"))
    else:
        if ds.kinds.get(col) == "discrete":
            lines.append("These look like codes or categories, so I count them instead of averaging them.")
        vc = s.value_counts().head(8)
        lines.append(table([[_clip(i, 26), f"{v:,}", pct(v / len(s) * 100)] for i, v in vc.items()],
                           ["Value", "Count", "Share"], "lrr"))
    return "\n".join(lines)


# ------------------------------------------------------------
# cleaning
# ------------------------------------------------------------

def cleaning_report(report: dict, path) -> str:
    lines = [_head("DATA CLEANING COMPLETE")]
    lines.append(f"Rows: {report['original_rows']:,} → {report['final_rows']:,}   "
                 f"Columns: {report['original_columns']} → {report['final_columns']}")
    lines.append("")
    if report["steps"]:
        lines.append("What I did:")
        lines += [f"  • {s}" for s in report["steps"]]
    if report["imputed"]:
        lines.append("")
        lines.append("Missing values filled:")
        for c, v in list(report["imputed"].items())[:10]:
            lines.append(f"  • {c}: {v['count']:,} filled ({v['method']})")
    if report["dropped_columns"]:
        lines.append("")
        lines.append("Dropped columns: " + ", ".join(report["dropped_columns"][:8]))
    lines.append("")
    lines.append(f"Saved as: {path}")
    lines.append("Your original file was not modified. The cleaned CSV is attached below for download.")
    return "\n".join(lines)


# ------------------------------------------------------------
# spoken summaries (short, no tables)
# ------------------------------------------------------------

def speech_analysis(ds: Dataset) -> str:
    ins = build_insights(ds)
    crit = [i for i in ins if i["severity"] in ("critical", "warning")]
    good = [i for i in ins if i["severity"] == "positive"]
    parts = [f"Analysis of {ds.name} is complete."]
    if good:
        parts.append(good[0]["title"] + ".")
    if crit:
        parts.append(f"I found {len(crit)} issue{'s' if len(crit) != 1 else ''} worth attention, starting with: {crit[0]['title']}.")
    parts.append("The full report and charts are on screen.")
    return " ".join(parts)
