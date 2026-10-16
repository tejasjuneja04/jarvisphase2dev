"""
Insight engine.

Every finding keeps three layers strictly apart:

  detail   (OBSERVED)   what was measured, with the numbers. Always a fact.
  meaning  (MAY MEAN)   a hedged interpretation that also says what the data cannot tell us.
  action   (NEXT STEP)  a recommendation. It exists ONLY when the dataset's real context supports it;
                        otherwise it is None and nothing is suggested.

Context gates: a business recommendation needs a confidently detected business role (revenue, profit,
discount, customer...). Datasets without them only ever get data-hygiene and investigation steps.
"""

from __future__ import annotations

import calendar

import analytics as A
import drivers as DR
from analytics import Dataset, full_analysis, human, pct

SEVERITY_ORDER = {"critical": 0, "warning": 1, "positive": 2, "info": 3}
UNIT = {"MS": "month", "W": "week", "D": "day"}


def _ins(severity, title, observed, meaning=None, action=None, area="general"):
    return {
        "severity": severity,
        "title": title,
        "detail": observed,      # OBSERVED
        "meaning": meaning,      # MAY MEAN
        "action": action,        # NEXT STEP (None when the data does not justify one)
        "area": area,
    }


def build_insights(ds: Dataset) -> list:
    if "insights" in ds.cache:
        return ds.cache["insights"]

    res = full_analysis(ds)
    out: list = []

    out += _mode_note(ds)
    out += _sample_note(ds)
    out += _quality(ds, res)
    out += _time(ds, res)
    out += _margin(ds, res)
    out += _groups(ds, res)
    out += _discount(ds, res)
    out += _customers(ds, res)
    out += _outliers(ds, res)
    out += _skew(ds, res)
    out += _segments(ds, res)
    out += _outcome(ds)
    out += _correlation(ds, res)

    out.sort(key=lambda i: SEVERITY_ORDER[i["severity"]])
    ds.cache["insights"] = out
    return out


def top_actions(insights: list, n: int = 5) -> list:
    """Recommendations only, de-duplicated, most severe first."""
    seen, actions = set(), []
    for i in insights:
        a = i.get("action")
        if a and a not in seen:
            seen.add(a)
            actions.append(a)
        if len(actions) >= n:
            break
    return actions


# ------------------------------------------------------------
# CONTEXT NOTES
# ------------------------------------------------------------

def _mode_note(ds):
    if ds.primary_metric:
        return []
    return [_ins(
        "info", "Analysed as a profile dataset",
        "No revenue, sales or profit column was recognised. Column names are used exactly as in your file.",
        "Without a performance measure I can describe distributions, groups and relationships, but I cannot "
        "say whether anything is good or bad for a business.",
        "If one column is your main metric, tell me, for example: 'use income as main metric'."
        + (" With a metric and the date column I can also show trends and forecasts." if ds.date_col else ""),
        "general",
    )]


def _sample_note(ds):
    if ds.rows >= 100:
        return []
    return [_ins(
        "warning", "Small sample",
        f"The dataset has only {ds.rows} rows.",
        "Percentages, group differences and trends from so few rows can change a lot when a few records change. "
        "Treat every pattern below as tentative.",
        None, "quality",
    )]


# ------------------------------------------------------------
# DATA QUALITY
# ------------------------------------------------------------

