"""Power BI workspace view for Bufab BI Governance Studio.

Wraps the headless Power BI pipeline (auth / powerbi_client / activity_events /
raw_export / analytics) in a GUI: Collect a UTC date range of activity events,
export the raw event log, and build a usage view.

The usage view follows report-usage-reporting practice: build once, then slice by
workspace, report and time window. It shows adoption KPIs, always-readable ranked
bars (the dimension name is on every row), a views-per-day trend, and detail
tables for reports / users / workspaces with a stale (days-since-last-view) flag.

Credentials come from the shell's unified Settings (never persisted); we build a
config.Settings directly from them so no .env file is needed for the GUI.
"""
from __future__ import annotations

import json
import datetime
import threading
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QDate
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox,
    QTabWidget, QDateEdit, QScrollArea, QMessageBox, QPlainTextEdit,
)

from config import Settings
from widgets import (
    TEAL, WARN, GOOD, BAD, make_card, label, kpi_row, line_chart, ranked_bars,
    colored_table, clear_layout,
)

TINT_WARN = "#F7ECD2"     # not viewed in 30+ days
TINT_BAD = "#F7D9DE"      # not viewed in 90+ days


class PowerBIView(QWidget):
    sig_done = Signal(str)
    sig_error = Signal(str, str)
    sig_usage_data = Signal(object)      # list of view records
    sig_lineage_done = Signal(str)

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._records = None             # list of dicts at (ws, report, user, day) grain
        self._building_filters = False
        self.sig_done.connect(self._on_done)
        self.sig_error.connect(lambda t, m: QMessageBox.critical(self, t, m))
        self.sig_usage_data.connect(self._on_usage_data)
        self.sig_lineage_done.connect(self._on_lineage_text)
        self._build()

    def log(self, msg):
        self.shell.log(msg)

    def _data_dir(self) -> Path | None:
        if not self.shell.output_dir_powerbi:
            QMessageBox.warning(self, "Missing settings",
                                "Set a Power BI output folder in Settings - Power BI data lands under it.")
            return None
        return Path(self.shell.output_dir_powerbi) / "powerbi_data"

    def _pbi_settings(self, data_dir: Path) -> Settings:
        """Build a config.Settings from the shell's Power BI settings (raises
        ValueError with a clear message if the chosen auth mode is incomplete)."""
        p = self.shell.pbi
        mode = p.get("auth_mode", "Client secret (in-memory)")
        tenant_id = (p.get("tenant_id") or "").strip() or None
        client_id = (p.get("client_id") or "").strip() or None
        kv_url = (p.get("key_vault_url") or "").strip() or None
        kv_secret = (p.get("key_vault_secret_name") or "").strip() or None

        if mode == "Managed identity":
            auth_mode = "managed_identity"
        elif mode == "Key Vault":
            if not (kv_url and kv_secret):
                raise ValueError("Key Vault mode needs both a Key Vault URL and secret name in Settings.")
            if not (tenant_id and client_id):
                raise ValueError("Key Vault mode needs the Power BI tenant ID and client ID in Settings.")
            auth_mode = "key_vault"
        else:  # Client secret (in-memory)
            if not (tenant_id and client_id):
                raise ValueError("Client-secret mode needs the tenant ID and client ID in Settings.")
            if not (self.shell.pbi_secret or "").strip():
                raise ValueError("Enter the client secret in Settings (held in memory only).")
            auth_mode = "env_secret"

        return Settings(
            tenant_id=tenant_id, client_id=client_id, auth_mode=auth_mode,
            key_vault_url=kv_url, key_vault_secret_name=kv_secret,
            client_secret=(self.shell.pbi_secret or None),
            output_dir=data_dir,
        )

    # ---------------- layout ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        tabs = QTabWidget()

        # Collect
        tab_c = QWidget()
        cl = QVBoxLayout(tab_c)
        cl.setContentsMargins(0, 10, 0, 0)
        cc = make_card()
        ccl = QVBoxLayout(cc)
        ccl.addWidget(label("COLLECT  (activity events by UTC day)", "section"))
        ccl.addWidget(label("Activity events retain only ~28 days, so collect regularly and let them "
                            "accumulate into the dataset. Pull a single day or a whole range — "
                            "already-collected days are skipped. Needs a service principal in the "
                            "Power BI admin group. To run this unattended every day, see 'Run it daily' "
                            "in HOW_TO_RUN.md (collect_daily.bat + Windows Task Scheduler).",
                            "muted", wrap=True))
        drow = QHBoxLayout()
        drow.addWidget(label("From (UTC)", "muted"))
        self.date_from = QDateEdit()
        self.date_from.setCalendarPopup(True)
        self.date_from.setDisplayFormat("yyyy-MM-dd")
        self.date_from.setMinimumWidth(140)
        self.date_from.setDate(QDate.currentDate().addDays(-7))
        drow.addWidget(self.date_from)
        drow.addSpacing(14)
        drow.addWidget(label("To (UTC)", "muted"))
        self.date_to = QDateEdit()
        self.date_to.setCalendarPopup(True)
        self.date_to.setDisplayFormat("yyyy-MM-dd")
        self.date_to.setMinimumWidth(140)
        self.date_to.setDate(QDate.currentDate().addDays(-1))
        drow.addWidget(self.date_to)
        drow.addStretch(1)
        ccl.addLayout(drow)
        self.chk_skip_existing = QCheckBox("Skip days already collected (only pull missing days)")
        self.chk_skip_existing.setChecked(True)
        ccl.addWidget(self.chk_skip_existing)
        cl.addWidget(cc)
        crow = QHBoxLayout()
        self.btn_collect = QPushButton("Collect range")
        self.btn_collect.setObjectName("accent")
        self.btn_collect.clicked.connect(self._on_collect)
        crow.addWidget(self.btn_collect)
        self.btn_catchup = QPushButton("Catch up (last 28 days)")
        self.btn_catchup.setObjectName("ghost")
        self.btn_catchup.clicked.connect(self._on_catchup)
        crow.addWidget(self.btn_catchup)
        crow.addStretch(1)
        cl.addLayout(crow)
        cl.addStretch(1)
        tabs.addTab(tab_c, "Collect")

        # Raw export
        tab_r = QWidget()
        rl = QVBoxLayout(tab_r)
        rl.setContentsMargins(0, 10, 0, 0)
        rc = make_card()
        rcl = QVBoxLayout(rc)
        rcl.addWidget(label("RAW EXPORT  (full event log, no aggregation)", "section"))
        rcl.addWidget(label("Flattens every collected event into a lossless table plus a key map "
                            "(which columns join to which dimension).", "muted", wrap=True))
        orow = QHBoxLayout()
        self.chk_parquet = QCheckBox("Parquet (typed, lossless)")
        self.chk_parquet.setChecked(True)
        self.chk_csv = QCheckBox("CSV (Excel-friendly)")
        self.chk_csv.setChecked(True)
        orow.addWidget(self.chk_parquet)
        orow.addWidget(self.chk_csv)
        orow.addStretch(1)
        rcl.addLayout(orow)
        rl.addWidget(rc)
        rrow = QHBoxLayout()
        self.btn_raw = QPushButton("Export raw events")
        self.btn_raw.setObjectName("accent")
        self.btn_raw.clicked.connect(self._on_raw)
        rrow.addWidget(self.btn_raw)
        rrow.addStretch(1)
        rl.addLayout(rrow)
        rl.addStretch(1)
        tabs.addTab(tab_r, "Raw export")

        # Usage analytics + dashboard
        tab_a = QWidget()
        al = QVBoxLayout(tab_a)
        al.setContentsMargins(0, 10, 0, 0)
        ac = make_card()
        acl = QVBoxLayout(ac)
        acl.addWidget(label("USAGE ANALYTICS", "section"))
        acl.addWidget(label("Exact recorded views from the collected activity events. Build once, then "
                            "slice by workspace, report and time window. (Time-per-visit and per-page "
                            "usage are not available from the Admin APIs.)", "muted", wrap=True))
        brow = QHBoxLayout()
        self.btn_analytics = QPushButton("Build usage analytics")
        self.btn_analytics.setObjectName("accent")
        self.btn_analytics.clicked.connect(self._on_analytics)
        brow.addWidget(self.btn_analytics)
        brow.addStretch(1)
        acl.addLayout(brow)

        frow = QHBoxLayout()
        frow.addWidget(label("Workspace", "muted"))
        self.cmb_ws = QComboBox()
        self.cmb_ws.setMinimumWidth(220)
        self.cmb_ws.setEnabled(False)
        self.cmb_ws.currentIndexChanged.connect(self._on_ws_changed)
        frow.addWidget(self.cmb_ws)
        frow.addSpacing(10)
        frow.addWidget(label("Report", "muted"))
        self.cmb_report = QComboBox()
        self.cmb_report.setMinimumWidth(280)
        self.cmb_report.setEnabled(False)
        self.cmb_report.currentIndexChanged.connect(lambda _i: self._render_usage())
        frow.addWidget(self.cmb_report, 1)
        frow.addSpacing(10)
        frow.addWidget(label("Window", "muted"))
        self.cmb_window = QComboBox()
        self.cmb_window.addItems(["All time", "Last 7 days", "Last 30 days", "Last 90 days"])
        self.cmb_window.setEnabled(False)
        self.cmb_window.currentIndexChanged.connect(lambda _i: self._render_usage())
        frow.addWidget(self.cmb_window)
        acl.addLayout(frow)
        al.addWidget(ac)

        a_scroll = QScrollArea()
        a_scroll.setWidgetResizable(True)
        holder = QWidget()
        self.usage_dash = QVBoxLayout(holder)
        self.usage_dash.setContentsMargins(0, 8, 0, 0)
        self.usage_dash.setSpacing(10)
        self.usage_dash.addWidget(label("Build analytics to see the usage dashboard here, then filter "
                                        "by workspace / report / window.", "muted"))
        self.usage_dash.addStretch(1)
        a_scroll.setWidget(holder)
        al.addWidget(a_scroll, 1)
        tabs.addTab(tab_a, "Usage analytics")

        # Model lineage
        tab_m = QWidget()
        ml = QVBoxLayout(tab_m)
        ml.setContentsMargins(0, 10, 0, 0)
        mc = make_card()
        mcl = QVBoxLayout(mc)
        mcl.addWidget(label("MODEL LINEAGE  (semantic model table -> warehouse source, direct or "
                            "through a Gen1 dataflow)", "section"))
        mcl.addWidget(label("Tenant-wide scan via the Admin Scanner API - no workspace selection "
                            "needed. Needs the tenant setting 'Enhance admin APIs responses with DAX "
                            "and mashup expressions' enabled, or every table comes back as "
                            "no_expression_available (see the Excel report's warning sheet). Writes "
                            "one combined workbook (model_lineage_*.xlsx) listing, for each table, "
                            "every source table/view it reads from - not just its primary one: a "
                            "second table combined in to enrich/fix data (whether the same connector "
                            "used twice, or a merge/join onto another query in the same dataset or "
                            "dataflow) is also resolved and tagged with which query brought it in - "
                            "which fields where the M code makes it explicit, and - for every model "
                            "column - whether it's referenced by a measure or calculated column's DAX "
                            "expression anywhere in the dataset (the closest proxy to 'used in a "
                            "report' available without Power BI exposing visual/page content via API "
                            "- a raw column placed directly on a visual with no calculation involved "
                            "can't be detected this way). Also writes a Sources sheet: the reverse "
                            "view, for each resolved source, how many tables across the tenant "
                            "actually pull from it.", "muted", wrap=True))
        mrow = QHBoxLayout()
        self.btn_lineage = QPushButton("Scan model lineage")
        self.btn_lineage.setObjectName("accent")
        self.btn_lineage.clicked.connect(self._on_lineage)
        mrow.addWidget(self.btn_lineage)
        mrow.addStretch(1)
        mcl.addLayout(mrow)
        ml.addWidget(mc)
        self.lineage_panel = QPlainTextEdit()
        self.lineage_panel.setReadOnly(True)
        self.lineage_panel.setMinimumHeight(150)
        ml.addWidget(self.lineage_panel, 1)
        tabs.addTab(tab_m, "Model lineage")

        root.addWidget(tabs, 1)

    # ================= collect =================
    def _on_catchup(self):
        self.date_from.setDate(QDate.currentDate().addDays(-28))
        self.date_to.setDate(QDate.currentDate().addDays(-1))
        self._on_collect()

    def _on_collect(self):
        data_dir = self._data_dir()
        if not data_dir:
            return
        try:
            settings = self._pbi_settings(data_dir)
        except ValueError as e:
            QMessageBox.warning(self, "Power BI settings", str(e))
            return
        qf, qt = self.date_from.date(), self.date_to.date()
        d_from = datetime.date(qf.year(), qf.month(), qf.day())
        d_to = datetime.date(qt.year(), qt.month(), qt.day())
        if d_from > d_to:
            QMessageBox.warning(self, "Date range", "'From' is after 'To' - pick a valid range.")
            return
        ndays = (d_to - d_from).days + 1
        self.btn_collect.setEnabled(False)
        self.btn_catchup.setEnabled(False)
        self.shell.busy_begin("Collecting Power BI activity")
        self.log(f"Collecting Power BI activity events {d_from.isoformat()} .. {d_to.isoformat()} "
                 f"({ndays} day(s), UTC) ...")
        threading.Thread(target=self._collect_worker,
                         args=(settings, data_dir, d_from, d_to, self.chk_skip_existing.isChecked()),
                         daemon=True).start()

    def _collect_worker(self, settings, data_dir, d_from, d_to, skip_existing):
        try:
            from auth import PowerBITokenProvider
            from powerbi_client import PowerBIAdminClient
            from activity_events import fetch_activity_events

            tokens = PowerBITokenProvider(settings)
            client = PowerBIAdminClient(tokens)
            out_dir = data_dir / "activity_events"
            out_dir.mkdir(parents=True, exist_ok=True)

            day = d_from
            total_events = days_pulled = days_skipped = 0
            cancelled = False
            while day <= d_to:
                if self.shell.cancel_requested():
                    cancelled = True
                    break
                out_file = out_dir / f"activity_events_{day.isoformat()}.jsonl"
                if skip_existing and out_file.exists() and out_file.stat().st_size > 0:
                    days_skipped += 1
                    self.log(f"  {day.isoformat()}: already collected - skipped.")
                    day += datetime.timedelta(days=1)
                    continue
                count = 0
                with out_file.open("w", encoding="utf-8") as fh:
                    for event in fetch_activity_events(client, day):
                        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
                        count += 1
                total_events += count
                days_pulled += 1
                self.log(f"  {day.isoformat()}: {count} events -> {out_file.name}")
                day += datetime.timedelta(days=1)

            tail = "CANCELLED" if cancelled else "done"
            self.log(f"Collect {tail}: {days_pulled} day(s) pulled ({total_events} events), "
                     f"{days_skipped} already present.")
        except Exception as e:
            self.log(f"ERROR collecting: {e}")
            self.sig_error.emit("Collect failed",
                                f"{e}\n\nCheck the Power BI tenant/client IDs and credential in Settings, "
                                "and that the service principal is in the Power BI admin group.")
        finally:
            self.sig_done.emit("collect")

    # ================= raw export =================
    def _on_raw(self):
        data_dir = self._data_dir()
        if not data_dir:
            return
        if not (self.chk_parquet.isChecked() or self.chk_csv.isChecked()):
            QMessageBox.warning(self, "Nothing selected", "Tick Parquet and/or CSV.")
            return
        self.btn_raw.setEnabled(False)
        self.shell.busy_begin("Exporting raw events")
        self.log("Exporting raw activity events ...")
        threading.Thread(target=self._raw_worker,
                         args=(data_dir, self.chk_parquet.isChecked(), self.chk_csv.isChecked()),
                         daemon=True).start()

    def _raw_worker(self, data_dir, want_parquet, want_csv):
        try:
            import raw_export
            raw_export.export(data_dir, want_parquet=want_parquet, want_csv=want_csv)
            self.log(f"Raw export written under {(data_dir / 'raw')}")
        except SystemExit as e:
            self.log(f"Raw export: {e}")
            self.sig_error.emit("Raw export", str(e))
        except Exception as e:
            self.log(f"ERROR exporting raw events: {e}")
            self.sig_error.emit("Raw export failed", str(e))
        finally:
            self.sig_done.emit("raw")

    # ================= usage analytics =================
    def _on_analytics(self):
        data_dir = self._data_dir()
        if not data_dir:
            return
        self.btn_analytics.setEnabled(False)
        for c in (self.cmb_ws, self.cmb_report, self.cmb_window):
            c.setEnabled(False)
        self.shell.busy_begin("Building usage analytics")
        self.log("Building Power BI usage analytics ...")
        threading.Thread(target=self._analytics_worker, args=(data_dir,), daemon=True).start()

    def _analytics_worker(self, data_dir):
        try:
            import analytics
            frames = analytics.compute(data_dir)          # one load, shared with the CSVs

            out_dir = data_dir / "analytics"
            out_dir.mkdir(parents=True, exist_ok=True)
            for name in ("report_usage_daily", "user_report_usage", "user_daily_usage"):
                frames[name].to_csv(out_dir / f"{name}.csv", index=False, encoding="utf-8-sig")
            self.log(f"Usage tables written under {out_dir}")

            # Records at (workspace, report, user, day) grain - everything the
            # in-app slicing needs without re-querying.
            rud = frames["report_usage_daily"]
            records = [{
                "workspace": str(r.workspace), "report": str(r.report),
                "report_id": str(r.report_id), "user": str(r.user),
                "date": str(r.date), "views": int(r.views),
            } for r in rud.itertuples(index=False)]
            self.sig_usage_data.emit(records)
            self.log(f"Usage analytics complete - {len(records):,} usage rows across "
                     f"{rud['report'].nunique()} reports.")
        except SystemExit as e:
            self.log(f"Usage analytics: {e}")
            self.sig_error.emit("Usage analytics", str(e))
        except Exception as e:
            self.log(f"ERROR building analytics: {e}")
            self.sig_error.emit("Usage analytics failed", str(e))
        finally:
            self.sig_done.emit("analytics")

    # ================= model lineage =================
    def _on_lineage_text(self, text):
        self.lineage_panel.setPlainText(text)

    def _on_lineage(self):
        data_dir = self._data_dir()
        if not data_dir:
            return
        try:
            settings = self._pbi_settings(data_dir)
        except ValueError as e:
            QMessageBox.warning(self, "Power BI settings", str(e))
            return
        self.btn_lineage.setEnabled(False)
        self.shell.busy_begin("Scanning model lineage")
        self.log("Scanning Power BI model lineage (tenant-wide) ...")
        threading.Thread(target=self._lineage_worker, args=(settings, data_dir), daemon=True).start()

    def _lineage_worker(self, settings, data_dir):
        try:
            from auth import PowerBITokenProvider
            from powerbi_client import PowerBIAdminClient
            from model_lineage import scan_model_lineage, render_model_lineage_text, write_model_lineage_report

            tokens = PowerBITokenProvider(settings)
            client = PowerBIAdminClient(tokens)
            results = scan_model_lineage(client, cancel_check=self.shell.cancel_requested,
                                         log=self.shell.sig_log.emit)
            if self.shell.cancel_requested():
                self.log("Model lineage scan cancelled - no report written.")
                return
            if not results:
                self.log("No semantic models found.")
                return
            text = render_model_lineage_text(results)
            self.sig_lineage_done.emit(text)
            out_path = write_model_lineage_report(results, data_dir / "model_lineage", self.shell.sig_log.emit)
            self.log(f"Model lineage report -> {Path(out_path).name}")
        except Exception as e:
            self.log(f"ERROR scanning model lineage: {e}")
            self.sig_error.emit("Model lineage scan failed",
                                f"{e}\n\nCheck the Power BI tenant/client IDs and credential in "
                                "Settings, and that the service principal is in the Power BI admin group.")
        finally:
            self.sig_done.emit("lineage")

    # ---------- filter wiring ----------
    def _on_usage_data(self, records):
        self._records = records
        if not records:
            clear_layout(self.usage_dash)
            self.usage_dash.addWidget(label("No view events found to aggregate.", "muted"))
            self.usage_dash.addStretch(1)
            return
        self.shell.last_pbi_usage = self._home_summary(records)
        self.shell.last_pbi_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

        self._building_filters = True
        self.cmb_ws.clear()
        self.cmb_ws.addItem("All workspaces", None)
        for w in sorted({r["workspace"] for r in records}):
            self.cmb_ws.addItem(w, w)
        self._populate_reports(None)
        self.cmb_window.setCurrentIndex(0)
        for c in (self.cmb_ws, self.cmb_report, self.cmb_window):
            c.setEnabled(True)
        self._building_filters = False
        self._render_usage()

    def _populate_reports(self, ws):
        self.cmb_report.blockSignals(True)
        self.cmb_report.clear()
        self.cmb_report.addItem("All reports", None)
        seen, opts = set(), []
        for r in self._records:
            if ws and r["workspace"] != ws:
                continue
            rid = r["report_id"] or r["report"]
            if rid in seen:
                continue
            seen.add(rid)
            text = r["report"] if ws else f'{r["report"]}  ·  {r["workspace"]}'
            opts.append((text, rid))
        for text, rid in sorted(opts, key=lambda x: x[0].lower()):
            self.cmb_report.addItem(text, rid)
        self.cmb_report.blockSignals(False)

    def _on_ws_changed(self, _i=0):
        if self._building_filters:
            return
        self._building_filters = True
        self._populate_reports(self.cmb_ws.currentData())
        self._building_filters = False
        self._render_usage()

    # ---------- aggregation ----------
    def _aggregate(self, ws, rid, since):
        recs = [r for r in self._records
                if (ws is None or r["workspace"] == ws)
                and (rid is None or (r["report_id"] or r["report"]) == rid)
                and (since is None or r["date"] >= since)]
        total = 0
        user_set, report_set = set(), set()
        reports, users, spaces, daily = {}, {}, {}, {}
        for r in recs:
            v = r["views"]
            total += v
            rk = r["report_id"] or r["report"]
            user_set.add(r["user"])
            report_set.add(rk)
            daily[r["date"]] = daily.get(r["date"], 0) + v
            rep = reports.setdefault(rk, {"report": r["report"], "workspace": r["workspace"],
                                          "views": 0, "users": set(), "last": ""})
            rep["views"] += v
            rep["users"].add(r["user"])
            rep["last"] = max(rep["last"], r["date"])
            u = users.setdefault(r["user"], {"views": 0, "reports": set(), "last": ""})
            u["views"] += v
            u["reports"].add(rk)
            u["last"] = max(u["last"], r["date"])
            s = spaces.setdefault(r["workspace"], {"views": 0, "reports": set(), "users": set()})
            s["views"] += v
            s["reports"].add(rk)
            s["users"].add(r["user"])
        return {"total": total, "active_users": len(user_set), "reports_viewed": len(report_set),
                "reports": reports, "users": users, "spaces": spaces, "daily": daily}

    @staticmethod
    def _days_since(iso, today):
        try:
            return (today - datetime.date.fromisoformat(iso)).days
        except Exception:
            return ""

    def _home_summary(self, records):
        agg = self._aggregate(None, None, None)
        rep_sorted = sorted(agg["reports"].values(), key=lambda d: -d["views"])
        days = len({r["date"] for r in records})
        low = [[d["report"], d["workspace"], d["views"]]
               for d in sorted(agg["reports"].values(), key=lambda d: d["views"])[:12]]
        return {
            "total_views": agg["total"], "distinct_reports": agg["reports_viewed"],
            "distinct_users": agg["active_users"], "days": days,
            "top_reports": [(d["report"], d["views"]) for d in rep_sorted[:6]],
            "low_usage": low,
        }

    # ---------- render ----------
    def _render_usage(self, *_):
        if self._building_filters or not self._records:
            return
        ws = self.cmb_ws.currentData()
        rid = self.cmb_report.currentData()
        win = self.cmb_window.currentIndex()
        since = None
        if win:
            days = {1: 7, 2: 30, 3: 90}[win]
            since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
        agg = self._aggregate(ws, rid, since)
        today = datetime.date.today()

        clear_layout(self.usage_dash)

        scope = []
        if ws:
            scope.append(ws)
        if rid:
            scope.append("1 report")
        scope.append(self.cmb_window.currentText().lower())
        avg = agg["total"] / agg["active_users"] if agg["active_users"] else 0
        self.usage_dash.addWidget(label("Scope: " + " · ".join(scope), "muted"))

        specs = [
            ("Total views", f"{agg['total']:,}", "recorded views", TEAL),
            ("Active users", f"{agg['active_users']:,}", "with ≥1 view", GOOD),
            ("Reports viewed", f"{agg['reports_viewed']:,}", "distinct", TEAL),
            ("Avg views / user", f"{avg:,.1f}", "engagement", TEAL),
        ]
        row, _ = kpi_row(specs)
        self.usage_dash.addWidget(row)

        # ranked bars - dimension name visible on every row
        rep_sorted = sorted(agg["reports"].values(), key=lambda d: -d["views"])
        usr_sorted = sorted(agg["users"].items(), key=lambda kv: -kv[1]["views"])
        bars = QHBoxLayout()
        rep_items = [((d["report"] if ws or rid else f'{d["report"]}  ·  {d["workspace"]}'), d["views"])
                     for d in rep_sorted]
        bars.addWidget(ranked_bars("Top reports by views", rep_items, colour=TEAL, max_n=10), 1)
        usr_items = [(u.split("@")[0], d["views"]) for u, d in usr_sorted]
        bars.addWidget(ranked_bars("Top users by views", usr_items, colour=GOOD, max_n=10), 1)
        bw = QWidget()
        bw.setLayout(bars)
        self.usage_dash.addWidget(bw)

        # trend
        daily = sorted(agg["daily"].items())
        if daily:
            self.usage_dash.addWidget(line_chart(
                "Views per day", [d for d, _ in daily], [v for _, v in daily], colour=TEAL))

        # reports table (sorted by views desc) with stale flag
        self.usage_dash.addWidget(label("REPORTS  (most used first; rows tinted by days since last "
                                        "view — amber 30+, red 90+)", "section"))
        rrows = []
        for d in rep_sorted[:80]:
            ds = self._days_since(d["last"], today)
            rrows.append([d["report"], d["workspace"], d["views"], len(d["users"]),
                          str(d["last"])[:10], ds])

        def rtint(_i, r):
            ds = r[5]
            if isinstance(ds, int) and ds >= 90:
                return TINT_BAD
            if isinstance(ds, int) and ds >= 30:
                return TINT_WARN
            return None

        self.usage_dash.addWidget(colored_table(
            ["Report", "Workspace", "Views", "Unique users", "Last viewed", "Days since"],
            rrows, row_colour=rtint, numeric_cols=(2, 3, 5)))

        # users + workspaces tables side by side
        cols = QHBoxLayout()
        ubox = QVBoxLayout()
        ubox.addWidget(label("TOP USERS", "section"))
        urows = [[u, d["views"], len(d["reports"]), str(d["last"])[:10]]
                 for u, d in usr_sorted[:40]]
        ubox.addWidget(colored_table(["User", "Views", "Reports", "Last active"],
                                     urows, numeric_cols=(1, 2)))
        uw = QWidget()
        uw.setLayout(ubox)
        cols.addWidget(uw, 1)

        sbox = QVBoxLayout()
        sbox.addWidget(label("WORKSPACES", "section"))
        sp_sorted = sorted(agg["spaces"].items(), key=lambda kv: -kv[1]["views"])
        srows = [[s, d["views"], len(d["reports"]), len(d["users"])] for s, d in sp_sorted]
        sbox.addWidget(colored_table(["Workspace", "Views", "Reports", "Users"],
                                     srows, numeric_cols=(1, 2, 3)))
        sw = QWidget()
        sw.setLayout(sbox)
        cols.addWidget(sw, 1)
        cw = QWidget()
        cw.setLayout(cols)
        self.usage_dash.addWidget(cw)
        self.usage_dash.addStretch(1)
        self.shell.refresh_status()

    # ================= worker done =================
    def _on_done(self, which):
        self.shell.busy_end()
        if which == "collect":
            self.btn_collect.setEnabled(True)
            self.btn_catchup.setEnabled(True)
        elif which == "raw":
            self.btn_raw.setEnabled(True)
        elif which == "analytics":
            self.btn_analytics.setEnabled(True)
        elif which == "lineage":
            self.btn_lineage.setEnabled(True)
