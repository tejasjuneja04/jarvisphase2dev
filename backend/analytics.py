"""
JARVIS analytics engine.

Pure pandas/numpy (no sklearn/scipy needed). Everything here returns plain
Python/pandas objects; turning them into text lives in reports.py and turning
them into pictures lives in charts.py.
"""

from __future__ import annotations

import csv
import io
import json
import math
import re
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

import config


# ============================================================
# SMALL HELPERS
# ============================================================

def human(x, decimals: int = 1) -> str:
    """1234567 -> 1.2M, 950 -> 950, 12.3456 -> 12.35"""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(x) or math.isinf(x):
        return "n/a"
    ax = abs(x)
    for div, suffix in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if ax >= div:
            return f"{x / div:.{decimals}f}{suffix}"
    if ax >= 100 or x.is_integer():
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def pct(x, decimals: int = 1, signed: bool = False) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if math.isnan(x) or math.isinf(x):
        return "n/a"
    return f"{x:+.{decimals}f}%" if signed else f"{x:.{decimals}f}%"


def _split_camel(name) -> str:
    """OrderDate -> Order Date, customerID -> customer ID, SalesAmount2024 -> Sales Amount2024"""
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", str(name))          # lower -> Upper
    return re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", t)          # HTMLPage -> HTML Page


def _norm(name) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _split_camel(name).lower()).strip("_")


def _spaced(name) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _split_camel(name).lower()).strip()


def _singular(text: str) -> str:
    """Very small plural stripper so 'customers' matches the 'customer' column."""
    out = []
    for tok in str(text).split():
        if len(tok) > 4 and tok.endswith("ies"):
            tok = tok[:-3] + "y"
        elif len(tok) > 4 and tok.endswith(("ses", "xes", "ches", "shes")):
            tok = tok[:-2]
        elif len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
            tok = tok[:-1]
        out.append(tok)
    return " ".join(out)


_TEMPORAL_NAMES = {"month", "mth", "quarter", "week", "weekday", "day", "dow", "hour", "day_of_week",
                   "month_num", "month_number", "week_number", "day_of_month"}
_YEAR_HINTS = ("year", "yr_", "_yr", "built", "founded", "born")
_MONTH_NAMES = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

_ID_HINT = re.compile(r"(^|_)(id|uuid|guid|code|zip|postal|pin|phone|mobile|sku|index|no|number)($|_)")
_DATE_HINT = re.compile(r"(date|time|day|month|created|updated|_at$|timestamp|period|dt$)")
_NA_STRINGS = {"", "na", "n/a", "nan", "null", "none", "-", "--", "?", "nil", "undefined"}


def _is_numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _is_datetime(s: pd.Series) -> bool:
    return pd.api.types.is_datetime64_any_dtype(s)


def _is_texty(s: pd.Series) -> bool:
    return not (_is_numeric(s) or _is_datetime(s) or pd.api.types.is_bool_dtype(s))


# ============================================================
# LOADING
# ============================================================

def load_dataframe(source, name: str) -> pd.DataFrame:
    """Load CSV / TSV / Excel / JSON / Parquet from a path or raw bytes."""
    ext = Path(name).suffix.lower()

    if isinstance(source, (str, Path)):
        data = Path(source).read_bytes()
    else:
        data = bytes(source)

    if not data:
        raise ValueError("The file is empty.")

    if ext in (".csv", ".tsv", ".txt"):
        sample = data[:8192].decode("utf-8", errors="ignore")
        if ext == ".tsv":
            sep = "\t"
        else:
            try:
                sep = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
            except Exception:
                sep = ","
        last_error = None
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                return pd.read_csv(io.BytesIO(data), sep=sep, encoding=enc, low_memory=False)
            except UnicodeDecodeError as e:
                last_error = e
            except Exception as e:  # malformed lines etc.
                try:
                    return pd.read_csv(
                        io.BytesIO(data), sep=sep, encoding=enc,
                        engine="python", on_bad_lines="skip",
                    )
                except Exception as e2:
                    last_error = e2
        raise ValueError(f"Could not read the CSV: {last_error}")

    if ext in (".xlsx", ".xls"):
        try:
            return pd.read_excel(io.BytesIO(data), sheet_name=0)
        except Exception as e:
            raise ValueError(f"Could not read the Excel file: {e}")

    if ext == ".json":
        text = data.decode("utf-8-sig", errors="ignore")
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            try:
                return pd.read_json(io.StringIO(text), lines=True)
            except Exception as e:
                raise ValueError(f"Invalid JSON: {e}")
        if isinstance(obj, list):
            return pd.json_normalize(obj)
        if isinstance(obj, dict):
            for key in ("data", "records", "results", "items", "rows"):
                if key in obj and isinstance(obj[key], list):
                    return pd.json_normalize(obj[key])
            return pd.json_normalize(obj)
        raise ValueError("Unsupported JSON structure.")

    if ext == ".parquet":
        try:
            return pd.read_parquet(io.BytesIO(data))
        except Exception as e:
            raise ValueError(f"Could not read Parquet (is pyarrow installed?): {e}")

    raise ValueError(
        f"Unsupported file type '{ext}'. Supported: CSV, TSV, Excel, JSON, Parquet."
    )


# ============================================================
# TYPE INFERENCE
# ============================================================

_CURRENCY = re.compile(r"(rs\.?|inr|usd|eur|gbp|[$€£₹%,\s])", flags=re.I)
_SYMBOLS = re.compile(r"(rs\.?|inr|usd|eur|gbp|[$€£₹%\s])", flags=re.I)   # keeps , and . intact


def _coerce_numeric(s: pd.Series):
    """Try to turn '₹1,200', '(45)', '12%' into numbers. Returns Series or None."""
    st = s.astype("string").str.strip()
    is_na = st.str.lower().isin(_NA_STRINGS) | st.isna()
    valid = int((~is_na).sum())
    if valid == 0:
        return None
    st = st.str.replace(r"^\((.*)\)$", r"-\1", regex=True)

    # European decimals: "1.234,56" or "10,5" -> 1234.56 / 10.5
    probe = st[~is_na].str.replace(_SYMBOLS, "", regex=True)
    euro = probe.str.match(r"^-?(\d{1,3}(\.\d{3})+|\d+),\d{1,2}$").mean()
    if euro >= 0.6:
        st = st.str.replace(_SYMBOLS, "", regex=True).str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    else:
        st = st.str.replace(_CURRENCY, "", regex=True)
    out = pd.to_numeric(st, errors="coerce")
    if out.notna().sum() / valid >= 0.9:
        return out.astype(float)
    return None


_COUNT_HINT = re.compile(r"(unit|qty|quantity|count|number_of|num_|items|orders|age|tenure|stock|visits)")


