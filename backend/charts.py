"""
JARVIS chart engine.

Every function returns a dict:
    {"id", "title", "caption", "image": "data:image/png;base64,..."}
or None when the dataset has nothing sensible to draw.

Uses matplotlib's object-oriented Figure API (no pyplot), which is safe to call
from worker threads.
"""

from __future__ import annotations

import base64
import calendar
import io
import traceback

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter

import analytics as A
import config
import drivers as DR
from analytics import Dataset, human

T = config.THEME
PALETTE = T["palette"]


# ============================================================
# STYLE HELPERS
# ============================================================

def _figure(w=9.0, h=4.6, ncols=1, nrows=1, **kw):
    fig = Figure(figsize=(w, h), dpi=130, facecolor=T["bg"])
    axes = fig.subplots(nrows, ncols, **kw) if (ncols * nrows) > 1 else fig.subplots()
    return fig, axes


def _style(ax, title=None, xlabel=None, ylabel=None, grid_axis="y"):
    ax.set_facecolor(T["panel"])
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(T["grid"])
    ax.tick_params(colors=T["muted"], labelsize=8, length=3)
    if grid_axis:
        ax.grid(axis=grid_axis, color=T["grid"], linewidth=0.6, alpha=0.7)
        ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=T["gold"], fontsize=11, fontweight="bold", loc="left", pad=10)
    if xlabel:
        ax.set_xlabel(xlabel, color=T["muted"], fontsize=8)
    if ylabel:
        ax.set_ylabel(ylabel, color=T["muted"], fontsize=8)


def _human_axis(axis):
    axis.set_major_formatter(FuncFormatter(lambda v, _: human(v, 1)))


def _pack(fig, cid, title, caption=""):
    fig.tight_layout(pad=1.4)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return {"id": cid, "title": title, "caption": caption, "image": f"data:image/png;base64,{b64}"}


def _sample(df: pd.DataFrame) -> pd.DataFrame:
    if len(df) > config.MAX_ROWS_FOR_CHARTS:
        return df.sample(config.MAX_ROWS_FOR_CHARTS, random_state=1)
    return df


def _label(text, n=22):
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


# ============================================================
# INDIVIDUAL CHARTS
# ============================================================

def kpi_scorecard(ds: Dataset):
    items = A.kpis(ds)[:8]
    if not items:
        return None
    cols = 4 if len(items) > 4 else len(items)
    rows = (len(items) + cols - 1) // cols
    fig = Figure(figsize=(9, 1.7 * rows + 0.6), dpi=130, facecolor=T["bg"])
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_facecolor(T["bg"])
    ax.set_xlim(0, cols)
    ax.set_ylim(0, rows)
    ax.axis("off")

    for i, k in enumerate(items):
        r, c = divmod(i, cols)
        y0 = rows - r - 1
        ax.add_patch(matplotlib.patches.FancyBboxPatch(
            (c + 0.04, y0 + 0.08), 0.92, 0.84, boxstyle="round,pad=0,rounding_size=0.03",
            facecolor=T["panel"], edgecolor=T["ember"], linewidth=1.0, alpha=0.95))
        ax.text(c + 0.5, y0 + 0.58, k["display"], ha="center", va="center",
                color=T["gold"], fontsize=19, fontweight="bold")
        ax.text(c + 0.5, y0 + 0.25, k["label"].upper(), ha="center", va="center",
                color=T["muted"], fontsize=7.5)

    return _pack_raw(fig, "kpis", "Key metrics", f"{ds.name}")


def _pack_raw(fig, cid, title, caption=""):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    return {"id": cid, "title": title, "caption": caption,
            "image": "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")}


