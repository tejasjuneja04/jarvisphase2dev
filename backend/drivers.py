"""
Driver analysis: "what is associated with churn / attrition / profit / price?"

For every other column it computes ONE comparable strength score between 0 and 1:

    numeric target   x numeric column      |Pearson r|
    numeric target   x category column     correlation ratio (how much of the variation the groups explain)
    category target  x numeric column      correlation ratio
    category target  x category column     Cramer's V (bias-corrected)

and describes the pattern in plain numbers ("Yes rate: 42% for Month-to-month vs 3% for Two year").
These are ASSOCIATIONS. They say nothing about cause, and columns can overlap each other; the reports
say so every time.
"""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd

import analytics as A
from analytics import Dataset, human, pct

# names that usually mean "the outcome we care about"
TARGET_HINT = re.compile(
    r"(^|_)(churn|churned|attrition|default|defaulted|fraud|survived|survival|converted|conversion|"
    r"cancel|cancelled|canceled|returned|target|label|outcome|response|readmitted|exited|left)($|_)", re.I)
_POSITIVE = {"yes", "y", "true", "t", "1", "churned", "left", "exited", "fraud", "default", "positive", "cancelled",
             "canceled", "converted", "survived", "attrited", "1.0"}


# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------

def _is_category(ds: Dataset, col: str) -> bool:
    return ds.kinds.get(col) in ("categorical", "discrete")


def _usable_predictor(ds: Dataset, col: str, target: str) -> bool:
    if col == target or ds.kinds.get(col) not in ("numeric", "categorical", "discrete"):
        return False
    s = ds.df[col]
    if s.isna().mean() > 0.5:
        return False
    if _is_category(ds, col) and not (2 <= s.nunique() <= 30):
        return False
    return True


def _eta(values: pd.Series, groups: pd.Series) -> float:
    """Correlation ratio: share of the variation in `values` explained by `groups` (0..1, as a square root)."""
    d = pd.DataFrame({"v": values, "g": groups}).dropna()
    if len(d) < 10 or d["g"].nunique() < 2:
        return 0.0
    grand = d["v"].mean()
    ss_total = float(((d["v"] - grand) ** 2).sum())
    if ss_total == 0:
        return 0.0
    g = d.groupby("g", observed=True)["v"]
    ss_between = float((g.count() * (g.mean() - grand) ** 2).sum())
    return math.sqrt(max(0.0, min(1.0, ss_between / ss_total)))


def _cramers_v(a: pd.Series, b: pd.Series) -> float:
    d = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(d) < 10:
        return 0.0
    tab = pd.crosstab(d["a"], d["b"]).values.astype(float)
    if tab.shape[0] < 2 or tab.shape[1] < 2:
        return 0.0
    n = tab.sum()
    expected = tab.sum(1, keepdims=True) @ tab.sum(0, keepdims=True) / n
    with np.errstate(divide="ignore", invalid="ignore"):
        chi2 = np.nansum((tab - expected) ** 2 / expected)
    r, k = tab.shape
    phi2 = max(0.0, chi2 / n - (k - 1) * (r - 1) / (n - 1))
    rc, kc = r - (r - 1) ** 2 / (n - 1), k - (k - 1) ** 2 / (n - 1)
    denom = min(kc - 1, rc - 1)
    return math.sqrt(phi2 / denom) if denom > 0 else 0.0


def positive_class(s: pd.Series):
    """Which value counts as 'the event'. Yes/True/1 when present, otherwise the rarer value."""
    vals = list(s.dropna().unique())
    for v in vals:
        if str(v).strip().lower() in _POSITIVE:
            return v
    counts = s.value_counts()
    return counts.index[-1] if len(counts) else None


def detect_targets(ds: Dataset) -> list:
    """Two-level columns whose NAME says they are an outcome (churn, attrition, survived...)."""
    out = []
    for c in ds.df.columns:
        if ds.kinds.get(c) not in ("categorical", "discrete"):
            continue
        if ds.df[c].nunique() == 2 and TARGET_HINT.search(re.sub(r"[^a-z0-9]+", "_", str(c).lower())):
            out.append(c)
    return out


def target_kind(ds: Dataset, target: str) -> str:
    if ds.kinds.get(target) == "numeric":
        return "numeric"
    return "binary" if ds.df[target].nunique() == 2 else "category"


# ------------------------------------------------------------
# main analysis
# ------------------------------------------------------------

