"""
Data analyst: answers ANY question about the loaded dataset by writing pandas code and running it.

    ask_data(ds, question, generate) -> AnalystResult

The model never guesses numbers. It writes a few lines of pandas, the code is checked, executed on a copy
of the data, and the printed answer comes from that execution. The exact code is returned so the user can
see how every figure was produced. If the first attempt fails, the error is fed back once for a fix.

SAFETY. The code runs in-process, so it is validated first:
    - only `df`, `pd` and `np` exist; no imports, no file or network access, no `open`/`exec`/`eval`
    - no attribute or name starting with an underscore, no while-loops, no class/def/with/try
    - pandas methods that read or write files, or evaluate strings, are blocked
    - a time limit and a size limit apply to the result
This is a strong guard for a personal assistant on your own machine, not a security boundary against a
determined attacker.
"""

from __future__ import annotations

import ast
import re
import threading
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

import analytics as A
from analytics import Dataset, human

MAX_CODE_CHARS = 2500
MAX_NODES = 500
TIMEOUT_S = 10

BLOCKED_NAMES = {
    "__import__", "open", "exec", "eval", "compile", "input", "getattr", "setattr", "delattr", "globals",
    "locals", "vars", "dir", "breakpoint", "exit", "quit", "help", "type", "object", "os", "sys", "subprocess",
    "builtins", "importlib", "socket", "shutil", "pathlib", "memoryview", "classmethod", "staticmethod", "super",
}
BLOCKED_ATTRS = {
    "to_csv", "to_excel", "to_pickle", "to_json", "to_sql", "to_parquet", "to_clipboard", "to_html", "to_latex",
    "to_feather", "to_hdf", "to_stata", "to_xml", "to_markdown", "read_csv", "read_excel", "read_json",
    "read_pickle", "read_sql", "read_parquet", "read_html", "read_clipboard", "read_table", "read_fwf",
    "read_feather", "read_hdf", "read_xml", "eval", "query", "pipe", "load", "save", "savetxt", "loadtxt",
    "fromfile", "tofile", "system", "popen", "memmap", "ctypeslib", "testing",
}
BLOCKED_NODES = (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal, ast.While, ast.ClassDef, ast.FunctionDef,
                 ast.AsyncFunctionDef, ast.With, ast.AsyncWith, ast.Try, ast.Raise, ast.Delete, ast.Await,
                 ast.Yield, ast.YieldFrom)

SAFE_BUILTINS = {
    n: __builtins__[n] if isinstance(__builtins__, dict) else getattr(__builtins__, n)
    for n in ("len", "range", "min", "max", "sum", "abs", "round", "sorted", "list", "dict", "set", "tuple",
              "enumerate", "zip", "str", "int", "float", "bool", "any", "all", "map", "filter", "isinstance",
              "reversed", "divmod", "pow", "slice", "None", "True", "False")
    if (n in __builtins__ if isinstance(__builtins__, dict) else hasattr(__builtins__, n))
}
SAFE_BUILTINS.update({"None": None, "True": True, "False": False})


class Unsafe(Exception):
    pass


def validate(code: str) -> ast.AST:
    if len(code) > MAX_CODE_CHARS:
        raise Unsafe("the code was too long")
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise Unsafe(f"syntax error: {e.msg} (line {e.lineno})")

    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise Unsafe("the code was too complex")
        if isinstance(node, BLOCKED_NODES):
            raise Unsafe(f"'{type(node).__name__}' statements are not allowed")
        if isinstance(node, ast.Name) and (node.id in BLOCKED_NAMES or node.id.startswith("_")):
            raise Unsafe(f"the name '{node.id}' is not allowed")
        if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in BLOCKED_ATTRS):
            raise Unsafe(f"the attribute '{node.attr}' is not allowed")
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "__" in node.value:
            raise Unsafe("strings containing '__' are not allowed")
    return tree


# ------------------------------------------------------------
# execution
# ------------------------------------------------------------

def run(code: str, df: pd.DataFrame) -> dict:
    tree = validate(code)
    scope = {"df": df.copy(), "pd": pd, "np": np}
    box: dict = {}

    def target():
        try:
            exec(compile(tree, "<analyst>", "exec"), {"__builtins__": SAFE_BUILTINS}, scope)
            box["ok"] = True
        except Exception as e:                       # noqa: BLE001 - anything the generated code raises
            box["error"] = f"{type(e).__name__}: {e}"

    th = threading.Thread(target=target, daemon=True)
    th.start()
    th.join(TIMEOUT_S)
    if th.is_alive():
        raise Unsafe(f"the calculation took longer than {TIMEOUT_S} seconds")
    if "error" in box:
        raise RuntimeError(box["error"])
    if "result" not in scope:
        raise RuntimeError("the code did not set a variable called `result`")
    return {"result": scope["result"], "chart_kind": scope.get("chart_kind"), "chart_df": scope.get("chart_df")}


# ------------------------------------------------------------
# prompts
# ------------------------------------------------------------