def trend(ds: Dataset, metric=None):
    t = A.time_analysis(ds, metric)
    if not t:
        return None
    s, freq, metric = t["series"], t["freq"], t["metric"]
    st = t["stats"]

    fig, ax = _figure(9.4, 4.8)
    _style(ax, f"{metric} over time", ylabel=metric)

    complete = s.iloc[:-1] if t["partial_last"] else s
    ax.fill_between(complete.index, complete.values, color=T["ember"], alpha=0.14)
    ax.plot(complete.index, complete.values, color=T["ember"], linewidth=2, label=metric)

    if t["partial_last"]:
        ax.plot(s.index[-2:], s.values[-2:], color=T["ember"], linewidth=1.5, linestyle=":")
        ax.scatter([s.index[-1]], [s.values[-1]], color=T["muted"], s=22, zorder=5, label="incomplete period")

    if len(complete) >= 6:
        ma = complete.rolling(3, min_periods=1).mean()
        ax.plot(ma.index, ma.values, color=T["gold"], linewidth=1.3, alpha=0.9, label="3-period average")

    # trend line
    x = np.arange(len(complete))
    ax.plot(complete.index, st["slope"] * x + (complete.values.mean() - st["slope"] * x.mean()),
            color=T["muted"], linewidth=1, linestyle="--", alpha=0.8, label="trend")

    fc = t.get("forecast")
    if fc:
        f = fc["forecast"]
        ax.plot(f.index, f["yhat"], color=T["green"], linewidth=2, linestyle="--", label="forecast")
        ax.fill_between(f.index, f["lo"], f["hi"], color=T["green"], alpha=0.12)

    for a in st["anomalies"]:
        ax.scatter([a["period"]], [a["value"]], color=T["red"], s=45, zorder=6, edgecolor="white", linewidth=0.6)

    _human_axis(ax.yaxis)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    leg = ax.legend(loc="upper left", fontsize=7.5, frameon=False)
    for txt in leg.get_texts():
        txt.set_color(T["muted"])

    cap = f"Trend: {st['direction']}"
    if st["yoy_pct"] is not None:
        cap += f", YoY {st['yoy_pct']:+.1f}%"
    if fc and fc["mape"] is not None:
        cap += f" · forecast backtest error ≈ {fc['mape']:.0f}%"
    return _pack(fig, "trend", f"{metric} trend & forecast", cap)


def growth(ds: Dataset):
    t = A.time_analysis(ds)
    if not t:
        return None
    s = t["series"]
    s = s.iloc[:-1] if t["partial_last"] else s
    ch = s.pct_change().replace([np.inf, -np.inf], np.nan).dropna() * 100
    ch = ch.tail(24)
    if len(ch) < 3:
        return None

    fig, ax = _figure(9.4, 4.0)
    _style(ax, "Period-over-period growth (%)")
    colors = [T["green"] if v >= 0 else T["red"] for v in ch.values]
    width = 20 if t["freq"] == "MS" else (5 if t["freq"] == "W" else 0.8)
    ax.bar(ch.index, ch.values, color=colors, width=width, alpha=0.9)
    ax.axhline(0, color=T["muted"], linewidth=0.8)
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(ax.xaxis.get_major_locator()))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    return _pack(fig, "growth", "Growth rate", f"{(ch > 0).sum()} of {len(ch)} periods grew")


def top_bar(ds: Dataset, dim=None, metric=None, n=10):
    metric = metric or ds.primary_metric
    dim = dim or (ds.dims[0] if ds.dims else None)
    g = A.group_analysis(ds, dim, metric) if dim and metric else None
    if g is None or len(g) < 2:
        return None

    additive = ds.is_additive(metric)
    title = f"Top {dim} by {metric}" if additive else f"Average {metric} by {dim}"
    top = g.head(n).iloc[::-1]
    fig, ax = _figure(9.0, max(3.2, 0.42 * len(top) + 1.4))
    _style(ax, title, grid_axis="x")
    colors = [PALETTE[0] if i == len(top) - 1 else PALETTE[3] for i in range(len(top))]
    bars = ax.barh([_label(i) for i in top.index], top["value"], color=colors, height=0.66)
    _human_axis(ax.xaxis)

    for bar, (_, row) in zip(bars, top.iterrows()):
        label = human(row["value"])
        if "share" in row and pd.notna(row["share"]):
            label += f"  ({row['share']:.1f}%)"
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, "  " + label,
                va="center", color=T["text"], fontsize=8)
    ax.set_xlim(0, top["value"].max() * 1.22)
    cap = f"{len(g)} {dim} values in total"
    return _pack(fig, f"top_{dim}", title, cap)