def _coerce_datetime(s: pd.Series, name: str):
    """Try to parse text as dates. Returns Series or None."""
    sample = s.dropna().astype(str).head(30)
    if sample.empty:
        return None

    looks_like_date = sample.str.match(
        r"^\s*(\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{1,2}[\s-][A-Za-z]{3,9}[\s,-]+\d{2,4}|[A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})"
    ).mean()

    if looks_like_date < 0.8 and not (_DATE_HINT.search(_norm(name)) and looks_like_date > 0.4):
        return None

    valid = int(s.notna().sum())
    if valid == 0:
        return None

    best, best_ratio = None, 0.0
    for dayfirst in (False, True):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                parsed = pd.to_datetime(s, errors="coerce", dayfirst=dayfirst)
            except Exception:
                continue
        ratio = parsed.notna().sum() / valid
        if ratio > best_ratio:
            best, best_ratio = parsed, ratio

    if best is not None and best_ratio >= 0.85:
        missed = best.isna() & s.notna()
        if missed.any():
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    rescued = pd.to_datetime(s[missed], errors="coerce", format="mixed")
                    best = best.copy()
                    best[missed] = rescued
                except Exception:
                    pass
        try:
            if getattr(best.dt, "tz", None) is not None:
                best = best.dt.tz_localize(None)
        except Exception:
            pass
        return best
    return None


# ============================================================
# DATASET
# ============================================================

# Business roles are assigned ONLY on strong evidence. Each role has:
#   strong: names that are assigned automatically
#   weak:   names assigned only when the dataset also looks transactional (has a date, order or customer column)
# Ambiguous words such as "income", "rate", "type", "area" or "class" are deliberately absent:
# they usually mean something else (a person's income, an interest rate, ...).
# Anything that is not assigned keeps its own column name everywhere in the output.
ROLE_ALIASES = {
    "date": {"strong": ["order_date", "invoice_date", "transaction_date", "sale_date", "purchase_date",
                        "date", "created_at", "timestamp", "datetime"],
             "weak": ["time", "period", "created"]},
    "order": {"strong": ["order_id", "order_no", "order_number", "invoice_no", "invoice_number",
                         "invoice_id", "transaction_id", "txn_id"],
              "weak": ["invoice"]},
    "customer": {"strong": ["customer_id", "customer_name", "customer", "client_id", "user_id",
                            "account_id", "member_id"],
                 "weak": []},
    "product": {"strong": ["product_name", "product", "product_id", "item_name", "sku"], "weak": ["item"]},
    "profit": {"strong": ["net_profit", "gross_profit", "profit", "profit_amount"], "weak": []},
    "cost": {"strong": ["total_cost", "cogs", "cost"], "weak": ["expenses", "expense"]},
    "discount": {"strong": ["discount_percent", "discount_pct", "discount_rate", "discount"], "weak": []},
    "quantity": {"strong": ["quantity", "qty", "units_sold", "items_sold"], "weak": ["units"]},
    "price": {"strong": ["unit_price", "selling_price", "price", "mrp"], "weak": []},
    "revenue": {"strong": ["revenue", "total_sales", "sales_amount", "net_sales", "sales", "turnover",
                           "gmv", "order_value", "total_amount", "sale_amount", "sales_revenue"],
                "weak": ["amount"]},
    "category": {"strong": ["category", "sub_category", "product_category", "product_line",
                            "department", "brand"],
                 "weak": ["segment"]},
    "region": {"strong": ["region", "country", "state", "city", "zone", "market", "territory"], "weak": []},
}

ROLE_ORDER = ["date", "order", "customer", "product", "profit", "cost", "discount",
              "quantity", "price", "revenue", "category", "region"]

ROLE_KINDS = {
    "date": {"datetime"},
    "order": {"id", "categorical", "text", "discrete"},
    "customer": {"id", "categorical", "text", "discrete"},
    "product": {"id", "categorical", "text", "discrete"},
    "category": {"categorical", "discrete"},
    "region": {"categorical", "discrete"},
    "quantity": {"numeric", "discrete"},
    "discount": {"numeric", "discrete"},
}  # every other role (revenue, profit, cost, price) must be continuous numeric

# words that mean "this is a ratio / derived figure", not an amount of money
_NOT_AN_AMOUNT = {"pct", "percent", "percentage", "margin", "ratio", "rate", "avg", "average", "per",
                  "growth", "change", "score", "index", "rank", "count", "num", "flag", "target"}

NUMERIC_ROLES = {"profit", "cost", "discount", "quantity", "price", "revenue"}
ADDITIVE_ROLES = {"profit", "cost", "quantity", "revenue"}   # safe to add up


def _role_values_ok(role: str, s: pd.Series) -> bool:
    """A role must also make sense in the data, not only in the name."""
    d = s.dropna()
    if len(d) == 0:
        return False
    if role in ("revenue", "cost", "price", "quantity") and (d < 0).mean() > 0.10:
        return False
    if role == "quantity" and ((d % 1) == 0).mean() < 0.9:
        return False
    if role == "discount":
        in_fraction = ((d >= 0) & (d <= 1)).mean() >= 0.95
        in_percent = ((d >= 0) & (d <= 100)).mean() >= 0.95
        if not (in_fraction or in_percent):
            return False
    return True