def _quality(ds, res):
    p = res["profile"]
    out = []

    if p["missing_pct"] > 5 or p["high_missing_columns"]:
        worst = sorted(p["missing_by_col"].items(), key=lambda kv: -kv[1]["pct"])[:3]
        worst_txt = ", ".join(f"{c} ({v['pct']}%)" for c, v in worst)
        out.append(_ins(
            "warning", "Missing data needs attention",
            f"{pct(p['missing_pct'])} of all cells are empty. Most affected: {worst_txt}.",
            "Empty cells can mean 'not applicable', 'unknown' or a broken data feed. The data alone cannot say which, "
            "and the right way to handle them depends on that.",
            "Check with whoever owns the data what the gaps mean, then choose to fill or exclude them "
            "('clean data' fills numbers with the median and text with the most common value).",
            "quality",
        ))
    elif p["missing_total"] > 0:
        out.append(_ins(
            "info", "Minor missing values",
            f"{p['missing_total']:,} empty cells ({pct(p['missing_pct'])}).",
            "A small share; unlikely to change results much.", None, "quality",
        ))

    if p["duplicate_pct"] >= 0.5:
        order = ds.roles.get("order")
        if order:
            unique_after = bool(ds.df.drop_duplicates()[order].is_unique)
            meaning = (f"Every other {order} value appears exactly once, so these repeats are very likely "
                       "double-counted rows that inflate totals." if unique_after else
                       f"{order} values also repeat elsewhere (for example multi-line orders), so identical rows "
                       "could be genuine repeat purchases. Totals may or may not be inflated.")
        else:
            meaning = ("Without a unique ID column I cannot tell repeated exports from genuinely repeated "
                       "records, so totals may or may not be inflated.")
        out.append(_ins(
            "warning", "Identical rows found",
            f"{p['duplicates']:,} rows are exact copies of another row ({pct(p['duplicate_pct'])}).",
            meaning,
            "Confirm with the data source. If they are true duplicates, remove them ('clean data' does this).",
            "quality",
        ))
    return out


# ------------------------------------------------------------
# TIME
# ------------------------------------------------------------

def _period(ts, freq):
    try:
        if freq == "MS":
            return f"{calendar.month_abbr[ts.month]} {ts.year}"
        return ts.strftime("%d %b %Y")
    except Exception:
        return str(ts)