def pareto(ds: Dataset, dim=None, metric=None, n=15):
    metric = metric or ds.primary_metric
    if dim is None:
        for cand in ds.dims:
            g = A.group_analysis(ds, cand, metric)
            if g is not None and len(g) >= 8 and "cum_share" in g:
                dim = cand
                break
    if dim is None:
        return None
    g = A.group_analysis(ds, dim, metric)
    if g is None or "cum_share" not in g or len(g) < 4:
        return None
    top = g.head(n)

    fig, ax = _figure(9.4, 4.6)
    _style(ax, f"Pareto: {dim} contribution to {metric}")
    ax.bar(range(len(top)), top["value"], color=PALETTE[0], alpha=0.9)
    ax.set_xticks(range(len(top)))
    ax.set_xticklabels([_label(i, 12) for i in top.index], rotation=45, ha="right", fontsize=7)
    _human_axis(ax.yaxis)

    ax2 = ax.twinx()
    ax2.plot(range(len(top)), top["cum_share"], color=T["gold"], marker="o", markersize=3.5, linewidth=1.8)
    ax2.axhline(80, color=T["muted"], linestyle="--", linewidth=0.9)
    ax2.set_ylim(0, 105)
    ax2.tick_params(colors=T["muted"], labelsize=8)
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    for side in ("top", "left", "bottom"):
        ax2.spines[side].set_visible(False)
    ax2.spines["right"].set_color(T["grid"])
    ax2.set_facecolor("none")

    c = A.concentration(g)
    cap = f"Top {c['top20_count']} of {c['items']} drive {c['top20_share']:.0f}%" if c else ""
    return _pack(fig, f"pareto_{dim}", "Pareto analysis", cap)


def donut(ds: Dataset, dim=None, metric=None):
    metric = metric or ds.primary_metric
    dim = dim or (ds.dims[0] if ds.dims else None)
    g = A.group_analysis(ds, dim, metric) if dim and metric else None
    if g is None or len(g) < 2 or (g["value"] <= 0).all():
        return None
    g = g[g["value"] > 0]
    top = g.head(6)
    rest = g["value"].iloc[6:].sum()
    values = list(top["value"]) + ([rest] if rest > 0 else [])
    labels = [_label(i, 18) for i in top.index] + (["Other"] if rest > 0 else [])

    fig, ax = _figure(7.4, 4.6)
    ax.set_facecolor(T["bg"])
    wedges, _ = ax.pie(values, colors=PALETTE[: len(values)], startangle=90, counterclock=False,
                       wedgeprops=dict(width=0.38, edgecolor=T["bg"], linewidth=2))
    total = sum(values)
    ax.text(0, 0.08, human(total), ha="center", va="center", color=T["gold"], fontsize=17, fontweight="bold")
    ax.text(0, -0.14, metric, ha="center", va="center", color=T["muted"], fontsize=8)
    ax.legend(wedges, [f"{l}  {v / total * 100:.1f}%" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8,
              labelcolor=T["text"])
    ax.set_title(f"{metric} share by {dim}", color=T["gold"], fontsize=11, fontweight="bold", loc="left")
    return _pack(fig, f"donut_{dim}", f"{metric} share by {dim}")


def margin_by_group(ds: Dataset, dim=None):
    metric = ds.primary_metric
    cands = [dim] if dim else ds.dims
    for d in cands:
        g = A.group_analysis(ds, d, metric)
        if g is not None and "margin" in g and g["margin"].notna().any() and 2 <= len(g) <= 25:
            g = g.dropna(subset=["margin"]).sort_values("margin")
            overall = A.kpis(ds)
            overall_m = next((k["raw"] for k in overall if k["label"] == "Profit margin"), None)
            fig, ax = _figure(9.0, max(3.2, 0.42 * len(g) + 1.4))
            _style(ax, f"Profit margin by {d}", grid_axis="x")
            colors = [T["red"] if v < 0 else (PALETTE[1] if (overall_m is None or v >= overall_m) else PALETTE[0])
                      for v in g["margin"]]
            bars = ax.barh([_label(i) for i in g.index], g["margin"], color=colors, height=0.66)
            for bar, v in zip(bars, g["margin"]):
                ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2,
                        f"  {v:.1f}%" if v >= 0 else f"{v:.1f}%  ",
                        va="center", ha="left" if v >= 0 else "right", color=T["text"], fontsize=8)
            if overall_m is not None:
                ax.axvline(overall_m, color=T["muted"], linestyle="--", linewidth=1)
                ax.text(overall_m, len(g) - 0.4, f" overall {overall_m:.1f}%", color=T["muted"], fontsize=7.5)
            ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
            return _pack(fig, f"margin_{d}", f"Profit margin by {d}")
    return None