class Dataset:
    """A loaded, type-inferred dataset plus everything we know about it."""

    def __init__(self, df: pd.DataFrame, name: str, path: str | None = None):
        self.name = name
        self.path = path
        self.notes: list[str] = []
        self.cache: dict = {}

        self.forced_metric = None
        self.role_info = {}
        self.raw_shape = df.shape
        self.df = self._prepare(df)
        self.kinds = self._classify()
        for c, k in self.kinds.items():
            # coded columns stored as 1.0 / 0.0 floats are shown as 1 / 0
            if k == "discrete" and pd.api.types.is_float_dtype(self.df[c]) and not self.df[c].isna().any():
                self.df[c] = self.df[c].astype("int64")
        self.roles = self._detect_roles()

    # ---------------- preparation ----------------

    def _prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()

        # tidy + de-duplicate column names
        seen: dict[str, int] = {}
        new_cols = []
        for c in df.columns:
            c = re.sub(r"\s+", " ", str(c)).strip() or "unnamed"
            if c in seen:
                seen[c] += 1
                c = f"{c}_{seen[c]}"
            else:
                seen[c] = 0
            new_cols.append(c)
        df.columns = new_cols

        # drop "Unnamed: 0" style index columns
        for c in list(df.columns):
            if c.lower().startswith("unnamed") and df[c].isna().all():
                df = df.drop(columns=c)

        for c in df.columns:
            s = df[c]
            if not _is_texty(s):
                continue

            s_obj = s.astype("object")
            norm = _norm(c)

            if not _ID_HINT.search(norm):
                as_num = _coerce_numeric(s_obj)
                if as_num is not None:
                    df[c] = as_num
                    self.notes.append(f"'{c}' converted from text to numbers")
                    continue

            as_dt = _coerce_datetime(s_obj, c)
            if as_dt is not None:
                df[c] = as_dt
                self.notes.append(f"'{c}' parsed as dates")
                continue

            # normalise blanks in text columns
            df[c] = s_obj.map(lambda v: v.strip() if isinstance(v, str) else v).replace(
                {k: np.nan for k in _NA_STRINGS if k}
            )
            df[c] = df[c].replace(r"^\s*$", np.nan, regex=True)
            self._merge_spelling_variants(df, c)

        self._derive_period(df)
        return df

    def _merge_spelling_variants(self, df: pd.DataFrame, c: str):
        """'north', 'NORTH' and 'North ' are the same category: map them to the most common spelling."""
        s = df[c]
        if s.nunique(dropna=True) > max(50, len(s) * 0.2):
            return  # free text or ids, leave alone
        key = s.map(lambda v: re.sub(r"\s+", " ", v).strip().casefold() if isinstance(v, str) else v)
        counts = s.value_counts()
        best = {}
        for val, n in counts.items():
            k = re.sub(r"\s+", " ", val).strip().casefold()
            if k not in best:
                best[k] = re.sub(r"\s+", " ", val).strip()
        merged = key.map(lambda k: best.get(k, k) if isinstance(k, str) else k)
        changed = int((merged.fillna("") != s.fillna("")).sum())
        if changed:
            df[c] = merged
            self.notes.append(f"'{c}': merged {changed:,} spelling/case variants into one category")

    def _derive_period(self, df: pd.DataFrame):
        """Files with separate Year and Month columns have no date column. Build one so trends,
        seasonality and forecasts work."""
        if any(_is_datetime(df[c]) for c in df.columns) or "Period" in df.columns:
            return
        year = month = None
        for c in df.columns:
            norm = _norm(c)
            s = df[c]
            if year is None and norm in ("year", "yr", "fiscal_year", "calendar_year") and _is_numeric(s):
                nn = s.dropna()
                if len(nn) and (nn % 1 == 0).all() and 1800 <= nn.min() and nn.max() <= 2200:
                    year = c
            if month is None and norm in ("month", "mth", "month_num", "month_number"):
                if _is_numeric(s):
                    nn = s.dropna()
                    if len(nn) and (nn % 1 == 0).all() and 1 <= nn.min() and nn.max() <= 12:
                        month = c
                else:
                    names = s.dropna().astype(str).str.strip().str.lower().str[:3].map(_MONTH_NAMES)
                    if len(names) and names.notna().mean() >= 0.9:
                        month = c
        if year is None or month is None:
            return
        m = df[month] if _is_numeric(df[month]) else df[month].astype(str).str.strip().str.lower().str[:3].map(_MONTH_NAMES)
        period = pd.to_datetime(
            pd.DataFrame({"year": df[year], "month": m, "day": 1}), errors="coerce")
        if period.notna().mean() >= 0.9:
            df["Period"] = period
            self.notes.append(f"Built a 'Period' date column from '{year}' and '{month}' so trends and forecasts work")

    def _classify(self) -> dict:
        kinds = {}
        n = max(len(self.df), 1)
        for c in self.df.columns:
            s = self.df[c]
            nun = int(s.nunique(dropna=True))
            norm = _norm(c)
            mostly_missing = float(s.isna().mean()) > 0.5

            if _is_datetime(s):
                kinds[c] = "datetime"
            elif pd.api.types.is_bool_dtype(s):
                kinds[c] = "categorical"
            elif _is_numeric(s):
                looks_id = bool(_ID_HINT.search(norm)) and nun / n > 0.5
                index_like = (
                    pd.api.types.is_integer_dtype(s) and nun == n and n > 20
                    and s.dropna().is_monotonic_increasing
                )
                integer_like = bool((s.dropna() % 1 == 0).all()) if len(s.dropna()) else False
                nn = s.dropna()
                year_like = (integer_like and len(nn) > 0 and 1800 <= nn.min() and nn.max() <= 2200
                             and any(h in norm for h in _YEAR_HINTS))
                if looks_id or index_like:
                    kinds[c] = "id"
                elif nun <= 1:
                    kinds[c] = "sparse" if mostly_missing else "constant"
                elif integer_like and norm in _TEMPORAL_NAMES and nun <= 60:
                    kinds[c] = "discrete"      # month 1-12, weekday 0-6...: a label, never an average
                elif year_like:
                    # a handful of years is a useful grouping; many years (YearBuilt) is a date-like attribute
                    kinds[c] = "discrete" if nun <= 12 else "year"
                elif integer_like and nun <= min(12, max(2, int(0.1 * n))):
                    # whole numbers with few distinct values (1/2/3 region codes, 0/1 flags, ratings):
                    # these are labels or codes, so averaging or correlating them would be misleading
                    kinds[c] = "discrete"
                else:
                    kinds[c] = "numeric"
            else:
                if nun <= 1:
                    kinds[c] = "sparse" if mostly_missing else "constant"
                elif nun <= config.MAX_CATEGORY_LEVELS or nun / n <= 0.2:
                    kinds[c] = "categorical"
                elif nun / n > 0.9:
                    kinds[c] = "id"
                else:
                    kinds[c] = "text"
        return kinds

    def _detect_roles(self) -> dict:
        roles: dict[str, str] = {}
        self.role_info: dict[str, dict] = {}
        used: set[str] = set()

        for role in ROLE_ORDER:
            ok_kinds = ROLE_KINDS.get(role, {"numeric"})
            money = role in ("revenue", "profit", "cost")
            transactional = any(r in roles for r in ("date", "order", "customer"))
            best, best_score, best_info = None, 0.0, None

            for c in self.df.columns:
                if c in used or self.kinds.get(c) not in ok_kinds:
                    continue
                norm = _norm(c)
                tokens = norm.split("_")
                if money and set(tokens) & _NOT_AN_AMOUNT:
                    continue

                for tier in ("strong", "weak"):
                    if tier == "weak" and not transactional:
                        continue
                    for idx, alias in enumerate(ROLE_ALIASES[role][tier]):
                        a_tokens = alias.split("_")
                        exact = norm == alias
                        contained = any(tokens[i:i + len(a_tokens)] == a_tokens
                                        for i in range(len(tokens) - len(a_tokens) + 1))
                        if not (exact or contained):
                            continue
                        score = (100 if exact else 60) - idx * 0.5 - (30 if tier == "weak" else 0)
                        if score > best_score and (role not in NUMERIC_ROLES or _role_values_ok(role, self.df[c])):
                            best, best_score = c, score
                            best_info = {
                                "confidence": "high" if tier == "strong" else "medium",
                                "why": f"column name matches '{alias.replace('_', ' ')}'",
                            }
                        break  # first matching alias in this tier is enough

            if best:
                roles[role] = best
                used.add(best)
                self.role_info[role] = best_info
        return roles

    # ---------------- convenience ----------------

    @property
    def rows(self) -> int:
        return int(self.df.shape[0])

    @property
    def num_cols(self) -> list:
        return [c for c, k in self.kinds.items() if k == "numeric"]

    @property
    def cat_cols(self) -> list:
        """Columns that describe groups: text categories and coded values (1/2/3, 0/1)."""
        return [c for c, k in self.kinds.items() if k in ("categorical", "discrete")]

    @property
    def discrete_cols(self) -> list:
        return [c for c, k in self.kinds.items() if k == "discrete"]

    def is_metric(self, col) -> bool:
        """Can this column be summed / averaged as a measurement?"""
        k = self.kinds.get(col)
        return k == "numeric" or (k == "discrete" and self.metric_role(col) is not None)

    @property
    def mode(self) -> str:
        """'business' when revenue/profit was confidently found (or the user chose a metric), else 'profile'."""
        return "business" if self.primary_metric else "profile"

    @property
    def date_cols(self) -> list:
        return [c for c, k in self.kinds.items() if k == "datetime"]

    @property
    def date_col(self):
        return self.roles.get("date") or (self.date_cols[0] if self.date_cols else None)

    @property
    def primary_metric(self):
        """The main measurement. Never guessed: it is the revenue/profit column found by name,
        or a column the user chose ('use income as main metric'). Otherwise None."""
        if self.forced_metric and self.forced_metric in self.df.columns:
            return self.forced_metric
        for r in ("revenue", "profit"):
            if r in self.roles:
                return self.roles[r]
        return None

    def metric_role(self, col):
        for r, c in self.roles.items():
            if c == col:
                return r
        return None

    def is_additive(self, col) -> bool:
        """True only for columns where adding values up is meaningful (money, units).
        Everything else (age, income, scores...) is averaged instead."""
        return self.metric_role(col) in ADDITIVE_ROLES

    # ---------------- user corrections ----------------

    def _reset_analysis(self):
        self.cache.clear()

    def set_role(self, role: str, col: str):
        """Returns (ok, message)."""
        if col not in self.df.columns:
            return False, f"I don't have a column called '{col}'."
        if role in NUMERIC_ROLES and self.kinds.get(col) not in ROLE_KINDS.get(role, {"numeric"}):
            return False, f"'{col}' isn't a numeric measurement column, so it can't be the {role}."
        if role == "date" and self.kinds.get(col) != "datetime":
            return False, f"'{col}' doesn't contain dates I could read."
        for r, c in list(self.roles.items()):
            if c == col and r != role:
                del self.roles[r]
                self.role_info.pop(r, None)
        self.roles[role] = col
        self.role_info[role] = {"confidence": "you", "why": "set by you"}
        self._reset_analysis()
        return True, f"Okay. I'll treat '{col}' as the {role} column."

    def set_main_metric(self, col: str):
        if col not in self.df.columns or not _is_numeric(self.df[col]):
            return False, f"'{col}' isn't a numeric column, so it can't be the main metric."
        self.forced_metric = col
        self._reset_analysis()
        how = "added up" if self.is_additive(col) else "averaged"
        return True, f"Okay. '{col}' is now the main metric. Values will be {how} when I compare groups."

    def clear_roles(self):
        self.roles.clear()
        self.role_info.clear()
        self.forced_metric = None
        self._reset_analysis()

    @property
    def dims(self) -> list:
        """Columns worth grouping by, best first."""
        out = []
        for r in ("category", "region", "product", "customer"):
            c = self.roles.get(r)
            if c:
                out.append(c)
        others = [
            c for c in self.cat_cols
            if c not in out and 2 <= self.df[c].nunique() <= config.MAX_CATEGORY_LEVELS
        ]
        others.sort(key=lambda c: self.df[c].nunique())
        return out + others

    def find_column(self, text: str):
        """Fuzzy-match a phrase to a column name."""
        target = _spaced(text)
        if not target:
            return None
        cols = {c: _spaced(c) for c in self.df.columns}
        for c, n in cols.items():
            if n == target:
                return c
        for c, n in cols.items():
            if target in n.split() or n in target.split():
                return c
        for c, n in cols.items():
            if target in n or n in target:
                return c
        return None

    def columns_in_text(self, text: str) -> list:
        """All columns mentioned in a sentence, in order of appearance."""
        hay = f" {_singular(_spaced(text))} "
        hits = []
        for c in sorted(self.df.columns, key=lambda c: -len(str(c))):
            n = _singular(_spaced(c))
            if n and f" {n} " in hay:
                hits.append((hay.index(f" {n} "), c))
        # role words ("revenue", "profit", "region") also count
        for role, col in self.roles.items():
            if f" {role} " in hay and col not in [h[1] for h in hits]:
                hits.append((hay.index(f" {role} "), col))
        hits.sort()
        seen, ordered = set(), []
        for _, c in hits:
            if c not in seen:
                seen.add(c)
                ordered.append(c)
        return ordered

    # ---------------- summary for the UI ----------------

    def ui_summary(self) -> dict:
        prof = profile(self)
        return {
            "name": self.name,
            "rows": self.rows,
            "columns": int(self.df.shape[1]),
            "quality": prof["quality_score"],
            "missing_pct": round(prof["missing_pct"], 2),
            "duplicates": prof["duplicates"],
            "roles": self.roles,
            "role_info": [
                {"role": r, "column": c, "confidence": self.role_info.get(r, {}).get("confidence", "high")}
                for r, c in self.roles.items()
            ],
            "mode": self.mode,
            "main_metric": self.primary_metric,
            "kpis": [{"label": k["label"], "value": k["display"]} for k in kpis(self)[:6]],
            "schema": [
                {
                    "name": str(c),
                    "kind": self.kinds.get(c, "?"),
                    "missing": round(float(self.df[c].isna().mean() * 100), 1),
                }
                for c in self.df.columns[:60]
            ],
            "preview": self.df.head(5).astype(str).to_dict("records"),
        }