def _time(ds, res):
    t = res.get("time")
    if not t:
        return []
    st, freq, metric = t["stats"], t["freq"], t["metric"]
    unit = UNIT.get(freq, "period")
    n = st["periods"]
    short = n < 12
    tentative = f" Only {n} {unit}s of history, so treat this as tentative." if short else ""
    dim_hint = ds.dims[0] if ds.dims else None
    drill = f"Break {metric} down by {dim_hint} to see what is driving this." if dim_hint else None
    out = []

    if st["direction"] == "growing":
        yoy = f" Year over year: {pct(st['yoy_pct'], signed=True)}." if st["yoy_pct"] is not None else ""
        strong = st["r2"] >= 0.5
        out.append(_ins(
            "positive", f"{metric} is trending up",
            f"The fitted trend adds about {pct(abs(st['slope_pct']))} of the average per {unit} (R² = {st['r2']:.2f}).{yoy}",
            ("A clear, steady upward pattern." if strong else
             "A modest upward drift with a lot of noise between periods: the direction is probably real, "
             "its size is uncertain.") + " The data does not show why it is rising." + tentative,
            drill, "growth",
        ))
    elif st["direction"] == "declining":
        sev = "critical" if st["slope_pct"] <= -2 and st["r2"] >= 0.5 else "warning"
        out.append(_ins(
            sev, f"{metric} is trending down",
            f"The fitted trend loses about {pct(abs(st['slope_pct']))} of the average per {unit} (R² = {st['r2']:.2f}).",
            "The decline is visible in the data but not explained by it: it could reflect the business, "
            "seasonality, or a change in how data is recorded." + tentative,
            (drill + " Rule out one-off causes (stock-outs, pricing, tracking changes) before reacting.") if drill
            else "Rule out one-off causes (stock-outs, pricing, tracking changes) before reacting.",
            "growth",
        ))
    else:
        out.append(_ins(
            "info", f"{metric} shows no clear trend",
            f"No statistically clear direction over {n} {unit}s (R² = {st['r2']:.2f}).",
            "Values move around without a consistent rise or fall." + tentative, None, "growth",
        ))

    cp = st["change_pct"]
    if cp is not None and abs(cp) >= 15:
        out.append(_ins(
            "positive" if cp > 0 else "warning", f"Latest {unit} moved {pct(cp, signed=True)}",
            f"{_period(st['last_period'], freq)}: {human(st['last_value'])} compared with the {unit} before.",
            f"A single {unit} can move for one-off reasons, so this alone does not establish a trend.",
            f"Check whether something specific happened in that {unit} (promotion, holiday, outage)." if cp < 0 else None,
            "growth",
        ))

    seas = st["seasonality"]
    if seas and seas["peak_value"] >= 1.2:
        years = n / 12
        out.append(_ins(
            "info", "Repeating yearly pattern",
            f"{calendar.month_name[seas['peak_month']]} runs {pct((seas['peak_value'] - 1) * 100, 0)} above the "
            f"average month; {calendar.month_name[seas['trough_month']]} runs "
            f"{pct((1 - seas['trough_value']) * 100, 0)} below.",
            f"Consistent with seasonal demand, based on about {years:.1f} years of data. "
            + ("With under three years, a coincidence cannot be ruled out." if years < 3 else
               "The pattern has repeated across several years."),
            f"If the pattern holds, plan stock and staffing around {calendar.month_name[seas['peak_month']]}.",
            "growth",
        ))

    if st["cv"] == st["cv"] and st["cv"] > 0.5:
        out.append(_ins(
            "warning", f"{metric} swings a lot",
            f"Coefficient of variation is {st['cv']:.2f}.",
            f"Results change a lot from {unit} to {unit}, so comparing one {unit} with another is unreliable.",
            "Use rolling averages when setting targets.", "growth",
        ))

    if st["anomalies"]:
        a = st["anomalies"][0]
        out.append(_ins(
            "info", f"{len(st['anomalies'])} unusual {unit}(s)",
            f"Biggest: {_period(a['period'], freq)} at {human(a['value'])} versus about {human(a['expected'])} expected.",
            "Could be a one-off event (promotion, outage) or a data error. The data cannot tell which.",
            f"Look at what happened in the {unit}s flagged as unusual.", "growth",
        ))

    fc = t.get("forecast")
    if fc:
        f = fc["forecast"]
        h = len(f)
        recent = t["series"].iloc[-(h + 1):-1] if t["partial_last"] else t["series"].iloc[-h:]
        recent_total = float(recent.sum())
        next_total = float(f["yhat"].sum())
        delta = (next_total - recent_total) / recent_total * 100 if recent_total else None
        delta_txt = f", {pct(delta, signed=True)} versus the last {h}" if delta is not None else ""
        err = ""
        if fc["mape"] is not None:
            err = f" On recent history this method was off by about {pct(fc['mape'], 0)}" + \
                  (", so treat it as a rough guide." if fc["mape"] > 25 else ".")
        out.append(_ins(
            "info", f"Forecast: next {h} {unit}s",
            f"Expected {human(next_total)} of {metric}{delta_txt}.",
            f"Extends past trend{' and seasonality' if 'season' in fc['method'] else ''}, based on {n} {unit}s of "
            f"history. It assumes prices, promotions and market conditions stay similar.{err}",
            None, "forecast",
        ))
    return out


# ------------------------------------------------------------
# MARGIN
# ------------------------------------------------------------

def _margin(ds, res):
    k = {x["label"]: x for x in res["kpis"]}
    m = k.get("Profit margin")
    out = []
    if m:
        v = m["raw"]
        if v < 10:
            out.append(_ins(
                "warning", "Thin overall margin",
                f"Profit is {pct(v)} of revenue.",
                "Low in absolute terms, which leaves little buffer if costs rise or prices fall. Some industries "
                "run on thin margins by design; the data has no benchmark.",
                "Look at which categories or regions have the lowest margin before changing anything.",
                "profit",
            ))
        else:
            out.append(_ins(
                "info", "Overall margin",
                f"Profit is {pct(v)} of revenue.",
                "Whether that is healthy depends on your industry and targets; the data has no benchmark. "
                "Compare it with your own targets or earlier periods.",
                None, "profit",
            ))

    r = ds.roles
    if "profit" in r:
        s = ds.df[r["profit"]].dropna()
        if len(s):
            neg_share = (s < 0).mean() * 100
            if neg_share >= 10:
                loss = float(s[s < 0].sum())
                out.append(_ins(
                    "warning", "Many loss-making records",
                    f"{pct(neg_share)} of records have negative {r['profit']}, {human(loss)} in total.",
                    "Some loss-making sales are normal (promotions, loss leaders). A high share can point to "
                    "pricing or discount problems, but the data alone does not show which.",
                    "Check what the loss-making records have in common (category, region, discount level).",
                    "profit",
                ))
    return out