def correlation_heatmap(ds: Dataset):
    c = A.correlations(ds)
    m = c["matrix"]
    if m is None or m.shape[0] < 2:
        return None
    n = m.shape[0]
    fig, ax = _figure(max(5.5, 0.62 * n + 3), max(4.5, 0.56 * n + 2.5))
    ax.set_facecolor(T["bg"])
    from matplotlib.colors import LinearSegmentedColormap
    cmap = LinearSegmentedColormap.from_list("jarvis", [T["red"], T["panel"], T["gold"]])
    im = ax.imshow(m.values, cmap=cmap, vmin=-1, vmax=1)
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels([_label(c, 14) for c in m.columns], rotation=45, ha="right", color=T["muted"], fontsize=8)
    ax.set_yticklabels([_label(c, 14) for c in m.index], color=T["muted"], fontsize=8)
    for i in range(n):
        for j in range(n):
            v = m.values[i, j]
            if pd.notna(v):
                ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=7.5,
                        color="#000000" if abs(v) > 0.55 else T["text"])
    for s in ax.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.03)
    cb.ax.tick_params(colors=T["muted"], labelsize=7)
    cb.outline.set_visible(False)
    ax.set_title("Correlation matrix", color=T["gold"], fontsize=11, fontweight="bold", loc="left", pad=10)
    top = c["pairs"][0] if c["pairs"] else None
    cap = f"Strongest: {top['a']} ↔ {top['b']} ({top['r']:.2f})" if top else ""
    return _pack(fig, "corr", "Correlation matrix", cap)


def distributions(ds: Dataset, cols=None):
    cols = cols or ds.num_cols[:6]
    cols = [c for c in cols if ds.df[c].dropna().nunique() > 1][:6]
    if not cols:
        return None
    n = len(cols)
    ncols = 3 if n > 4 else (2 if n > 1 else 1)
    nrows = (n + ncols - 1) // ncols
    fig, axes = _figure(9.6, 2.7 * nrows + 0.4, ncols=ncols, nrows=nrows)
    axes = np.atleast_1d(axes).ravel()
    df = _sample(ds.df)

    for ax, c in zip(axes, cols):
        s = df[c].dropna()
        _style(ax, _label(c, 26), grid_axis="y")
        ax.hist(s, bins=min(40, max(10, int(np.sqrt(len(s))))), color=T["ember"], alpha=0.9,
                edgecolor=T["bg"], linewidth=0.4)
        ax.axvline(s.median(), color=T["gold"], linewidth=1.2, linestyle="--")
        _human_axis(ax.xaxis)
        ax.title.set_fontsize(9)
    for ax in axes[n:]:
        ax.axis("off")
    return _pack(fig, "dist", "Distributions", "Dashed line = median")


def histogram(ds: Dataset, col: str):
    if col not in ds.df.columns or not A._is_numeric(ds.df[col]):
        return None
    s = _sample(ds.df)[col].dropna()
    if s.empty:
        return None
    fig, ax = _figure(8.6, 4.2)
    _style(ax, f"Distribution of {col}", ylabel="count")
    ax.hist(s, bins=min(50, max(10, int(np.sqrt(len(s))))), color=T["ember"], alpha=0.92,
            edgecolor=T["bg"], linewidth=0.4)
    ax.axvline(s.mean(), color=T["gold"], linestyle="--", linewidth=1.3, label=f"mean {human(s.mean())}")
    ax.axvline(s.median(), color=T["green"], linestyle="--", linewidth=1.3, label=f"median {human(s.median())}")
    _human_axis(ax.xaxis)
    leg = ax.legend(frameon=False, fontsize=8)
    for txt in leg.get_texts():
        txt.set_color(T["text"])
    return _pack(fig, f"hist_{col}", f"Distribution of {col}", f"skew {s.skew():.2f}")


def missing(ds: Dataset):
    p = A.profile(ds)
    miss = p["missing_by_col"]
    if not miss:
        return None
    items = sorted(miss.items(), key=lambda kv: kv[1]["pct"])[-15:]
    fig, ax = _figure(8.6, max(3.0, 0.4 * len(items) + 1.3))
    _style(ax, "Missing values by column", grid_axis="x")
    vals = [v["pct"] for _, v in items]
    colors = [T["red"] if v > 30 else (T["gold"] if v > 5 else T["ember"]) for v in vals]
    bars = ax.barh([_label(k) for k, _ in items], vals, color=colors, height=0.62)
    for bar, v in zip(bars, vals):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f"  {v:.1f}%",
                va="center", color=T["text"], fontsize=8)
    ax.set_xlim(0, max(vals) * 1.2 + 1)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    return _pack(fig, "missing", "Missing values", f"{p['missing_total']:,} empty cells in total")