# ============================================================
# PROFILE
# ============================================================

def profile(ds: Dataset) -> dict:
    if "profile" in ds.cache:
        return ds.cache["profile"]

    df = ds.df
    rows, cols = df.shape
    total_cells = max(rows * cols, 1)

    miss = df.isna().sum()
    missing_total = int(miss.sum())
    missing_pct = missing_total / total_cells * 100
    dup = int(df.duplicated().sum())
    dup_pct = dup / max(rows, 1) * 100

    constant = [c for c, k in ds.kinds.items() if k == "constant"]
    high_missing = [c for c in df.columns if df[c].isna().mean() > 0.3]

    score = 100.0
    score -= min(35, missing_pct * 1.8)
    score -= min(15, dup_pct * 3)
    score -= min(10, len(constant) * 4)
    score -= min(10, len(high_missing) * 5)
    score = max(0, round(score))

    counts: dict = {}
    for k in ds.kinds.values():
        counts[k] = counts.get(k, 0) + 1

    result = {
        "rows": int(rows),
        "columns": int(cols),
        "memory_mb": round(float(df.memory_usage(deep=True).sum()) / 1e6, 2),
        "duplicates": dup,
        "duplicate_pct": dup_pct,
        "missing_total": missing_total,
        "missing_pct": missing_pct,
        "missing_by_col": {
            str(c): {"count": int(v), "pct": round(v / max(rows, 1) * 100, 1)}
            for c, v in miss.items() if v > 0
        },
        "kind_counts": counts,
        "constant_columns": constant,
        "high_missing_columns": high_missing,
        "quality_score": int(score),
        "notes": list(ds.notes),
    }
    ds.cache["profile"] = result
    return result


