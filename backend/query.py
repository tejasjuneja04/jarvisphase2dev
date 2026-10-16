"""
"Ask your data": answers simple questions by computing them with pandas.

    total sales in West
    average profit by category
    top 5 products by revenue
    which region has the lowest margin
    how many customers

Anything this module can't compute returns None and the caller falls back to the
LLM, which is given computed facts (never raw guesses).
"""

from __future__ import annotations

import re

import pandas as pd

import analytics as A
from analytics import Dataset, human
from reports import _clip, _group_table, table

AGG_WORDS = [
    ("how many", "count"), ("number of", "count"), ("count", "count"),
    ("average", "mean"), ("avg", "mean"), ("mean", "mean"), ("median", "median"),
    ("total", "sum"), ("sum", "sum"),
    ("maximum", "max"), ("max", "max"), ("highest", "max"), ("biggest", "max"), ("largest", "max"),
    ("minimum", "min"), ("min", "min"), ("lowest", "min"), ("smallest", "min"),
]
AGG_LABEL = {"count": "Count", "mean": "Average", "median": "Median", "sum": "Total", "max": "Maximum", "min": "Minimum"}


def _filters(ds: Dataset, low: str) -> list:
    """Category values mentioned in the sentence, e.g. 'west' -> (Region, 'West')."""
    found = []
    for col in ds.cat_cols:
        if len(found) >= 2:
            break
        if A._is_numeric(ds.df[col]):
            continue  # coded columns (1/2/3): a number in a sentence ("top 3") must not become a filter
        s = ds.df[col].dropna()
        if s.nunique() > 150:
            continue
        for v in s.astype(str).unique():
            vl = v.lower()
            if len(vl) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(vl)}(?![a-z0-9])", low):
                found.append((col, v))
                break
    return found


def quick_query(ds: Dataset, text: str) -> str | None:
    low = text.lower().strip().rstrip("?.! ")
    df = ds.df

    cols = ds.columns_in_text(text)
    nums = [c for c in cols if ds.is_metric(c)]
    cats = [c for c in cols if c not in nums and ds.kinds.get(c) in ("categorical", "discrete", "id", "text")]
    filters = _filters(ds, low)

    metric = nums[0] if nums else ds.primary_metric

    # ---------- ranking: top / bottom / highest / lowest <dimension> ----------
    m = re.search(r"\b(top|best|bottom|worst|lowest|highest|biggest|smallest)\b\s*(\d+)?", low)
    if m and cats and metric:
        word = m.group(1)
        n = int(m.group(2)) if m.group(2) else (5 if word in ("top", "bottom") else 3)
        ascending = word in ("bottom", "worst", "lowest", "smallest")
        want_margin = "margin" in low and ds.roles.get("profit")
        g = A.group_analysis(ds, cats[0], metric)
        if g is not None and len(g):
            if want_margin and "margin" in g:
                g = g.dropna(subset=["margin"]).sort_values("margin", ascending=ascending).head(n)
                label = "margin"
            else:
                g = g.sort_values("value", ascending=ascending).head(n)
                label = metric
            head = f"{'Bottom' if ascending else 'Top'} {len(g)} {cats[0]} by {label}:"
            vlabel = "Total" if ds.is_additive(metric) else "Average"
            return head + "\n" + _group_table(g, cats[0], vlabel)

    # ---------- aggregation ----------
    agg = None
    for word, fn in AGG_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", low):
            agg = fn
            break
    if agg is None and nums and (filters or (cats and re.search(r"\b(by|per|each|across)\b", low))):
        agg = "sum" if ds.is_additive(nums[0]) else "mean"   # "sales in West" means total sales
    if agg is None:
        return None

    sub = df
    applied = []
    for col, val in filters:
        sub = sub[sub[col].astype(str) == val]
        applied.append(f"{col} = {val}")
    scope = f" ({', '.join(applied)})" if applied else ""

    grouped = bool(re.search(r"\b(by|per|each|for each|across|split by|group by)\b", low)) and cats

    # counts
    if agg == "count":
        if cats and not grouped and not applied:
            return f"There are {sub[cats[0]].nunique():,} distinct {cats[0]} values."
        if grouped:
            vc = sub[cats[0]].value_counts().head(15)
            return f"Records per {cats[0]}:\n" + table([[_clip(i), f"{v:,}"] for i, v in vc.items()],
                                                       [cats[0], "Count"], "lr")
        return f"{len(sub):,} records{scope}."

    if not nums and not (ds.primary_metric and (cols or grouped)):
        return None
    col = nums[0] if nums else ds.primary_metric
    if col is None or not A._is_numeric(df[col]):
        return None
    if agg == "sum" and not ds.is_additive(col) and ds.kinds.get(col) == "numeric" and "total" not in low and "sum" not in low:
        agg = "mean"

    if grouped:
        g = sub.groupby(cats[0], observed=True)[col].agg(agg).sort_values(ascending=False).head(15)
        rows = [[_clip(i), human(v)] for i, v in g.items()]
        return f"{AGG_LABEL[agg]} of {col} by {cats[0]}{scope}:\n" + table(rows, [cats[0], AGG_LABEL[agg]], "lr")

    value = getattr(sub[col], agg)()
    extra = ""
    if agg in ("max", "min") and cats:
        idx = sub[col].idxmax() if agg == "max" else sub[col].idxmin()
        extra = f"  (at {cats[0]} = {df.loc[idx, cats[0]]})"
    elif agg in ("max", "min"):
        idx = sub[col].idxmax() if agg == "max" else sub[col].idxmin()
        ident = ds.roles.get("order") or ds.roles.get("product") or ds.roles.get("customer")
        if ident:
            extra = f"  ({ident}: {df.loc[idx, ident]})"
    return f"{AGG_LABEL[agg]} of {col}{scope}: {human(value)}{extra}"