def outlier_box(ds: Dataset):
    cols = list(A.outliers(ds).keys())[:8] or ds.num_cols[:8]
    cols = [c for c in cols if ds.df[c].dropna().nunique() > 2]
    if not cols:
        return None
    df = _sample(ds.df)
    data = []
    for c in cols:
        s = df[c].dropna()
        sd = s.std()
        data.append(((s - s.mean()) / sd).values if sd else s.values * 0)
    fig, ax = _figure(9.0, max(3.2, 0.5 * len(cols) + 1.5))
    _style(ax, "Outliers (standardised values)", grid_axis="x")
    bp = ax.boxplot(data, vert=False, patch_artist=True, widths=0.55,
                    flierprops=dict(marker="o", markerfacecolor=T["red"], markeredgecolor="none", markersize=3, alpha=0.6),
                    medianprops=dict(color=T["gold"], linewidth=1.6),
                    whiskerprops=dict(color=T["muted"]), capprops=dict(color=T["muted"]),
                    boxprops=dict(facecolor=T["panel"], edgecolor=T["ember"]))
    ax.set_yticklabels([_label(c, 18) for c in cols], color=T["muted"], fontsize=8)
    return _pack(fig, "box", "Outlier check", "Red dots = statistical outliers (IQR rule)")


def scatter(ds: Dataset, x: str, y: str):
    df = ds.df
    if x not in df.columns or y not in df.columns or not A._is_numeric(df[x]) or not A._is_numeric(df[y]):
        return None
    d = _sample(df[[x, y]].dropna())
    if len(d) < 5:
        return None
    if len(d) > 5000:
        d = d.sample(5000, random_state=1)

    fig, ax = _figure(8.2, 5.0)
    _style(ax, f"{y} vs {x}", xlabel=x, ylabel=y, grid_axis="both")
    ax.scatter(d[x], d[y], s=10, color=T["ember"], alpha=0.45, edgecolors="none")
    slope, intercept = np.polyfit(d[x], d[y], 1)
    xs = np.linspace(d[x].min(), d[x].max(), 50)
    ax.plot(xs, slope * xs + intercept, color=T["gold"], linewidth=2)
    r = d[x].corr(d[y])
    ax.text(0.02, 0.96, f"r = {r:.2f}", transform=ax.transAxes, color=T["gold"], fontsize=10,
            fontweight="bold", va="top")
    _human_axis(ax.xaxis)
    _human_axis(ax.yaxis)
    return _pack(fig, f"scatter_{x}_{y}", f"{y} vs {x}", f"Correlation {r:.2f}")


def discount_chart(ds: Dataset):
    d = A.discount_impact(ds)
    if not d:
        return None
    t = d["table"]
    fig, (ax1, ax2) = _figure(9.4, 4.2, ncols=2)
    _style(ax1, "Profit per order by discount")
    ax1.bar(t.index.astype(str), t["avg_profit"],
            color=[T["red"] if v < 0 else PALETTE[0] for v in t["avg_profit"]], width=0.62)
    _human_axis(ax1.yaxis)
    if "margin" in t:
        _style(ax2, "Margin % by discount")
        ax2.bar(t.index.astype(str), t["margin"],
                color=[T["red"] if v < 0 else PALETTE[1] for v in t["margin"]], width=0.62)
        ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    else:
        _style(ax2, "Orders by discount")
        ax2.bar(t.index.astype(str), t["rows"], color=PALETTE[1], width=0.62)
    return _pack(fig, "discount", "Discount impact", f"Discount vs profit correlation {d['corr']:.2f}")


def rfm_chart(ds: Dataset):
    r = A.rfm(ds)
    if not r:
        return None
    s = r["summary"].sort_values("revenue_share")
    fig, ax = _figure(9.0, max(3.2, 0.55 * len(s) + 1.5))
    _style(ax, "Customer segments: customers vs revenue", grid_axis="x")
    y = np.arange(len(s))
    ax.barh(y + 0.19, s["customer_share"], height=0.36, color=T["muted"], label="% of customers")
    ax.barh(y - 0.19, s["revenue_share"], height=0.36, color=T["ember"], label="% of revenue")
    ax.set_yticks(y)
    ax.set_yticklabels(s.index, color=T["muted"], fontsize=8.5)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    leg = ax.legend(frameon=False, fontsize=8, loc="lower right")
    for txt in leg.get_texts():
        txt.set_color(T["text"])
    return _pack(fig, "rfm", "Customer segments (RFM)", f"{len(r['customers']):,} customers scored on recency, frequency, value")