# ============================================================
# NUMERIC SUMMARY / OUTLIERS / CORRELATION
# ============================================================

def describe_numeric(ds: Dataset) -> dict:
    out = {}
    for c in ds.num_cols:
        s = ds.df[c].dropna()
        if s.empty:
            continue
        mean = float(s.mean())
        std = float(s.std()) if len(s) > 1 else 0.0
        out[c] = {
            "count": int(s.count()),
            "mean": mean,
            "median": float(s.median()),
            "std": std,
            "min": float(s.min()),
            "max": float(s.max()),
            "skew": float(s.skew()) if len(s) > 2 else 0.0,
            "cv": abs(std / mean) if mean else float("nan"),
            "negatives": int((s < 0).sum()),
            "zeros": int((s == 0).sum()),
        }
    return out


def outliers(ds: Dataset) -> dict:
    if "outliers" in ds.cache:
        return ds.cache["outliers"]
    out = {}
    for c in ds.num_cols:
        s = ds.df[c].dropna()
        if len(s) < 8:
            continue
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr == 0:
            continue
        lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        mask = (s < lo) | (s > hi)
        n = int(mask.sum())
        if n:
            out[c] = {
                "count": n,
                "pct": n / len(s) * 100,
                "lower": float(lo),
                "upper": float(hi),
                "max": float(s.max()),
                "min": float(s.min()),
                "median": float(s.median()),
            }
    ds.cache["outliers"] = out
    return out


def correlations(ds: Dataset, top: int = 10) -> dict:
    if "corr" in ds.cache:
        return ds.cache["corr"]
    cols = [c for c in ds.num_cols if ds.df[c].nunique() > 2][:25]
    if len(cols) < 2:
        res = {"matrix": None, "pairs": []}
        ds.cache["corr"] = res
        return res

    matrix = ds.df[cols].corr(numeric_only=True)
    pairs = []
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            v = matrix.iloc[i, j]
            if pd.notna(v):
                pairs.append({"a": cols[i], "b": cols[j], "r": round(float(v), 3)})
    pairs.sort(key=lambda p: abs(p["r"]), reverse=True)
    res = {"matrix": matrix, "pairs": pairs[:top]}
    ds.cache["corr"] = res
    return res


# ============================================================
# KPIs
# ============================================================

def kpis(ds: Dataset) -> list:
    """Headline numbers. Labels always carry the real column name, e.g. 'Total Sales', never a generic guess."""
    if "kpis" in ds.cache:
        return ds.cache["kpis"]

    df, r = ds.df, ds.roles
    out = []

    def add(label, raw, display=None):
        out.append({"label": label, "raw": raw, "display": display if display is not None else human(raw)})

    rev = df[r["revenue"]].sum() if "revenue" in r else None
    prof = df[r["profit"]].sum() if "profit" in r else None
    cost = df[r["cost"]].sum() if "cost" in r else None

    if rev is not None:
        add(f"Total {r['revenue']}", rev)
    if prof is not None:
        add(f"Total {r['profit']}", prof)
    if cost is not None:
        add(f"Total {r['cost']}", cost)

    margin = None
    if rev and prof is not None:
        margin = prof / rev * 100
    elif rev and cost is not None:
        margin = (rev - cost) / rev * 100
    if margin is not None:
        add("Profit margin", margin, pct(margin))

    if "order" in r:
        orders = int(df[r["order"]].nunique())
        add("Orders", orders, f"{orders:,}")
        if rev:
            add(f"Avg {r['revenue']} per order", rev / max(orders, 1))
    elif rev is not None:
        add(f"Avg {r['revenue']}", df[r["revenue"]].mean())

    if "customer" in r:
        custs = int(df[r["customer"]].nunique())
        add("Customers", custs, f"{custs:,}")
        if rev:
            add(f"{r['revenue']} per customer", rev / max(custs, 1))

    if "quantity" in r:
        units = df[r["quantity"]].sum()
        add(f"Total {r['quantity']}", units, f"{int(units):,}" if float(units).is_integer() else human(units))

    if "discount" in r:
        d = df[r["discount"]].dropna()
        if len(d):
            avg = d.mean() * (100 if d.max() <= 1 else 1)
            add(f"Avg {r['discount']}", avg, pct(avg))

    if rev is None and prof is None and cost is None:
        # no money measure: describe the data exactly as it is, keeping any counts found above
        head = [{"label": "Records", "raw": ds.rows, "display": f"{ds.rows:,}"},
                {"label": "Columns", "raw": int(df.shape[1]), "display": f"{df.shape[1]}"}]
        out = head + [k for k in out if k["label"] not in ("Records", "Columns")]
        for c in ds.num_cols:
            if len(out) >= 6:
                break
            out.append({"label": f"Avg {c}", "raw": float(df[c].mean()), "display": human(float(df[c].mean()))})

    ds.cache["kpis"] = out
    return out


# ============================================================
# SEGMENT PROFILE (works on any dataset, no business columns needed)
# ============================================================

def segment_profile(ds: Dataset, max_dims: int = 3, max_metrics: int = 4) -> list:
    """Average of the main numeric columns for each group of a category or coded column."""
    if "segprof" in ds.cache:
        return ds.cache["segprof"]

    metrics = ds.num_cols[:max_metrics]
    dims = [c for c in ds.dims if 2 <= ds.df[c].nunique() <= 8][:max_dims]
    out = []
    if metrics:
        for dim in dims:
            if dim in metrics:
                continue
            grp = ds.df.groupby(dim, observed=True)
            means = grp[metrics].mean()
            counts = grp.size()
            out.append({"dim": dim, "means": means, "counts": counts})
    ds.cache["segprof"] = out
    return out