# ------------------------------------------------------------
# GROUPS
# ------------------------------------------------------------

def _groups(ds, res):
    out = []
    metric = res["metric"]
    for dim, g in list(res["groups"].items())[:4]:
        c = res["concentration"].get(dim)
        if c:
            if c["top1_share"] >= 50:
                out.append(_ins(
                    "warning", f"One {dim} dominates",
                    f"'{c['top1_name']}' accounts for {pct(c['top1_share'])} of {metric}.",
                    f"If that {dim} weakened, total {metric} would fall sharply. Whether that is a real risk depends on "
                    "how stable it is, which the data does not show.",
                    f"Check how stable '{c['top1_name']}' is and what the next-largest {dim} values could contribute.",
                    "concentration",
                ))
            elif c["items"] >= 10 and c["top20_share"] >= 80:
                out.append(_ins(
                    "info", f"A few {dim} values carry most of {metric}",
                    f"The top {c['top20_count']} of {c['items']} {dim} values (20%) make up {pct(c['top20_share'])}.",
                    "A concentrated distribution is common in sales data. The remaining values form a long tail of "
                    "small contributors; the data does not include what they cost to serve.",
                    None, "concentration",
                ))

        if "profit" in g and g["profit"].notna().any() and len(g) <= 400:
            losers = g[g["profit"] < 0].sort_values("profit")
            if len(losers):
                names = ", ".join(str(i) for i in losers.index[:3])
                out.append(_ins(
                    "critical", f"{len(losers)} {dim} value(s) have negative profit",
                    f"Combined loss {human(float(losers['profit'].sum()))}. Largest losses: {names}.",
                    "The data shows they lose money but not why, and cannot say whether it is intentional "
                    "(loss leader, launch pricing) or a problem.",
                    f"Find the cause for these {dim} values (price, discount or cost) before deciding what to do.",
                    "profit",
                ))

        if "margin" in g and len(g) >= 3:
            big = g[(g.get("share", 0) >= 5) & g["margin"].notna()]
            if len(big) >= 2:
                best, worst = big["margin"].idxmax(), big["margin"].idxmin()
                gap = float(big["margin"].max() - big["margin"].min())
                if gap >= 15:
                    out.append(_ins(
                        "warning", f"Margins differ widely across {dim}",
                        f"'{best}' earns {pct(big.loc[best, 'margin'])} while '{worst}' earns "
                        f"{pct(big.loc[worst, 'margin'])} ({gap:.0f} points apart).",
                        "Differences can come from pricing, costs or product mix. This data cannot separate them.",
                        f"Compare pricing, discounts and costs between '{best}' and '{worst}'.",
                        "profit",
                    ))
    return out


# ------------------------------------------------------------
# DISCOUNTS
# ------------------------------------------------------------

