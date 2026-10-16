"""
Data-analysis half of JARVIS (dataset loading, intent routing, roles, reports, charts).
This code is unchanged from the tested version; it now lives in its own mixin so main.py can focus on the brain.
"""

from __future__ import annotations

import os
import re
import traceback
from pathlib import Path

import analytics as A
import advisor
import analyst
import charts as C
import config
import drivers as D
import exporter
import reports as R
from insights import build_insights, top_actions
from models import Reply, Session, _file_payload
from query import quick_query


class DataAssistant:
    """Mixin: needs self.generate(), self.llm_ready from the LLM mixin."""

    # ========================================================
    # DATASET LOADING
    # ========================================================

    def load_bytes(self, session: Session, name: str, data: bytes) -> Reply:
        """Called by the server when the browser uploads a file."""
        safe = re.sub(r"[^\w.\- ]", "_", Path(name).name) or "dataset"
        try:
            (config.UPLOAD_DIR / safe).write_bytes(data)
        except Exception:
            pass

        df = A.load_dataframe(data, safe)
        ds = A.Dataset(df, safe, str(config.UPLOAD_DIR / safe))
        return self._adopt_dataset(session, ds)

    def load_path(self, session: Session, path: Path) -> Reply:
        df = A.load_dataframe(path, path.name)
        ds = A.Dataset(df, path.name, str(path))
        return self._adopt_dataset(session, ds)

    def _adopt_dataset(self, session: Session, ds: A.Dataset) -> Reply:
        session.dataset = ds
        session.original = ds
        session.history = []  # fresh context for the new dataset
        p = A.profile(ds)
        charts = [c for c in [C._safe(C.kpi_scorecard, ds)] if c]
        speech = (f"Dataset loaded. {p['rows']:,} rows and {p['columns']} columns. "
                  f"Data quality score is {p['quality_score']} out of 100.")
        return Reply(R.overview(ds), speech=speech, charts=charts, dataset=ds.ui_summary(),
                     suggestions=self._suggest(ds, None))

    def _resolve_filename(self, phrase: str) -> Path | None:
        """'analyse my sales.csv' -> try the whole phrase, then drop leading words until a file matches."""
        words = phrase.split()
        for i in range(len(words)):
            path = self.find_file(" ".join(words[i:]))
            if path:
                return path
        return None

    def find_file(self, name: str) -> Path | None:
        name = name.strip().strip("\"'")
        direct = [Path(name).expanduser(), Path.cwd() / name, config.UPLOAD_DIR / name]
        for p in direct:
            if p.is_file():
                return p
        roots = [config.UPLOAD_DIR, Path.cwd()] + [Path.home() / d for d in ("Downloads", "Desktop", "Documents")]
        target = Path(name).name.lower()
        for root in roots:
            if not root.exists():
                continue
            base_depth = len(root.parts)
            for dirpath, dirnames, filenames in os.walk(root):
                if len(Path(dirpath).parts) - base_depth >= 3:
                    dirnames[:] = []
                dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                for f in filenames:
                    if f.lower() == target:
                        return Path(dirpath) / f
        return None

    # ========================================================
    # DATA INTENTS
    # ========================================================

    _FILE_RE = re.compile(r"([\w\-. /\\~()]+?\.(?:csv|tsv|xlsx|xls|json|parquet))", re.I)
    _STRONG_DATA = re.compile(
        r"\b(dataset|data set|csv|excel|spreadsheet|xlsx|my data|the data|business analysis|kpis?|"
        r"clean (the |my )?data|forecast (my|the|our)|visuali[sz]e (my|the) data)\b", re.I)

    def _intent(self, low: str, ds: A.Dataset) -> str | None:
        def has(*w):
            return any(x in low for x in w)

        dataish = has("data", "dataset", "business", "sales", "revenue", "profit", "company", "store",
                      "customer", "margin", "growth", "kpi", "product", "region", "orders") or bool(ds.columns_in_text(low))

        cols_in = ds.columns_in_text(low)
        targets = D.detect_targets(ds)
        if (re.search(r"\b(what|which|why|how)\b.*\b(drive|drives|driving|driver|drivers|affect|affects|influence|"
                      r"influences|impact|impacts|cause|causes|explain|explains|predict|predicts|behind|lead to|leads to)\b", low)
                or has("key factors", "risk factors", "driver analysis", "drivers of", "factors behind",
                       "factors that", "what matters for", "what matters most")
                or (targets and set(cols_in) & set(targets)
                    and re.search(r"\b(analy[sz]e|analysis|why|rate|factors?|risk|reasons?|behind|affect\w*)\b", low))):
            return "drivers"
        if re.search(r"\b(use original|revert|undo clean|original data)\b", low):
            return "revert"
        if re.search(r"\b(clean|impute|preprocess|pre-process|fix missing|handle missing|fill missing)\b", low):
            return "clean"
        if re.search(r"\b(export|download|generate|create|make|send|build)\b.*\breport\b", low) or has("full report", "html report"):
            return "export"
        if has("forecast", "predict", "projection", "next month", "next quarter", "next year",
               "next week", "future sales", "future revenue"):
            return "forecast"
        if has("segment", "rfm", "churn", "retention", "customer group"):
            return "segments"
        if has("correlat"):
            return "correlation"
        if has("outlier", "anomal"):
            return "outliers"
        if re.search(r"\b(chart|plot|graph|visuali[sz]\w*|dashboard|histogram|scatter|pareto|heatmap|heat map|pie|donut|diagram)\b", low) \
                or re.search(r"\b(show|draw)\b.*\b(trend|distribution|breakdown|split|share)\b", low):
            return "charts"
        if dataish and re.search(r"\b(advice|advise|recommend\w*|strateg\w*|suggest\w*|improve\w*|what should i|how (can|do|could) (i|we)|opportunit\w*|risks?)\b", low):
            return "advice"
        if has("executive summary", "exec summary", "key takeaways", "tldr", "tl;dr"):
            return "executive"
        if re.search(r"\b(eda|exploratory)\b", low):
            return "eda"
        if has("insight"):
            return "insights"
        if has("business analysis", "full analysis", "complete analysis", "analyse", "analyze", "analysis"):
            return "analysis"
        if has("summary", "summarise", "summarize", "overview", "profile", "describe the dataset",
               "about the dataset", "what's in", "whats in", "tell me about the data"):
            return "overview"
        if has("columns", "schema", "fields", "column names"):
            return "schema"
        return None

    _ROLE_WORDS = {
        "revenue": "revenue", "sales": "revenue", "profit": "profit", "cost": "cost", "date": "date",
        "customer": "customer", "product": "product", "category": "category", "region": "region",
        "quantity": "quantity", "discount": "discount", "order": "order", "price": "price",
    }
    _ROLE_PATTERN = "|".join(["main metric", "primary metric", "metric"] + list(_ROLE_WORDS))

    def _handle_roles(self, ds: A.Dataset, text: str) -> Reply | None:
        """Let the user correct how their columns are read."""
        t = text.strip().rstrip(".!")
        low = t.lower()

        if re.search(r"\b(show|list|what are)\b.*\b(roles?)\b|how did you read my columns|which columns did you (detect|use)", low):
            return Reply(R.roles_report(ds))

        if re.match(r"^(clear|reset|remove|ignore|forget)\s+(all\s+)?(the\s+)?(column\s+)?roles?$", low) or low in ("no roles", "ignore roles"):
            ds.clear_roles()
            return Reply("Cleared. Every column now keeps its own name and I won't assume what any of them mean.\n"
                         "Set the ones you want with, for example: 'use Amount as revenue' or 'use income as main metric'.",
                         dataset=ds.ui_summary())

        role_word = col_phrase = None
        m = re.match(rf"^(?:use|set|treat|make|take)\s+(.+?)\s+(?:as|for)\s+(?:the\s+|my\s+)?({self._ROLE_PATTERN})(?:\s+column)?$", t, re.I)
        if m:
            col_phrase, role_word = m.group(1), m.group(2).lower()
        else:
            m = re.match(rf"^(?:the\s+)?({self._ROLE_PATTERN})(?:\s+column)?\s*(?:is|=|:)\s*(.+)$", t, re.I)
            if m:
                role_word, col_phrase = m.group(1).lower(), m.group(2)
        if not m:
            return None

        col = ds.find_column(col_phrase.strip().strip("\"'"))
        if col is None:
            cols = ", ".join(list(ds.df.columns)[:15])
            return Reply(f"I couldn't find a column matching '{col_phrase}'. Columns: {cols}")

        if role_word in ("main metric", "primary metric", "metric"):
            ok, msg = ds.set_main_metric(col)
        else:
            ok, msg = ds.set_role(self._ROLE_WORDS[role_word], col)
        if not ok:
            return Reply(msg)
        return Reply(msg + "\n\nRun 'business analysis' again to see results based on this.\n\n" + R.roles_report(ds),
                     speech=msg, dataset=ds.ui_summary())

    def handle_data(self, session: Session, text: str) -> Reply | None:
        low = text.lower().strip()
        loaded_now: Reply | None = None

        # ---- a file named in the message ----
        m = self._FILE_RE.search(text)
        if m:
            fname = m.group(1).strip()
            path = self._resolve_filename(fname)
            if path is None:
                return Reply(f"I couldn't find '{fname}'. Upload it with the button on the left, "
                             "or put it in Downloads, Desktop, Documents or the jarvis_data/uploads folder.")
            try:
                loaded_now = self.load_path(session, path)
            except Exception as e:
                return Reply(f"I found the file but couldn't read it: {e}")

        ds = session.dataset

        if ds is None:
            if self._STRONG_DATA.search(text):
                return Reply("No dataset is loaded yet. Click UPLOAD DATASET on the left "
                             "(CSV, Excel, JSON or Parquet), or tell me a filename, for example: analyse sales.csv")
            return None

        role_reply = self._handle_roles(ds, text)
        if role_reply:
            return role_reply

        intent = self._intent(low, ds)

        if loaded_now and intent is None:
            return loaded_now
        if loaded_now and intent:
            # loaded and asked for something: carry out the request, keep the dataset summary
            result = self._run_intent(session, ds, intent, text)
            result.dataset = ds.ui_summary()
            return result

        if intent:
            return self._run_intent(session, ds, intent, text)

        # ---- describe a single column ----
        m = re.match(r"^(?:describe|tell me about|explain|stats (?:for|on|of)|statistics (?:for|on|of))\s+(?:the\s+)?(?:column\s+)?(.+)$", low)
        if m:
            col = ds.find_column(m.group(1))
            if col:
                return Reply(R.describe_column(ds, col))

        # ---- the question is about something this dataset does not contain ----
        if re.search(r"\b(how many|total|sum|average|avg|mean|top|best|worst|highest|lowest|count|per|by|compare)\b", low):
            concepts = {"revenue": "revenue", "sales": "revenue", "profit": "profit", "customer": "customer",
                        "customers": "customer", "order": "order", "orders": "order",
                        "product": "product", "products": "product"}
            for word, role in concepts.items():
                if re.search(rf"\b{word}\b", low) and role not in ds.roles and ds.find_column(word) is None:
                    cols = ", ".join(list(ds.df.columns)[:12])
                    return Reply(f"This dataset has no column I can read as {role}, so I can't answer that "
                                 f"without guessing. Columns: {cols}.\n"
                                 f"If one of them is the {role}, tell me, for example: 'use <column> as {role}'.")

        # ---- direct computed answer ----
        try:
            answer = quick_query(ds, text)
        except Exception:
            traceback.print_exc()
            answer = None
        if answer:
            return Reply(answer, speech=answer if "\n" not in answer else "Here are the figures. They are on screen.")

        # ---- free-form question about the data: LLM grounded in computed facts ----
        dataish = bool(ds.columns_in_text(text)) or any(
            w in low for w in ("data", "dataset", "sales", "revenue", "profit", "margin", "customer",
                               "product", "region", "trend", "why", "column", "row"))
        if dataish and self.llm_ready:
            r = self._analyst_reply(ds, text)
            if r:
                return r
        if dataish and self.llm_ready:
            try:
                out = self.generate(advisor.ADVISOR_SYSTEM, advisor.qa_prompt(ds, text), config.CHAT_MAX_TOKENS + 120)
                if out:
                    return Reply(out, speech=out[:300])
            except Exception:
                traceback.print_exc()
        return None

    # ---------------------------------------------------------

    # ---------------------------------------------------------
    # helpers for the newer features
    # ---------------------------------------------------------

    def _metric_in_text(self, ds: A.Dataset, text: str):
        for c in ds.columns_in_text(text):
            if ds.is_metric(c):
                return c
        return None

    def _resolve_target(self, ds: A.Dataset, text: str):
        cols = ds.columns_in_text(text)
        targets = D.detect_targets(ds)
        for c in cols:
            if c in targets:
                return c
        for c in cols:
            if ds.kinds.get(c) in ("numeric", "categorical", "discrete"):
                return c
        return targets[0] if len(targets) == 1 else None

    def _suggest(self, ds: A.Dataset, done: str | None = None) -> list:
        """Follow-up chips: only things this dataset can actually do."""
        pool = []
        targets = D.detect_targets(ds)
        if targets and done != "drivers":
            pool.append(f"What drives {targets[0]}?")
        if done not in ("analysis", "eda", "overview"):
            pool.append("Business analysis" if ds.primary_metric else "Analyse this dataset")
        if done != "charts":
            pool.append("Show the dashboard")
        if ds.date_col and (ds.primary_metric or ds.num_cols) and done != "forecast":
            pool.append(f"Forecast {ds.primary_metric or ds.num_cols[0]}")
        if "customer" in ds.roles and "revenue" in ds.roles and ds.date_col and done != "segments":
            pool.append("Show customer segments")
        if done != "advice":
            pool.append("Give me business advice")
        if done != "outliers":
            pool.append("Find outliers")
        if done != "correlation":
            pool.append("Show correlations")
        p = A.profile(ds)
        if (p["missing_total"] or p["duplicates"]) and done != "clean":
            pool.append("Clean the data")
        if done != "export":
            pool.append("Export a report")
        return pool[:4]

    def _analyst_reply(self, ds: A.Dataset, question: str) -> Reply | None:
        """Let the model write pandas code, run it in the sandbox and show the result and the method."""
        try:
            res = analyst.ask_data(ds, question, self.generate)
        except Exception:
            traceback.print_exc()
            return None
        if not res.ok:
            print(f"[analyst] gave up: {res.error}")
            return None
        lines = [R._head("ANSWER"), res.text, "", "How I worked it out (pandas, run on your data):"]
        lines += ["  " + l for l in res.code.splitlines()]
        charts = []
        if res.chart_kind and res.chart_df is not None:
            ch = C._safe(C.custom_chart, res.chart_kind, res.chart_df, question[:70])
            if ch:
                charts.append(ch)
        one_line = "\n" not in res.text and len(res.text) < 120
        return Reply("\n".join(lines), charts=charts,
                     speech=f"The answer is {res.text}." if one_line else "I've worked that out. The result is on screen.")

    def _run_intent(self, session: Session, ds: A.Dataset, intent: str, text: str) -> Reply:
        r = self._run_intent_core(session, ds, intent, text)
        if not r.suggestions:
            r.suggestions = self._suggest(ds, intent)
        return r

    def _run_intent_core(self, session: Session, ds: A.Dataset, intent: str, text: str) -> Reply:
        if intent == "drivers":
            target = self._resolve_target(ds, text)
            if not target:
                cands = D.detect_targets(ds) or [c for c in ds.df.columns if ds.kinds.get(c) in ("categorical", "discrete")][:6]
                return Reply("Which column is the outcome you want to explain? For example: 'what drives "
                             f"{cands[0] if cands else 'Churn'}'. " + (f"Candidates: {', '.join(cands)}." if cands else ""))
            charts = [c for c in (C._safe(C.drivers_chart, ds, target), C._safe(C.target_rate_chart, ds, target)) if c]
            res = D.drivers(ds, target)
            top = res["rows"][0] if res and res.get("rows") else None
            speech = (f"{top['column']} shows the strongest link with {target}. The ranking is on screen." if top
                      else f"I couldn't find a reliable link with {target}.")
            return Reply(D.report(ds, target), charts=charts, speech=speech)

        if intent == "revert":
            if session.original is None or session.original is ds:
                return Reply("You are already working with the original dataset.")
            session.dataset = session.original
            return Reply("Switched back to the original dataset.", dataset=session.dataset.ui_summary())

        if intent == "clean":
            low = text.lower()
            cap = any(w in low for w in ("cap outlier", "winsor", "treat outlier", "remove outlier", "clip outlier"))
            strategy = "mean" if "mean" in low else "median"
            cleaned, report = A.clean_dataset(ds, strategy=strategy, cap_outliers=cap)
            path = A.save_cleaned(ds, cleaned)
            new_ds = A.Dataset(cleaned, path.name, str(path))
            session.dataset = new_ds
            text_out = R.cleaning_report(report, path.name)
            text_out += "\n\nThe cleaned version is now my working dataset. Say 'use original data' to switch back."
            files = [f for f in [_file_payload(path, "text/csv")] if f]
            before, after = A.profile(ds)["quality_score"], A.profile(new_ds)["quality_score"]
            text_out += f"\nData quality score: {before} → {after}"
            return Reply(text_out,
                         speech=f"Cleaning complete. Quality score improved from {before} to {after}.",
                         files=files, dataset=new_ds.ui_summary())

        if intent == "export":
            charts = C.auto_charts(ds, limit=8)
            advice = advisor.strategic_advice(ds, self.generate) if self.llm_ready else advisor.rule_based_advice(ds)
            html = exporter.build_html_report(ds, charts, advice)
            path = exporter.save_html_report(ds, html)
            files = [f for f in [_file_payload(path, "text/html")] if f]
            return Reply(f"Report generated: {path.name}\nIt includes key metrics, insights, priority actions and "
                         f"{len(charts)} charts, all in one file you can open or share.",
                         speech="Your report is ready to download.", files=files, charts=charts[:1])

        if intent == "forecast":
            metric = self._metric_in_text(ds, text)
            charts = [c for c in [C._safe(C.trend, ds, metric)] if c] if (metric or ds.primary_metric) else []
            return Reply(R.forecast_report(ds, metric),
                         speech="Forecast complete. The projection and its range are on screen." if charts
                         else "I need to know which column to forecast.",
                         charts=charts)

        if intent == "segments":
            charts = [c for c in [C._safe(C.rfm_chart, ds)] if c]
            return Reply(R.segments_report(ds), speech="Customer segmentation complete.", charts=charts)

        if intent == "correlation":
            return Reply(R.correlation_report(ds), speech="Here are the strongest relationships in your data.",
                         charts=C.charts_for_request(ds, "correlation heatmap"))

        if intent == "outliers":
            charts = [c for c in [C._safe(C.outlier_box, ds)] if c]
            return Reply(R.outlier_report(ds), speech="Outlier check complete.", charts=charts)

        if intent == "charts":
            charts = C.charts_for_request(ds, text)
            if not charts:
                return Reply("I couldn't find anything suitable to plot for that request. "
                             "Try naming columns, for example: plot sales by region, or histogram of profit.")
            lines = ["Here is what I plotted:"] + [f"• {c['title']}" + (f": {c['caption']}" if c.get("caption") else "")
                                                   for c in charts]
            return Reply("\n".join(lines), speech=f"I've drawn {len(charts)} chart{'s' if len(charts) != 1 else ''}.",
                         charts=charts)

        if intent == "advice":
            low = text.lower()
            generic = len(low.split()) <= 7 or any(w in low for w in ("business advice", "strategic", "strategy for", "briefing"))
            advice = advisor.strategic_advice(ds, self.generate, None if generic else text)
            return Reply(advice, speech="My briefing is on screen. The recommendations are in priority order.")

        if intent == "executive":
            return Reply(self._executive_summary(ds), speech=R.speech_analysis(ds))

        if intent == "eda":
            charts = [c for c in (C._safe(C.missing, ds), C._safe(C.distributions, ds),
                                  C._safe(C.correlation_heatmap, ds), C._safe(C.outlier_box, ds)) if c]
            body = "\n\n".join([R.overview(ds), R.outlier_report(ds), R.correlation_report(ds)])
            return Reply(body, speech="Exploratory analysis complete.", charts=charts)

        if intent == "insights":
            return Reply(R.insights_report(ds), speech=R.speech_analysis(ds))

        if intent == "analysis":
            if ds.primary_metric:
                charts = [c for c in (C._safe(C.kpi_scorecard, ds), C._safe(C.trend, ds),
                                      C._safe(C.top_bar, ds), C._safe(C.margin_by_group, ds)) if c]
            else:
                charts = C.auto_charts(ds, limit=5)
            return Reply(R.business_analysis(ds), speech=R.speech_analysis(ds), charts=charts)

        if intent == "overview":
            return Reply(R.overview(ds) + "\n\n" + R.schema(ds),
                         speech=f"{ds.name} has {ds.rows:,} rows and {ds.df.shape[1]} columns.")

        if intent == "schema":
            return Reply(R.schema(ds))

        return Reply("I'm not sure what to do with that.")

    def _executive_summary(self, ds: A.Dataset) -> str:
        res = A.full_analysis(ds)
        ins = build_insights(ds)
        lines = [R._head(f"SUMMARY: {ds.name}")]
        lines.append("  |  ".join(f"{k['label']}: {k['display']}" for k in res["kpis"][:4]))
        t = res.get("time")
        if t:
            st = t["stats"]
            lines.append(f"{t['metric']} shows a {st['direction']} trend"
                         + (f" ({st['yoy_pct']:+.1f}% year over year)" if st["yoy_pct"] is not None else "") + ".")
        lines.append("")
        lines.append("Main findings")
        for i in ins[:4]:
            lines.append(f"{R.ICON[i['severity']]} {i['title']}")
            lines.append(f"   Observed:  {i['detail']}")
            if i.get("meaning"):
                lines.append(f"   May mean:  {i['meaning']}")
        actions = top_actions(ins, 3)
        lines.append("")
        lines.append("Next steps (only where the data supports them)")
        lines += [f"{n}. {a}" for n, a in enumerate(actions, 1)] or [
            "The data does not support a specific action beyond the checks above."]
        return "\n".join(lines)