def seasonality_chart(ds: Dataset):
    t = A.time_analysis(ds)
    if not t or not t["stats"]["seasonality"]:
        return None
    idx = t["stats"]["seasonality"]["index"]
    months = sorted(idx)
    vals = [idx[m] for m in months]
    fig, ax = _figure(8.6, 4.0)
    _style(ax, "Seasonality index (1.0 = average month)")
    ax.bar([calendar.month_abbr[m] for m in months], vals,
           color=[T["gold"] if v >= 1 else PALETTE[0] for v in vals], width=0.66)
    ax.axhline(1, color=T["muted"], linestyle="--", linewidth=1)
    return _pack(fig, "season", "Seasonality", "Gold months beat the average")


def cat_overview(ds: Dataset):
    """Share of records in each group, for the category / coded columns."""
    dims = [c for c in ds.dims if 2 <= ds.df[c].nunique() <= 8][:6]
    if not dims:
        return None
    n = len(dims)
    ncols = 3 if n > 4 else (2 if n > 1 else 1)
    nrows = (n + ncols - 1) // ncols
    fig, axes = _figure(9.6, 2.5 * nrows + 0.5, ncols=ncols, nrows=nrows)
    axes = np.atleast_1d(axes).ravel()
    for ax, c in zip(axes, dims):
        vc = ds.df[c].value_counts(normalize=True).sort_index() * 100
        _style(ax, _label(c, 24), grid_axis="y")
        ax.bar([str(i) for i in vc.index], vc.values, color=PALETTE[0], width=0.66)
        ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
        ax.title.set_fontsize(9)
    for ax in axes[n:]:
        ax.axis("off")
    return _pack(fig, "cats", "Group mix", "Share of records in each group")


def segment_means(ds: Dataset):
    """Average of the main numeric columns for the first grouping column."""
    prof = A.segment_profile(ds)
    if not prof:
        return None
    sp = prof[0]
    metrics = list(sp["means"].columns)[:3]
    n = len(metrics)
    fig, axes = _figure(9.6, 3.2, ncols=n, nrows=1)
    axes = np.atleast_1d(axes).ravel()
    for ax, m in zip(axes, metrics):
        _style(ax, f"Average {_label(m, 18)}", grid_axis="y")
        vals = sp["means"][m]
        ax.bar([str(i) for i in vals.index], vals.values, color=PALETTE[1], width=0.66)
        ax.axhline(ds.df[m].mean(), color=T["muted"], linestyle="--", linewidth=1)
        _human_axis(ax.yaxis)
        ax.set_xlabel(sp["dim"], color=T["muted"], fontsize=8)
        ax.title.set_fontsize(9)
    return _pack(fig, "segmeans", f"Averages by {sp['dim']}", "Dashed line = overall average")


def drivers_chart(ds: Dataset, target: str):
    res = DR.drivers(ds, target)
    if not res or res.get("too_small") or not res["rows"]:
        return None
    rows = res["rows"][:8][::-1]
    fig, ax = _figure(9.0, max(3.2, 0.46 * len(rows) + 1.5))
    _style(ax, f"What is associated with {target}", grid_axis="x")
    colors = [T["gold"] if r["strength"] >= 0.4 else PALETTE[0] if r["strength"] >= 0.2 else T["muted"] for r in rows]
    bars = ax.barh([_label(r["column"]) for r in rows], [r["strength"] for r in rows], color=colors, height=0.62)
    for bar, r in zip(bars, rows):
        ax.text(bar.get_width(), bar.get_y() + bar.get_height() / 2, f"  {r['strength']:.2f}",
                va="center", color=T["text"], fontsize=8)
    ax.set_xlim(0, max(0.5, max(r["strength"] for r in rows) * 1.2))
    ax.set_xlabel("strength of association (0 = none, 1 = complete). Not a measure of cause.",
                  color=T["muted"], fontsize=7.5)
    return _pack(fig, f"drivers_{target}", f"Drivers of {target}", "Gold = strong, orange = moderate, grey = weak")