def _discount(ds, res):
    d = res.get("discount")
    if not d:
        return []
    table, corr, within = d["table"], d["corr"], d.get("within")
    out = []

    if "margin" in table and len(table) >= 3:
        first, last = table["margin"].iloc[0], table["margin"].iloc[-1]
        if first == first and last == last and (first - last) >= 15:
            heavy = table.index[-1]
            share = table["rows"].iloc[-1] / table["rows"].sum() * 100
            if within:
                if within["worse"] >= 0.75 * within["tested"]:
                    control = (f"The same drop appears inside {within['worse']} of {within['tested']} "
                               f"{within['dim']} groups, so it is not just a difference in product mix.")
                else:
                    control = (f"Inside {within['dim']} groups the drop shows up in only {within['worse']} of "
                               f"{within['tested']}, so product mix may explain part of it.")
            else:
                control = "I could not test whether product mix explains it."
            out.append(_ins(
                "warning", "Heavily discounted orders earn much lower margins",
                f"Margin is {pct(first)} with no discount and {pct(last)} at {heavy} discount; "
                f"{pct(share)} of orders sit in that band (discount-profit correlation {corr:.2f}).",
                "This is an association. Discounts may reduce margin directly, or heavily discounted orders "
                f"may differ in other ways. {control}",
                "Compare discounted and full-price orders within the same category before setting a discount cap.",
                "profit",
            ))
    elif corr == corr and corr < -0.3:
        out.append(_ins(
            "warning", "Discount and profit move in opposite directions",
            f"Correlation between {'discount'} and profit is {corr:.2f}.",
            "This is an association, not proof that discounts cause the lower profit.",
            "Compare discounted and full-price orders within the same category.", "profit",
        ))
    return out


# ------------------------------------------------------------
# CUSTOMERS
# ------------------------------------------------------------

def _customers(ds, res):
    r = res.get("rfm")
    if not r:
        return []
    s, gap = r["summary"], r.get("typical_gap_days")
    out = []

    def yardstick(recency):
        if gap:
            return (f"Their usual wait between orders is about {gap:.0f} days, so {recency:.0f} days of silence is "
                    f"{recency / gap:.1f} times longer than normal. They may have left, or may simply buy rarely.")
        return "Whether that counts as lapsed depends on how often these customers normally buy, which I could not measure."

    if "At Risk (high value)" in s.index:
        row = s.loc["At Risk (high value)"]
        if row["revenue_share"] >= 15:
            out.append(_ins(
                "warning", "Valuable customers have gone quiet",
                f"{int(row['customers'])} customers ({pct(row['customer_share'])}) generated {pct(row['revenue_share'])} "
                f"of revenue but have not bought for about {row['avg_recency']:.0f} days.",
                yardstick(row["avg_recency"]),
                "Consider contacting a small sample of the valuable customers who have gone quiet, and measure the response before scaling up.",
                "customers",
            ))
    if "Champions" in s.index:
        row = s.loc["Champions"]
        out.append(_ins(
            "info", "A small group of customers carries a large share of revenue",
            f"{int(row['customers'])} customers ({pct(row['customer_share'])}) generate {pct(row['revenue_share'])} of revenue.",
            "Losing a few of them would be noticeable. RFM is descriptive: it ranks past behaviour, not future loyalty.",
            None, "customers",
        ))
    if "Lost / Hibernating" in s.index:
        row = s.loc["Lost / Hibernating"]
        if row["customer_share"] >= 25:
            out.append(_ins(
                "info", "Many customers have not bought for a long time",
                f"{pct(row['customer_share'])} of customers are in the lowest recency and frequency group "
                f"({pct(row['revenue_share'])} of revenue).",
                yardstick(row["avg_recency"]),
                "Try a low-cost reactivation message on a sample and measure whether anyone responds.",
                "customers",
            ))
    return out


# ------------------------------------------------------------
# OUTLIERS, SKEW, SEGMENTS, CORRELATION (valid for any dataset)
# ------------------------------------------------------------

def _outliers(ds, res):
    o = res["outliers"]
    if not o:
        return []
    worst = sorted(o.items(), key=lambda kv: -kv[1]["pct"])[:3]
    heavy = [(c, v) for c, v in worst if v["pct"] >= 5]
    if heavy:
        txt = ", ".join(f"{c} {pct(v['pct'])}" for c, v in heavy)
        c0, v0 = heavy[0]
        return [_ins(
            "info", "Statistical outliers present",
            f"Share of values outside the usual range (IQR rule): {txt}. "
            f"For {c0} the upper limit is {human(v0['upper'])} and the highest value is {human(v0['max'])}.",
            "Outliers are unusual, not necessarily wrong. They can be genuine high or low values or entry errors; "
            "the data alone cannot tell which.",
            "Review the most extreme rows individually. Keep genuine ones, correct confirmed errors, "
            "and report mean and median together.",
            "quality",
        )]
    c, v = worst[0]
    return [_ins(
        "info", "A few statistical outliers",
        f"{c}: {v['count']:,} value{'s' if v['count'] != 1 else ''} outside the usual range "
        f"(highest {human(v['max'])}, median {human(v['median'])}).",
        "A small number; they may be genuine.", None, "quality",
    )]