def segment_differences(ds: Dataset, limit: int = 3) -> list:
    """Largest between-group differences (only groups with enough rows to trust)."""
    found = []
    n = max(ds.rows, 1)
    for sp in segment_profile(ds):
        counts = sp["counts"]
        ok = counts[(counts >= 30) & (counts / n >= 0.05)].index
        if len(ok) < 2:
            continue
        for m in sp["means"].columns:
            overall = float(ds.df[m].mean())
            if not overall:
                continue
            col = sp["means"].loc[ok, m]
            spread = float((col.max() - col.min()) / abs(overall))
            if spread >= 0.4:
                found.append({
                    "dim": sp["dim"], "metric": m, "spread": spread, "overall": overall,
                    "high": (col.idxmax(), float(col.max())), "low": (col.idxmin(), float(col.min())),
                })
    found.sort(key=lambda x: -x["spread"])
    return found[:limit]


# ============================================================
# TIME SERIES + FORECAST
# ============================================================

def time_series(ds: Dataset, metric=None, freq=None):
    """Aggregate the metric over time. Returns (Series, freq, partial_last) or None."""
    date = ds.date_col
    metric = metric or ds.primary_metric
    if not date or not metric or metric not in ds.df.columns:
        return None

    d = ds.df[[date, metric]].dropna(subset=[date])
    if len(d) < 6:
        return None

    span = (d[date].max() - d[date].min()).days
    if freq is None:
        freq = "MS" if span >= 90 else ("W" if span >= 21 else "D")

    additive = ds.is_additive(metric)
    grouped = d.set_index(date)[metric].sort_index().resample(freq)
    s = grouped.sum() if additive else grouped.mean()
    if not additive:
        s = s.interpolate()

    if len(s) < 4:
        return None

    # is the final period incomplete?
    last_date = d[date].max()
    partial = False
    if freq == "MS":
        period_end = s.index[-1] + pd.offsets.MonthEnd(0)
        partial = (period_end - last_date).days > 2
    elif freq == "W":
        partial = (s.index[-1] - last_date).days > 2

    return s, freq, partial


def _fit_predict(y: np.ndarray, h: int, season: int):
    m = len(y)
    x = np.arange(m)
    seas = None

    if season and m >= 2 * season:
        b, a = np.polyfit(x, y, 1)
        resid = y - (b * x + a)
        pos = x % season
        seas = np.array([resid[pos == k].mean() if (pos == k).any() else 0.0 for k in range(season)])
        seas -= seas.mean()
        b, a = np.polyfit(x, y - seas[pos], 1)
        fitted = b * x + a + seas[pos]
    else:
        b, a = np.polyfit(x, y, 1)
        fitted = b * x + a

    resid_std = float(np.std(y - fitted, ddof=2 if m > 3 else 0))
    xf = np.arange(m, m + h)
    yhat = b * xf + a
    if seas is not None:
        yhat = yhat + seas[xf % season]
    return yhat, resid_std, fitted