def target_rate_chart(ds: Dataset, target: str, dim: str = None):
    res = DR.drivers(ds, target)
    if not res or res.get("kind") != "binary":
        return None
    if dim is None:
        dim = next((r["column"] for r in res["rows"] if DR._is_category(ds, r["column"])), None)
    tab = DR.rate_by_group(ds, target, dim) if dim else None
    if tab is None:
        return None
    tab = tab.head(12)
    fig, ax = _figure(8.8, 4.2)
    _style(ax, f"{target} = {res['positive']} rate by {dim}", ylabel="% of records")
    bars = ax.bar([_label(str(i), 12) for i in tab.index], tab["rate"],
                  color=[T["red"] if v > res["overall_rate"] else PALETTE[1] for v in tab["rate"]], width=0.62)
    ax.axhline(res["overall_rate"], color=T["muted"], linestyle="--", linewidth=1.1)
    ax.text(len(tab) - 0.5, res["overall_rate"], f" overall {res['overall_rate']:.1f}%", color=T["muted"],
            fontsize=8, va="bottom", ha="right")
    for bar, (_, r) in zip(bars, tab.iterrows()):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f"{r['rate']:.0f}%\nn={int(r['n']):,}",
                ha="center", va="bottom", color=T["text"], fontsize=7.5)
    ax.set_ylim(0, max(tab["rate"].max() * 1.3, res["overall_rate"] * 1.3, 5))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.0f}%"))
    return _pack(fig, f"rate_{target}_{dim}", f"{target} rate by {dim}", "Red = above the overall rate")


def custom_chart(kind: str, data: pd.DataFrame, title: str = "Result"):
    """Chart for a DataFrame produced by the data analyst (first column = labels/x, the rest numeric)."""
    if data is None or len(data) == 0:
        return None
    data = data.reset_index() if not isinstance(data.index, pd.RangeIndex) else data.copy()
    if data.shape[1] < 2:
        return None
    x = data.iloc[:, 0]
    ys = [c for c in data.columns[1:] if pd.api.types.is_numeric_dtype(data[c])][:3]
    if not ys:
        return None
    data = data.head(40)
    x = data.iloc[:, 0]
    fig, ax = _figure(9.0, 4.4)
    _style(ax, title, grid_axis="y")
    labels = [_label(str(v), 14) for v in x]
    if kind == "line":
        for i, c in enumerate(ys):
            ax.plot(range(len(data)), data[c], color=PALETTE[i], linewidth=2, label=str(c))
    elif kind == "pie" and len(ys) >= 1:
        ax.set_facecolor(T["bg"])
        ax.pie(data[ys[0]].clip(lower=0), labels=labels, colors=PALETTE, textprops={"color": T["text"], "fontsize": 8})
    elif kind == "scatter" and len(ys) >= 1:
        ax.scatter(data.iloc[:, 0], data[ys[0]], color=PALETTE[0], alpha=0.6, s=14)
    else:
        w = 0.8 / len(ys)
        for i, c in enumerate(ys):
            ax.bar([j + i * w for j in range(len(data))], data[c], width=w, color=PALETTE[i], label=str(c))
    if kind not in ("pie", "scatter"):
        ax.set_xticks([j + 0.4 - 0.4 / len(ys) for j in range(len(data))] if kind != "line" else range(len(data)))
        ax.set_xticklabels(labels, rotation=35 if len(data) > 6 else 0, ha="right" if len(data) > 6 else "center", fontsize=7.5)
        _human_axis(ax.yaxis)
        if len(ys) > 1:
            leg = ax.legend(frameon=False, fontsize=8)
            for t in leg.get_texts():
                t.set_color(T["muted"])
    return _pack(fig, f"custom_{abs(hash(title)) % 10000}", title)


def bar_by(ds: Dataset, dim: str, metric=None, agg="sum"):
    metric = metric or ds.primary_metric
    if dim not in ds.df.columns or not metric:
        return None
    g = ds.df.groupby(dim, observed=True)[metric].agg(agg).sort_values(ascending=False).head(12)
    if len(g) < 1:
        return None
    fig, ax = _figure(9.0, 4.4)
    _style(ax, f"{agg.title()} of {metric} by {dim}")
    ax.bar([_label(i, 12) for i in g.index], g.values, color=PALETTE[0], width=0.66)
    plt_rot = 30 if len(g) > 5 else 0
    ax.tick_params(axis="x", rotation=plt_rot)
    for lbl in ax.get_xticklabels():
        lbl.set_ha("right" if plt_rot else "center")
    _human_axis(ax.yaxis)
    return _pack(fig, f"by_{dim}", f"{metric} by {dim}")