def _skew(ds, res):
    rows = []
    for c, d in res["numeric"].items():
        if ds.kinds.get(c) != "numeric" or not d["median"]:
            continue
        ratio = d["mean"] / d["median"]
        if abs(d["skew"]) >= 1 and (ratio >= 1.3 or ratio <= 0.77):
            rows.append((abs(d["skew"]), c, d))
    rows.sort(reverse=True)
    out = []
    for _, c, d in rows[:2]:
        side = "high" if d["mean"] > d["median"] else "low"
        out.append(_ins(
            "info", f"{c}: mean {human(d['mean'])} versus median {human(d['median'])}",
            f"Half of all records are at or below {human(d['median'])}, while the average is {human(d['mean'])}.",
            f"A minority of unusually {side} values pulls the average away from the typical value, "
            "so the average alone describes the group poorly.",
            f"Report both the mean and the median for {c}.", "quality",
        ))
    return out


def _segments(ds, res):
    out = []
    for d in A.segment_differences(ds, limit=3):
        hi, lo = d["high"], d["low"]
        out.append(_ins(
            "info", f"{d['metric']} differs across {d['dim']}",
            f"Average {d['metric']} is {human(hi[1])} for {d['dim']} = {hi[0]} and {human(lo[1])} for "
            f"{d['dim']} = {lo[0]} (overall average {human(d['overall'])}).",
            f"A difference between groups does not prove that {d['dim']} causes it; other factors may explain it.",
            None, "segments",
        ))
    return out


def _outcome(ds):
    """If the file has an outcome column (churn, attrition, survived...), report what it is most linked to."""
    out = []
    for t in DR.detect_targets(ds)[:1]:
        res = DR.drivers(ds, t, top=3)
        if not res or res.get("too_small") or not res["rows"]:
            continue
        top = res["rows"][0]
        if top["strength"] < 0.2:
            out.append(_ins(
                "info", f"No column is strongly linked to {t}",
                f"{t} = {res['positive']} for {pct(res['overall_rate'])} of records. The strongest link found "
                f"({top['column']}) is only {top['label']} ({top['strength']:.2f}).",
                "The columns in this file do not separate the outcome well. What drives it may not be recorded here.",
                None, "drivers"))
        else:
            out.append(_ins(
                "info", f"{t} differs most by {top['column']}",
                f"{t} = {res['positive']} for {pct(res['overall_rate'])} of records overall. {top['pattern'].capitalize()}.",
                "A clear difference between groups, but an association only: other columns may overlap with it "
                "or explain it, and the file cannot show cause.",
                f"Say 'what drives {t}' to see every column ranked.", "drivers"))
    return out


_RELATED_ROLES = {"revenue", "profit", "cost", "quantity", "price"}


def _correlation(ds, res):
    pairs = res["correlations"]["pairs"]
    out = []
    for p in pairs:
        if abs(p["r"]) < 0.8:
            continue
        ra, rb = ds.metric_role(p["a"]), ds.metric_role(p["b"])
        if ra in _RELATED_ROLES and rb in _RELATED_ROLES:
            continue  # revenue/profit/cost/quantity are linked by definition, not a discovery
        direction = "rise together" if p["r"] > 0 else "move in opposite directions"
        out.append(_ins(
            "info", f"{p['a']} and {p['b']} are strongly related",
            f"Correlation {p['r']:.2f}: the two {direction}.",
            "A strong correlation shows they move together, not that one causes the other. "
            "They may also measure nearly the same thing.",
            None, "drivers",
        ))
        if len(out) >= 2:
            break
    return out