def forecast_series(s: pd.Series, freq: str, h: int | None = None):
    h = h or config.FORECAST_PERIODS
    y = s.values.astype(float)
    n = len(y)
    if n < 6:
        return None

    season = {"MS": 12, "W": 52, "D": 7}.get(freq, 0)
    if freq == "W" and n < 104:
        season = 0

    window = {"MS": 36, "W": 104, "D": 120}.get(freq, n)
    y_fit = y[-window:]

    yhat, resid_std, _ = _fit_predict(y_fit, h, season)

    # backtest on the last few points to report honest error
    mape = None
    k = max(2, min(6, n // 4))
    if n - k >= 6:
        try:
            pred, _, _ = _fit_predict(y[: n - k][-window:], k, season)
            actual = y[n - k:]
            nz = actual != 0
            if nz.any():
                mape = float(np.mean(np.abs((actual[nz] - pred[nz]) / actual[nz])) * 100)
        except Exception:
            mape = None

    non_negative = bool((y >= 0).all())
    idx = pd.date_range(s.index[-1], periods=h + 1, freq=freq)[1:]
    steps = np.arange(1, h + 1)
    band = 1.96 * resid_std * np.sqrt(1 + steps / max(len(y_fit), 1))
    lo, hi = yhat - band, yhat + band
    if non_negative:
        yhat, lo, hi = np.clip(yhat, 0, None), np.clip(lo, 0, None), np.clip(hi, 0, None)

    df = pd.DataFrame({"yhat": yhat, "lo": lo, "hi": hi}, index=idx)
    method = "linear trend" + (f" + {season}-period seasonality" if season and n >= 2 * season else "")
    return {"forecast": df, "mape": mape, "method": method, "h": h}


def ts_analysis(s: pd.Series, freq: str, partial: bool) -> dict:
    complete = s.iloc[:-1] if partial and len(s) > 6 else s
    y = complete.values.astype(float)
    n = len(y)
    x = np.arange(n)

    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_res = float(((y - pred) ** 2).sum())
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    mean = float(y.mean())
    slope_pct = slope / mean * 100 if mean else 0.0

    if r2 >= 0.25 and abs(slope_pct) >= 0.5:
        direction = "growing" if slope > 0 else "declining"
    else:
        direction = "flat"

    change_pct = None
    if n >= 2 and y[-2] != 0:
        change_pct = float((y[-1] - y[-2]) / abs(y[-2]) * 100)

    yoy = None
    if freq == "MS" and n >= 24:
        last12, prev12 = y[-12:].sum(), y[-24:-12].sum()
        if prev12:
            yoy = float((last12 - prev12) / abs(prev12) * 100)

    seasonality = None
    if freq == "MS" and n >= 24:
        tmp = pd.Series(y, index=complete.index)
        by_month = tmp.groupby(tmp.index.month).mean()
        idx = by_month / by_month.mean()
        seasonality = {
            "index": idx.to_dict(),
            "peak_month": int(idx.idxmax()),
            "peak_value": float(idx.max()),
            "trough_month": int(idx.idxmin()),
            "trough_value": float(idx.min()),
        }

    # anomalies via modified z-score against a rolling median
    anomalies = []
    if n >= 8:
        med = pd.Series(y).rolling(5, center=True, min_periods=3).median().values
        resid = y - med
        mad = np.median(np.abs(resid - np.median(resid)))
        if mad > 0:
            z = 0.6745 * resid / mad
            for i in np.argsort(-np.abs(z))[:5]:
                if abs(z[i]) > 3.5:
                    anomalies.append({
                        "period": complete.index[i],
                        "value": float(y[i]),
                        "expected": float(med[i]),
                        "z": float(z[i]),
                    })

    return {
        "periods": n,
        "freq": freq,
        "partial_last": partial,
        "total": float(y.sum()),
        "mean": mean,
        "slope": float(slope),
        "slope_pct": float(slope_pct),
        "r2": float(r2),
        "direction": direction,
        "last_value": float(y[-1]),
        "last_period": complete.index[-1],
        "change_pct": change_pct,
        "yoy_pct": yoy,
        "best_period": complete.idxmax(),
        "best_value": float(y.max()),
        "worst_period": complete.idxmin(),
        "worst_value": float(y.min()),
        "cv": float(np.std(y) / mean) if mean else float("nan"),
        "seasonality": seasonality,
        "anomalies": anomalies,
    }


def time_analysis(ds: Dataset, metric=None):
    key = f"time:{metric}"
    if key in ds.cache:
        return ds.cache[key]
    ts = time_series(ds, metric)
    if ts is None:
        ds.cache[key] = None
        return None
    s, freq, partial = ts
    result = {
        "metric": metric or ds.primary_metric,
        "series": s,
        "freq": freq,
        "partial_last": partial,
        "stats": ts_analysis(s, freq, partial),
    }
    s_fc = s.iloc[:-1] if partial and len(s) > 6 else s
    result["forecast"] = forecast_series(s_fc, freq)
    ds.cache[key] = result
    return result


# ============================================================
# GROUPS / CONCENTRATION
# ============================================================

def group_analysis(ds: Dataset, dim: str, metric=None) -> pd.DataFrame | None:
    metric = metric or ds.primary_metric
    if not metric or dim not in ds.df.columns or metric not in ds.df.columns:
        return None

    key = f"group:{dim}:{metric}"
    if key in ds.cache:
        return ds.cache[key]

    df = ds.df
    g = df.groupby(dim, observed=True, dropna=True)
    additive = ds.is_additive(metric)

    out = pd.DataFrame({
        "value": g[metric].sum() if additive else g[metric].mean(),
        "avg": g[metric].mean(),
        "rows": g.size(),
    })

    prof_col = ds.roles.get("profit")
    if prof_col and prof_col != metric:
        out["profit"] = g[prof_col].sum()
        if additive:
            with np.errstate(divide="ignore", invalid="ignore"):
                out["margin"] = out["profit"] / out["value"] * 100
    elif prof_col == metric:
        out["profit"] = out["value"]

    out = out.sort_values("value", ascending=False)
    total = out["value"].sum()
    if additive and total:
        out["share"] = out["value"] / total * 100
        out["cum_share"] = out["share"].cumsum()
    ds.cache[key] = out
    return out


def concentration(group_df: pd.DataFrame) -> dict | None:
    if group_df is None or "share" not in group_df or len(group_df) < 3:
        return None
    pos = group_df[group_df["value"] > 0]
    if pos.empty:
        return None
    n = len(pos)
    k = max(1, math.ceil(n * 0.2))
    shares = pos["value"] / pos["value"].sum()
    return {
        "items": n,
        "top1_name": pos.index[0],
        "top1_share": float(shares.iloc[0] * 100),
        "top20_count": k,
        "top20_share": float(shares.iloc[:k].sum() * 100),
        "hhi": float((shares ** 2).sum()),
    }


# ============================================================
# DISCOUNT IMPACT
# ============================================================

def discount_impact(ds: Dataset) -> dict | None:
    if "discount" in ds.cache:
        return ds.cache["discount"]
    r = ds.roles
    res = None
    if "discount" in r and ("profit" in r or ("revenue" in r and "cost" in r)):
        df = ds.df
        disc = df[r["discount"]].astype(float)
        scale = 100 if disc.max(skipna=True) <= 1 else 1
        disc_pct = disc * scale

        if "profit" in r:
            profit = df[r["profit"]]
        else:
            profit = df[r["revenue"]] - df[r["cost"]]
        revenue = df[r["revenue"]] if "revenue" in r else None

        tmp = pd.DataFrame({"disc": disc_pct, "profit": profit})
        if revenue is not None:
            tmp["revenue"] = revenue
        tmp = tmp.dropna(subset=["disc", "profit"])

        if len(tmp) >= 30 and tmp["disc"].nunique() > 1:
            bins = [-0.01, 0.001, 10, 20, 30, 1000]
            labels = ["0%", "0-10%", "10-20%", "20-30%", "30%+"]
            tmp["band"] = pd.cut(tmp["disc"], bins=bins, labels=labels)
            g = tmp.groupby("band", observed=True)
            table = pd.DataFrame({"rows": g.size(), "avg_profit": g["profit"].mean(), "profit": g["profit"].sum()})
            if "revenue" in tmp:
                table["revenue"] = g["revenue"].sum()
                with np.errstate(divide="ignore", invalid="ignore"):
                    table["margin"] = table["profit"] / table["revenue"] * 100
            corr = float(tmp["disc"].corr(tmp["profit"]))

            within = None
            grp_col = ds.roles.get("category") or next((d for d in ds.dims if ds.df[d].nunique() <= 12), None)
            if grp_col and "revenue" in tmp and len(table) >= 2:
                tmp["grp"] = df.loc[tmp.index, grp_col]
                lo_band, hi_band = table.index[0], table.index[-1]
                tested = worse = 0
                for _, sub in tmp.groupby("grp", observed=True):
                    lo, hi = sub[sub["band"] == lo_band], sub[sub["band"] == hi_band]
                    if len(lo) >= 20 and len(hi) >= 20 and lo["revenue"].sum() and hi["revenue"].sum():
                        tested += 1
                        if hi["profit"].sum() / hi["revenue"].sum() < lo["profit"].sum() / lo["revenue"].sum():
                            worse += 1
                if tested >= 2:
                    within = {"dim": grp_col, "tested": tested, "worse": worse}
            res = {"table": table, "corr": corr, "within": within}
    ds.cache["discount"] = res
    return res


# ============================================================
# RFM CUSTOMER SEGMENTATION
# ============================================================

def rfm(ds: Dataset) -> dict | None:
    if "rfm" in ds.cache:
        return ds.cache["rfm"]
    r = ds.roles
    res = None
    if "customer" in r and ds.date_col and ("revenue" in r):
        df = ds.df[[r["customer"], ds.date_col, r["revenue"]] + ([r["order"]] if "order" in r else [])]
        df = df.dropna(subset=[r["customer"], ds.date_col])
        if df[r["customer"]].nunique() >= 20:
            snapshot = df[ds.date_col].max() + pd.Timedelta(days=1)
            g = df.groupby(r["customer"])
            table = pd.DataFrame({
                "recency": (snapshot - g[ds.date_col].max()).dt.days,
                "frequency": g[r["order"]].nunique() if "order" in r else g.size(),
                "monetary": g[r["revenue"]].sum(),
            })

            def score(s, ascending):
                ranked = s.rank(method="first", ascending=ascending)
                return pd.qcut(ranked, 5, labels=[1, 2, 3, 4, 5]).astype(int)

            table["R"] = score(table["recency"], ascending=False)   # recent = high
            table["F"] = score(table["frequency"], ascending=True)
            table["M"] = score(table["monetary"], ascending=True)

            def segment(row):
                R, F, M = row["R"], row["F"], row["M"]
                if R >= 4 and F >= 4 and M >= 4:
                    return "Champions"
                if R >= 3 and F >= 3:
                    return "Loyal"
                if R >= 4 and F <= 2:
                    return "New / Promising"
                if R <= 2 and (F >= 4 or M >= 4):
                    return "At Risk (high value)"
                if R <= 2 and F <= 2:
                    return "Lost / Hibernating"
                return "Needs Attention"

            table["segment"] = table.apply(segment, axis=1)

            # how long customers normally wait between purchases (gives "lapsed" a real yardstick)
            dates = df[[r["customer"], ds.date_col]].drop_duplicates().sort_values([r["customer"], ds.date_col])
            gaps = dates.groupby(r["customer"])[ds.date_col].diff().dt.days.dropna()
            gaps = gaps[gaps > 0]
            typical_gap = float(gaps.median()) if len(gaps) >= 20 else None
            total_rev = table["monetary"].sum()
            summary = table.groupby("segment").agg(
                customers=("monetary", "size"),
                revenue=("monetary", "sum"),
                avg_recency=("recency", "mean"),
                avg_frequency=("frequency", "mean"),
            )
            summary["customer_share"] = summary["customers"] / len(table) * 100
            summary["revenue_share"] = summary["revenue"] / total_rev * 100 if total_rev else 0
            summary = summary.sort_values("revenue", ascending=False)
            res = {"customers": table, "summary": summary, "typical_gap_days": typical_gap}
    ds.cache["rfm"] = res
    return res


# ============================================================
# FULL ANALYSIS
# ============================================================

def full_analysis(ds: Dataset) -> dict:
    if "analysis" in ds.cache:
        return ds.cache["analysis"]

    metric = ds.primary_metric
    groups = {}
    for dim in ds.dims[:5]:
        g = group_analysis(ds, dim, metric)
        if g is not None and len(g) >= 2:
            groups[dim] = g

    result = {
        "profile": profile(ds),
        "numeric": describe_numeric(ds),
        "outliers": outliers(ds),
        "correlations": correlations(ds),
        "kpis": kpis(ds),
        "time": time_analysis(ds, metric),
        "groups": groups,
        "concentration": {d: concentration(g) for d, g in groups.items()},
        "discount": discount_impact(ds),
        "rfm": rfm(ds),
        "segments": segment_profile(ds),
        "metric": metric,
    }
    ds.cache["analysis"] = result
    return result


# ============================================================
# CLEANING
# ============================================================

def clean_dataset(ds: Dataset, strategy: str = "median", cap_outliers: bool = False,
                  drop_missing_threshold: float = 0.7):
    """Return (cleaned_df, report). The original dataset is never modified."""
    df = ds.df.copy()
    report = {
        "original_rows": len(df),
        "original_columns": df.shape[1],
        "steps": [],
        "imputed": {},
        "dropped_columns": [],
        "capped": {},
    }

    before = len(df)
    df = df.dropna(how="all")
    if len(df) < before:
        report["steps"].append(f"Removed {before - len(df):,} completely empty rows")

    empty_cols = [c for c in df.columns if df[c].isna().all()]
    if empty_cols:
        df = df.drop(columns=empty_cols)
        report["dropped_columns"] += empty_cols
        report["steps"].append(f"Dropped {len(empty_cols)} empty columns")

    before = len(df)
    df = df.drop_duplicates()
    report["duplicates_removed"] = before - len(df)
    if before - len(df):
        report["steps"].append(f"Removed {before - len(df):,} duplicate rows")

    sparse = [c for c in df.columns if df[c].isna().mean() > drop_missing_threshold]
    if sparse:
        df = df.drop(columns=sparse)
        report["dropped_columns"] += sparse
        report["steps"].append(
            f"Dropped {len(sparse)} columns with over {int(drop_missing_threshold * 100)}% missing: "
            + ", ".join(sparse[:5])
        )

    for note in ds.notes:
        report["steps"].append(note)

    report["invalid"] = {}
    for c in [c for c in df.columns if _is_numeric(df[c]) and ds.kinds.get(c) in ("numeric", "discrete")]:
        s = df[c]
        bad = pd.Series(False, index=s.index)
        if _COUNT_HINT.search(_norm(c)):
            bad |= s < 0                                  # negative units / quantities are impossible
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0 and ds.kinds.get(c) == "numeric":
            bad |= (s > q3 + 10 * iqr) | (s < q1 - 10 * iqr)   # absurd values like 999999 (entry errors)
        n = int(bad.sum())
        if n and n <= max(3, len(s) * 0.05):
            df[c] = s.mask(bad)
            report["invalid"][c] = n
    if report["invalid"]:
        report["steps"].append("Treated impossible or extreme values as missing in "
                               + ", ".join(f"{c} ({n})" for c, n in report["invalid"].items()) + " and filled them in")

    for c in df.columns:
        miss = int(df[c].isna().sum())
        if not miss:
            continue
        s = df[c]
        if _is_numeric(s):
            if strategy == "mean" and abs(s.skew()) < 1:
                value, how = s.mean(), "mean"
            else:
                value, how = s.median(), "median"
            if pd.notna(value):
                df[c] = s.fillna(value)
                report["imputed"][c] = {"count": miss, "method": how, "value": float(value)}
        elif _is_datetime(s):
            df[c] = s.ffill().bfill()
            report["imputed"][c] = {"count": miss, "method": "carry forward", "value": None}
        else:
            mode = s.mode()
            if not mode.empty:
                df[c] = s.fillna(mode.iloc[0])
                report["imputed"][c] = {"count": miss, "method": "most frequent", "value": str(mode.iloc[0])}

    if cap_outliers:
        for c in [c for c in df.columns if _is_numeric(df[c]) and ds.kinds.get(c) == "numeric"]:
            s = df[c]
            q1, q3 = s.quantile(0.25), s.quantile(0.75)
            iqr = q3 - q1
            if iqr == 0:
                continue
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            n = int(((s < lo) | (s > hi)).sum())
            if n:
                df[c] = s.clip(lo, hi)
                report["capped"][c] = n
        if report["capped"]:
            report["steps"].append(f"Capped outliers in {len(report['capped'])} columns (IQR rule)")

    report["final_rows"] = len(df)
    report["final_columns"] = df.shape[1]
    return df, report


def save_cleaned(ds: Dataset, df: pd.DataFrame) -> Path:
    stem = Path(ds.name).stem
    path = config.OUTPUT_DIR / f"{stem}_cleaned.csv"
    df.to_csv(path, index=False)
    return path