SYSTEM = """You are a careful data analyst. You answer questions about a pandas DataFrame named `df` by writing Python.

RULES
- Output ONLY Python code. No explanations, no markdown fences.
- You may use only `df`, `pd` and `np`. Do not import anything. No files, no network.
- Use the exact column names given. Never invent columns.
- Assign the final answer to a variable named `result` (a scalar, a Series, or a DataFrame).
- Keep tables small: use .head(15) or nlargest for rankings. Round numbers to 2 decimals.
- Treat columns listed as 'code' as categories (count or group by them), never average them.
- If a chart would help, ALSO set chart_kind to one of 'bar', 'line', 'pie', 'scatter' and chart_df to a
  DataFrame whose first column holds the labels/x values and whose other columns are numeric.
- Write at most 15 lines."""


def schema_text(ds: Dataset, max_cols: int = 40) -> str:
    lines = []
    for c in list(ds.df.columns)[:max_cols]:
        s = ds.df[c]
        kind = ds.kinds.get(c, "?")
        kind_label = {"discrete": "code", "categorical": "category", "numeric": "number", "datetime": "date",
                      "id": "id", "year": "year", "text": "text"}.get(kind, kind)
        ex = ""
        if kind in ("categorical", "discrete") and s.nunique() <= 12:
            ex = " values: " + ", ".join(str(v) for v in s.dropna().unique()[:12])
        elif kind == "numeric":
            ex = f" range {human(s.min())} to {human(s.max())}"
        elif kind == "datetime":
            ex = f" from {s.min():%Y-%m-%d} to {s.max():%Y-%m-%d}"
        else:
            ex = " e.g. " + ", ".join(str(v) for v in s.dropna().astype(str).head(3))
        lines.append(f"- {c} [{kind_label}]{ex}")
    return "\n".join(lines)


def build_prompt(ds: Dataset, question: str, previous: str | None = None, error: str | None = None) -> str:
    extra = ""
    if ds.roles:
        extra = "Detected roles (may be wrong): " + ", ".join(f"{r}={c}" for r, c in ds.roles.items()) + "\n"
    p = (f"DataFrame `df` has {ds.rows} rows and these columns:\n{schema_text(ds)}\n{extra}\n"
         f"First rows:\n{ds.df.head(3).astype(str).to_string(max_colwidth=18)}\n\nQUESTION: {question}\n")
    if previous and error:
        p += f"\nYour previous code:\n{previous}\n\nIt failed with: {error}\nWrite corrected code."
    return p


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S | re.I)
    code = m.group(1) if m else text
    return code.strip("\n").strip()


# ------------------------------------------------------------
# result -> text
# ------------------------------------------------------------

def _fmt(v):
    if isinstance(v, (int, np.integer)):
        return f"{int(v):,}"
    if isinstance(v, (float, np.floating)):
        return "n/a" if v != v else human(v, 2) if abs(v) >= 1000 else f"{v:,.2f}".rstrip("0").rstrip(".")
    return str(v)


def render(result) -> tuple[str, pd.DataFrame | None]:
    """Returns (text, dataframe used for a possible chart)."""
    from reports import table

    if isinstance(result, pd.Series):
        df = result.head(20).reset_index()
        df.columns = [str(c) if str(c) else "value" for c in df.columns]
        result = df
    if isinstance(result, pd.DataFrame):
        df = result.head(20)
        if len(df.columns) > 8:
            df = df.iloc[:, :8]
        rows = [[_fmt(v) for v in r] for r in df.itertuples(index=False)]
        txt = table(rows, [str(c)[:18] for c in df.columns], "l" + "r" * (len(df.columns) - 1))
        if len(result) > 20:
            txt += f"\n(showing 20 of {len(result):,} rows)"
        return txt, df
    if isinstance(result, (list, tuple, set)):
        return ", ".join(_fmt(v) for v in list(result)[:30]), None
    return _fmt(result), None


# ------------------------------------------------------------
# public entry
# ------------------------------------------------------------

@dataclass
class AnalystResult:
    ok: bool
    text: str = ""
    code: str = ""
    chart_kind: str | None = None
    chart_df: pd.DataFrame | None = None
    error: str = ""
    attempts: int = 0
    table: pd.DataFrame | None = field(default=None, repr=False)


def ask_data(ds: Dataset, question: str, generate, max_tokens: int = 500) -> AnalystResult:
    """generate(system, user, max_tokens) -> str"""
    code, err = "", ""
    for attempt in (1, 2):
        try:
            raw = generate(SYSTEM, build_prompt(ds, question, code if attempt == 2 else None,
                                                err if attempt == 2 else None), max_tokens)
        except Exception as e:                        # model unavailable
            return AnalystResult(False, error=f"model unavailable: {e}", attempts=attempt)
        code = extract_code(raw or "")
        if not code:
            err = "no code was produced"
            continue
        try:
            out = run(code, ds.df)
        except Unsafe as e:
            err = str(e)
            continue
        except Exception as e:                        # noqa: BLE001
            err = str(e)
            continue

        text, tbl = render(out["result"])
        kind = out["chart_kind"] if out["chart_kind"] in ("bar", "line", "pie", "scatter") else None
        cdf = out["chart_df"] if isinstance(out["chart_df"], pd.DataFrame) else (tbl if kind else None)
        if len(text) > 6000:
            text = text[:6000] + "\n(output truncated)"
        return AnalystResult(True, text=text, code=code, chart_kind=kind, chart_df=cdf, attempts=attempt, table=tbl)
    return AnalystResult(False, code=code, error=err, attempts=2)