def drivers(ds: Dataset, target: str, top: int = 8) -> dict | None:
    key = f"drivers:{target}"
    if key in ds.cache:
        return ds.cache[key]

    df = ds.df
    if target not in df.columns or ds.kinds.get(target) not in ("numeric", "categorical", "discrete"):
        return None
    if df[target].notna().sum() < 30:
        return {"target": target, "too_small": True, "rows": []}

    kind = target_kind(ds, target)
    y = df[target]
    pos = positive_class(y) if kind == "binary" else None
    overall = float((y == pos).mean() * 100) if kind == "binary" else None

    rows = []
    for c in df.columns:
        if not _usable_predictor(ds, c, target):
            continue
        x = df[c]
        x_num = ds.kinds.get(c) == "numeric"

        if kind == "numeric":
            if x_num:
                d = pd.DataFrame({"x": x, "y": y}).dropna()
                if len(d) < 10 or d["x"].nunique() < 2:
                    continue
                r = float(d["x"].corr(d["y"]))
                strength, measure = abs(r), f"correlation {r:+.2f}"
                if abs(r) < 0.1:
                    pattern = "no meaningful straight-line relationship"
                else:
                    pattern = f"higher {c} goes with higher {target}" if r > 0 else \
                              f"higher {c} goes with lower {target}"
            else:
                strength = _eta(y, x)
                means = df.groupby(c, observed=True)[target].agg(["mean", "size"])
                means = means[means["size"] >= max(5, 0.02 * len(df))]
                if len(means) < 2:
                    continue
                hi, lo = means["mean"].idxmax(), means["mean"].idxmin()
                measure = f"groups explain {strength ** 2 * 100:.0f}% of variation"
                pattern = f"avg {human(means.loc[hi, 'mean'])} ({_clip(hi, 14)}) vs {human(means.loc[lo, 'mean'])} ({_clip(lo, 14)})"
        else:
            if x_num:
                strength = _eta(x, y)
                g = df.groupby(y, observed=True)[c].mean()
                measure = f"groups explain {strength ** 2 * 100:.0f}% of variation"
                if kind == "binary" and pos in g.index:
                    other = [i for i in g.index if i != pos][0]
                    pattern = f"avg {human(g[pos])} ({pos}) vs {human(g[other])} ({other})"
                else:
                    pattern = "averages differ between groups"
            else:
                strength = _cramers_v(x, y)
                measure = f"association {strength:.2f}"
                if kind == "binary":
                    tab = df.groupby(c, observed=True)[target].agg(
                        rate=lambda s: (s == pos).mean() * 100, n="size")
                    tab = tab[tab["n"] >= max(5, 0.02 * len(df))]
                    if len(tab) < 2:
                        continue
                    hi, lo = tab["rate"].idxmax(), tab["rate"].idxmin()
                    pattern = f"{pos} rate {tab.loc[hi, 'rate']:.0f}% ({_clip(hi, 14)}) vs {tab.loc[lo, 'rate']:.0f}% ({_clip(lo, 14)})"
                else:
                    pattern = "the mix of outcomes differs between groups"

        if strength == strength:   # not NaN
            rows.append({"column": c, "strength": float(strength), "measure": measure, "pattern": pattern,
                         "label": "strong" if strength >= 0.4 else "moderate" if strength >= 0.2 else "weak"})

    rows.sort(key=lambda r: -r["strength"])
    res = {"target": target, "kind": kind, "positive": pos, "overall_rate": overall, "rows": rows[:top],
           "n": int(y.notna().sum()), "too_small": False}
    ds.cache[key] = res
    return res


def rate_by_group(ds: Dataset, target: str, dim: str) -> pd.DataFrame | None:
    """Event rate for each level of `dim`, for a binary target."""
    if target_kind(ds, target) != "binary" or dim not in ds.df.columns:
        return None
    pos = positive_class(ds.df[target])
    tab = ds.df.groupby(dim, observed=True)[target].agg(rate=lambda s: (s == pos).mean() * 100, n="size")
    tab = tab[tab["n"] >= max(5, 0.02 * len(ds.df))]
    return tab.sort_values("rate", ascending=False) if len(tab) >= 2 else None


# ------------------------------------------------------------
# text report
# ------------------------------------------------------------

def report(ds: Dataset, target: str) -> str:
    from reports import _head, table   # local import: reports imports insights, which imports this module lazily

    res = drivers(ds, target)
    if res is None:
        return f"I can't analyse '{target}' as an outcome: it needs to be a numeric column or a column with a few categories."
    if res.get("too_small"):
        return f"'{target}' has too few filled-in values (under 30) for a reliable driver analysis."

    lines = [_head(f"WHAT IS ASSOCIATED WITH {target.upper()}")]
    if res["kind"] == "binary":
        lines.append(f"Event tracked: {target} = {res['positive']}, which is {pct(res['overall_rate'])} of "
                     f"{res['n']:,} records.")
    elif res["kind"] == "numeric":
        s = ds.df[target].dropna()
        lines.append(f"Outcome: {target} (mean {human(s.mean())}, median {human(s.median())}, {res['n']:,} records).")
    else:
        lines.append(f"Outcome: {target} ({ds.df[target].nunique()} categories, {res['n']:,} records).")

    if not res["rows"]:
        lines.append("\nNo other column could be compared with it reliably.")
        return "\n".join(lines)

    rows = [[_clip(r["column"], 20), f"{r['strength']:.2f}", r["label"], r["pattern"]] for r in res["rows"]]
    lines.append("")
    lines.append(table(rows, ["Column", "Strength", "Level", "Pattern"], "lrll"))

    top = res["rows"][0]
    strong = [r for r in res["rows"] if r["strength"] >= 0.2]
    lines.append("")
    lines.append("Observed:  " + (f"{top['column']} shows the strongest link ({top['measure']}): {top['pattern']}."
                                  if top["strength"] >= 0.1 else "No column shows more than a weak link."))
    lines.append("May mean:  These are associations, not causes. Columns can overlap (several may be measuring "
                 "the same underlying thing), and a pattern here may be explained by something not in this file."
                 + (f" Only {len(strong)} column(s) reach a moderate strength or more." if len(strong) < 3 else ""))
    if ds.rows < 200:
        lines.append(f"Caution:   with only {ds.rows} rows these strengths are unstable.")
    return "\n".join(lines)


def _clip(text, n=22):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"