# ============================================================
# ORCHESTRATION
# ============================================================

def _safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        print(f"[charts] {fn.__name__} failed:")
        traceback.print_exc()
        return None


def auto_charts(ds: Dataset, limit: int = 8) -> list:
    """A sensible default dashboard for any dataset."""
    if ds.primary_metric:
        plan = [
            (kpi_scorecard, ()), (trend, ()), (top_bar, ()), (margin_by_group, ()), (pareto, ()),
            (donut, ()), (discount_chart, ()), (rfm_chart, ()), (seasonality_chart, ()),
            (correlation_heatmap, ()), (distributions, ()), (missing, ()),
        ]
    else:  # profile mode: describe the columns, no business assumptions
        plan = [
            (kpi_scorecard, ()), (distributions, ()), (cat_overview, ()), (segment_means, ()),
            (correlation_heatmap, ()), (outlier_box, ()), (missing, ()),
        ]
    out = []
    for fn, args in plan:
        chart = _safe(fn, ds, *args)
        if chart:
            out.append(chart)
        if len(out) >= limit:
            break
    return out


def charts_for_request(ds: Dataset, text: str, limit: int = 4) -> list:
    """Pick charts from a natural-language request."""
    t = text.lower()
    cols = ds.columns_in_text(text)
    nums = [c for c in cols if ds.is_metric(c)]
    cats = [c for c in cols if c not in nums and ds.kinds.get(c) in ("categorical", "discrete", "id", "text")]
    metric = nums[0] if nums else None
    dim = cats[0] if cats else None
    out = []

    def add(chart):
        if chart:
            out.append(chart)

    if any(w in t for w in ("dashboard", "overview chart", "all charts", "everything", "visualise everything", "visualize everything")):
        return auto_charts(ds, limit=8)

    if any(w in t for w in ("scatter", " vs ", "versus", "relationship between")) and len(nums) >= 2:
        add(_safe(scatter, ds, nums[0], nums[1]))
        return out

    if any(w in t for w in ("histogram", "distribution", "spread of")):
        if metric:
            add(_safe(histogram, ds, metric))
        else:
            add(_safe(distributions, ds))
        return out

    if any(w in t for w in ("correlat", "heatmap", "heat map")):
        add(_safe(correlation_heatmap, ds))
        pairs = A.correlations(ds)["pairs"]
        if pairs:
            add(_safe(scatter, ds, pairs[0]["a"], pairs[0]["b"]))
        return out

    if any(w in t for w in ("missing", "null", "empty value")):
        add(_safe(missing, ds))
        return out

    if any(w in t for w in ("outlier", "boxplot", "box plot", "anomal")):
        add(_safe(outlier_box, ds))
        add(_safe(trend, ds)) if "anomal" in t else None
        return out

    if any(w in t for w in ("pareto", "80/20", "80-20")):
        add(_safe(pareto, ds, dim, metric))
        return out

    if any(w in t for w in ("forecast", "predict", "projection", "trend", "over time", "timeline", "monthly", "weekly", "daily")):
        add(_safe(trend, ds, metric))
        add(_safe(growth, ds))
        if "season" in t:
            add(_safe(seasonality_chart, ds))
        return out

    if "season" in t:
        add(_safe(seasonality_chart, ds))
        return out

    if any(w in t for w in ("share", "pie", "donut", "breakdown", "split", "mix", "proportion")):
        if ds.primary_metric or metric:
            add(_safe(donut, ds, dim, metric))
        if not out:
            add(_safe(cat_overview, ds))
        return out

    if "margin" in t:
        add(_safe(margin_by_group, ds, dim))
        return out

    if "discount" in t:
        add(_safe(discount_chart, ds))
        return out

    if any(w in t for w in ("customer", "rfm", "segment", "retention", "churn")):
        add(_safe(rfm_chart, ds))
        return out

    if any(w in t for w in ("top", "best", "worst", "rank", "leaderboard", "bar", " by ")):
        if dim:
            add(_safe(top_bar, ds, dim, metric))
            add(_safe(margin_by_group, ds, dim))
        else:
            add(_safe(top_bar, ds, None, metric))
        return out

    return auto_charts(ds, limit=limit + 2)
