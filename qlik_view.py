"""Qlik workspace view for Bufab BI Governance Studio.

This is the original Qlik Governance Studio MainWindow refactored into a single
QWidget that plugs into the unified shell. The shared header, status line, busy
indicator and LOG panel now live on the shell; this view keeps the app-selection
card, the six feature tabs, their threaded workers, and a new in-app Capacity
dashboard that renders the scan result instead of only writing Excel.

All Qlik logic is reused unchanged from qlik_core / qlik_capacity.
"""
from __future__ import annotations

import os
import time
import datetime
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QPlainTextEdit, QFileDialog, QMessageBox,
    QScrollArea, QComboBox, QCompleter,
)

import qlik_core as core
import qlik_capacity as qcap
import reports
from scope_sheet import ScopeBar
from widgets import (
    TEAL, BAD, WARN, GOOD, ActionBar, TaskHub,
    make_card, label, tip,
    key_format_ok, scrub, friendly_load_error, human_bytes,
    MeterBar, kpi_row, ranked_bars, colored_table, clear_layout,
)

# light row tints for the colour-coded action list
TINT_BAD = "#F7D9DE"
TINT_WARN = "#F7ECD2"
TINT_MUTED = "#EFF2F3"


class QlikView(QWidget):
    sig_loaded = Signal(list)
    sig_load_failed = Signal(str)
    sig_done = Signal(str)
    sig_error = Signal(str, str)
    sig_fields_loaded = Signal(list)
    sig_trace_done = Signal(str, str)
    sig_qvd_usage_done = Signal(str)
    sig_tenant_usage_done = Signal(str)
    sig_diag_visibility_done = Signal(str)
    sig_index_built = Signal(int, object)
    sig_capacity_result = Signal(object)
    sig_consistency_result = Signal(object)
    sig_usage_result_q = Signal(object)

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._loaded_sig = None
        self._producer_map = None
        self._usage_app_results = None
        self._building_usage_combo = False

        self.sig_loaded.connect(self._populate)
        self.sig_load_failed.connect(self._on_load_failed)
        self.sig_done.connect(self._on_worker_done)
        self.sig_error.connect(lambda t, m: QMessageBox.critical(self, t, m))
        self.sig_fields_loaded.connect(self._on_fields_loaded)
        self.sig_trace_done.connect(self._on_trace_done)
        self.sig_qvd_usage_done.connect(self._on_qvd_usage_text)
        self.sig_tenant_usage_done.connect(self._on_tenant_usage_text)
        self.sig_diag_visibility_done.connect(self._on_diag_visibility_text)
        self.sig_index_built.connect(self._on_index_built)
        self.sig_capacity_result.connect(self._render_capacity)
        self.sig_consistency_result.connect(self._render_consistency)
        self.sig_usage_result_q.connect(self._on_usage_results_q)

        self._build()

    # convenience accessors onto shared state
    @property
    def tenant(self):
        return self.shell.tenant

    @property
    def api_key(self):
        return self.shell.api_key

    @property
    def output_dir(self):
        return self.shell.output_dir

    def _feature_dir(self, name):
        """This feature's own subfolder inside the library: <library>/Qlik/<name>."""
        return self.shell.feature_dir("Qlik", name)

    def _record(self, path, type_key, title, scope="", started=None, headline=()):
        """File a finished run in the library index (REDESIGN_SPEC.md step 3)."""
        reports.record(self.output_dir, path, "Qlik", type_key, title,
                       scope=scope, started=started, headline=headline, log=self.log)
        self.shell.reports_changed()

    def log(self, msg):
        self.shell.log(msg)

    # ---------------- layout ----------------
    def _build(self):
        """A scope bar over the task area. The app table that used to sit here
        permanently is now the scope sheet, opened on demand (REDESIGN_SPEC.md,
        structural change 2), which gives every task page the whole workspace."""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        self.scope_bar = ScopeBar(self.shell, on_load=self._on_load_apps)
        root.addWidget(self.scope_bar)
        root.addWidget(self._build_task_area(), 1)

    def _build_task_area(self):
        """The Qlik tasks as a hub of cards plus one page per task, instead of
        a tab strip (REDESIGN_SPEC.md, structural change 1).

        Every page below is built exactly as it was when this was a
        QTabWidget - only the final `addTab` became `_add_task`, which puts a
        card on the hub and the page in the stack. No worker, signal or handler
        moved, so the threading model is untouched."""
        self.hub = TaskHub("Qlik Cloud")

        # Extract
        tab_x = QWidget()
        xl = QVBoxLayout(tab_x)
        xl.setContentsMargins(0, 10, 0, 0)
        xc = make_card()
        xcl = QVBoxLayout(xc)
        xcl.addWidget(label("WHAT TO EXPORT  (for each selected app)", "section"))
        row = QHBoxLayout()
        self.checks = {}
        for key, text in (("measures", "Master measures"), ("dimensions", "Master dimensions"),
                          ("variables", "Variables"), ("script", "Load script"), ("visuals", "Visuals")):
            cb = QCheckBox(text)
            cb.setChecked(True)
            self.checks[key] = cb
            row.addWidget(cb)
        row.addStretch(1)
        b_sel_all = QPushButton("Select all")
        b_sel_all.setObjectName("ghost")
        b_sel_all.clicked.connect(lambda: self._set_all_exports(True))
        b_sel_none = QPushButton("Unselect all")
        b_sel_none.setObjectName("ghost")
        b_sel_none.clicked.connect(lambda: self._set_all_exports(False))
        row.addWidget(b_sel_all)
        row.addWidget(b_sel_none)
        xcl.addLayout(row)
        xl.addWidget(xc)
        xl.addStretch(1)
        self.btn_run = QPushButton("Run export")
        tip(self.btn_run, "Writes one Excel workbook per selected app, containing the item "
                          "types ticked above, into the library's Qlik/metadata_export folder.")
        self.btn_run.clicked.connect(self._on_run)
        self.bar_export = ActionBar(self.btn_run)
        for cb in self.checks.values():
            cb.toggled.connect(lambda _on: self.refresh_ready())
        self.hub.add(tab_x, "Extract metadata", "Measures, dimensions, variables, load script "
                     "and visuals for each app in scope.", bar=self.bar_export)

        # Comparison
        tab_c = QWidget()
        cl = QVBoxLayout(tab_c)
        cl.setContentsMargins(0, 10, 0, 0)
        cc = make_card()
        ccl = QVBoxLayout(cc)
        ccl.addWidget(label("CROSS-APP CONSISTENCY  (measures & dimensions)", "section"))
        ccl.addWidget(label("Needs at least two apps in scope.", "muted"))
        cl.addWidget(cc)
        self.btn_analyze = QPushButton("Analyze consistency")
        self.btn_analyze.clicked.connect(self._on_analyze)
        self.bar_compare = ActionBar(self.btn_analyze)
        cons_scroll = QScrollArea()
        cons_scroll.setWidgetResizable(True)
        cons_holder = QWidget()
        self.cons_dash = QVBoxLayout(cons_holder)
        self.cons_dash.setContentsMargins(0, 8, 0, 0)
        self.cons_dash.setSpacing(10)
        self.cons_dash.addWidget(label("Run the analysis to see conflicts and redundancy here.", "muted"))
        self.cons_dash.addStretch(1)
        cons_scroll.setWidget(cons_holder)
        cl.addWidget(cons_scroll, 1)
        self.hub.add(tab_c, "Comparison analysis", "Name conflicts and redundancy in measures "
                     "and dimensions across 2+ apps.", bar=self.bar_compare)

        # Usage
        tab_u = QWidget()
        ul = QVBoxLayout(tab_u)
        ul.setContentsMargins(0, 10, 0, 0)
        uc = make_card()
        ucl = QVBoxLayout(uc)
        ucl.addWidget(label("USAGE & LEANNESS  (what is NOT used)", "section"))
        ucl.addWidget(label("For each selected app: flags unused master items, model fields, tables "
                            "and variables.", "muted"))
        warn = label("Results are CANDIDATES - verify before deleting.", "muted")
        warn.setStyleSheet(f"color: {BAD};")
        tip(warn, "Dynamic $(=...) expressions can hide real usage. The report lists those "
                        "separately so you can review them by hand instead of trusting the flag.")
        ucl.addWidget(warn)
        ul.addWidget(uc)
        self.btn_usage = QPushButton("Analyze usage")
        tip(self.btn_usage, "Read-only. For each selected app, flags master items, model "
                            "fields, tables and variables that nothing references, and shows "
                            "them per app below.")
        self.btn_usage.clicked.connect(self._on_usage)
        self.bar_usage = ActionBar(self.btn_usage)
        usel = QHBoxLayout()
        usel.addWidget(label("Show app", "muted"))
        self.cmb_usage_app = QComboBox()
        self.cmb_usage_app.setMinimumWidth(300)
        self.cmb_usage_app.setEnabled(False)
        self.cmb_usage_app.currentIndexChanged.connect(lambda _i: self._render_usage_q())
        usel.addWidget(self.cmb_usage_app, 1)
        usel.addStretch(1)
        ul.addLayout(usel)
        u_scroll = QScrollArea()
        u_scroll.setWidgetResizable(True)
        u_holder = QWidget()
        self.usage_dash_q = QVBoxLayout(u_holder)
        self.usage_dash_q.setContentsMargins(0, 8, 0, 0)
        self.usage_dash_q.setSpacing(10)
        self.usage_dash_q.addWidget(label("Analyze usage to see unused items per app here.", "muted"))
        self.usage_dash_q.addStretch(1)
        u_scroll.setWidget(u_holder)
        ul.addWidget(u_scroll, 1)
        self.hub.add(tab_u, "Usage & leanness", "What is not used: master items, model fields, "
                     "tables and variables.", bar=self.bar_usage)

        # Apply (WRITE)
        tab_a = QWidget()
        al = QVBoxLayout(tab_a)
        al.setContentsMargins(0, 10, 0, 0)
        ac = make_card()
        acl = QVBoxLayout(ac)
        acl.addWidget(label("APPLY MASTER ITEMS  (create / update / delete - measures & dimensions)", "section"))
        awarn = label("This WRITES to and SAVES the selected app(s).", "muted")
        awarn.setStyleSheet(f"color: {BAD};")
        tip(awarn, "A backup of the current master items is exported first. Use Dry run to "
                         "preview every change with nothing written.")
        acl.addWidget(awarn)
        agrid = QGridLayout()
        agrid.setHorizontalSpacing(10)
        agrid.setVerticalSpacing(8)
        agrid.addWidget(label("Measures CSV", "muted"), 0, 0)
        self.ed_meas_csv = QLineEdit()
        self.ed_meas_csv.setReadOnly(True)
        self.ed_meas_csv.setPlaceholderText("(optional) exported master_measures_*.csv")
        agrid.addWidget(self.ed_meas_csv, 0, 1)
        bm = QPushButton("Browse...")
        bm.setObjectName("ghost")
        bm.clicked.connect(lambda: self._browse_csv(self.ed_meas_csv))
        agrid.addWidget(bm, 0, 2)
        agrid.addWidget(label("Dimensions CSV", "muted"), 1, 0)
        self.ed_dim_csv = QLineEdit()
        self.ed_dim_csv.setReadOnly(True)
        self.ed_dim_csv.setPlaceholderText("(optional) exported master_dimensions_*.csv")
        agrid.addWidget(self.ed_dim_csv, 1, 1)
        bd = QPushButton("Browse...")
        bd.setObjectName("ghost")
        bd.clicked.connect(lambda: self._browse_csv(self.ed_dim_csv))
        agrid.addWidget(bd, 1, 2)
        agrid.addWidget(label("Mode", "muted"), 2, 0)
        self.cmb_mode = QComboBox()
        self.cmb_mode.addItems(["Create + update (sync)", "Create only", "Update only", "Delete"])
        agrid.addWidget(self.cmb_mode, 2, 1)
        agrid.setColumnStretch(1, 1)
        acl.addLayout(agrid)
        al.addWidget(ac)
        al.addStretch(1)
        # Two buttons rather than a dry-run checkbox: the design puts the safe
        # path and the real one side by side so which one you are about to take
        # is visible in the bar, not folded into a tick you may have left off.
        self.btn_dry = QPushButton("Dry run")
        tip(self.btn_dry, "Reports every change the CSV would make and writes nothing.")
        self.btn_dry.clicked.connect(lambda: self._on_apply(dry=True))
        self.btn_apply = QPushButton("Apply for real")
        self.btn_apply.clicked.connect(lambda: self._on_apply(dry=False))
        self.bar_apply = ActionBar(self.btn_apply, secondary=[self.btn_dry])
        for ed in (self.ed_meas_csv, self.ed_dim_csv):
            ed.textChanged.connect(lambda _t: self.refresh_ready())
        self.hub.add(tab_a, "Apply master items", "Create, update or delete measures and "
                     "dimensions from a CSV. Dry run first.", "writes", bar=self.bar_apply)

        # QVD field usage. Split from the field trace below into its own task
        # rather than sharing a page: they need different scopes (any number of
        # apps vs exactly one) and so cannot share one primary action.
        tab_q = QWidget()
        ql = QVBoxLayout(tab_q)
        ql.setContentsMargins(0, 10, 0, 0)
        qc = make_card()
        qcl = QVBoxLayout(qc)
        qcl.addWidget(label("QVD FIELD USAGE REPORT", "section"))
        qcl.addWidget(label("Which QVDs each app in scope reads, and which of their fields reach "
                            "the final data model.", "muted"))
        self.chk_qvd_upstream = QCheckBox("Also trace upstream to the true source")
        tip(self.chk_qvd_upstream, "Resolves each confirmed field back to the database table "
                                   "or file it originally came from. Slower: it opens every "
                                   "QVD's producing app via Qlik's own lineage graph.")
        qcl.addWidget(self.chk_qvd_upstream)
        ql.addWidget(qc)
        self.qvd_panel = QPlainTextEdit()
        self.qvd_panel.setReadOnly(True)
        self.qvd_panel.setMinimumHeight(150)
        ql.addWidget(self.qvd_panel, 1)
        self.btn_qvd_usage = QPushButton("Scan QVD field usage")
        tip(self.btn_qvd_usage, "Writes one combined workbook (qvd_field_usage_*.xlsx) and "
                                "shows a summary below. Treat 'not found' fields as a "
                                "prioritized worklist, not a verdict.")
        self.btn_qvd_usage.clicked.connect(self._on_qvd_usage)
        self.bar_qvd = ActionBar(self.btn_qvd_usage)
        self.hub.add(tab_q, "QVD field usage", "Which QVDs each app in scope reads, and which of "
                     "their fields reach its model.", bar=self.bar_qvd)

        # Trace a field
        tab_l = QWidget()
        ll = QVBoxLayout(tab_l)
        ll.setContentsMargins(0, 10, 0, 0)
        lc = make_card()
        lcl = QVBoxLayout(lc)
        lcl.addWidget(label("TRACE A FIELD", "section"))
        lcl.addWidget(label("The pipeline one field took INTO one app. Needs exactly one app in "
                            "scope.", "muted"))
        irow = QHBoxLayout()
        self.btn_index = QPushButton("Build cross-app index")
        self.btn_index.setObjectName("ghost")
        tip(self.btn_index, "Optional. Indexes every app's load script so the fallback trace "
                            "can find a producing app when the native lineage graph has no "
                            "answer.")
        self.btn_index.clicked.connect(self._on_build_index)
        irow.addWidget(self.btn_index)
        self.lbl_index = QLabel("Cross-app index: not built (only used by the fallback trace)")
        self.lbl_index.setObjectName("muted")
        irow.addWidget(self.lbl_index, 1)
        lcl.addLayout(irow)
        self.chk_native = QCheckBox("Add upstream apps from Qlik's own lineage")
        self.chk_native.setChecked(True)
        tip(self.chk_native, "Extends the pipeline back into the apps that produce the "
                             "source, rather than stopping at the first QVD.")
        lcl.addWidget(self.chk_native)
        frow = QHBoxLayout()
        self.btn_load_fields = QPushButton("Load fields")
        self.btn_load_fields.setObjectName("ghost")
        self.btn_load_fields.clicked.connect(self._on_load_fields)
        frow.addWidget(self.btn_load_fields)
        self.cmb_field = QComboBox()
        self.cmb_field.setEditable(True)
        self.cmb_field.setInsertPolicy(QComboBox.NoInsert)
        self.cmb_field.lineEdit().setPlaceholderText("Field (load fields first, then type to filter)")
        self.cmb_field.currentTextChanged.connect(lambda _t: self.refresh_ready())
        frow.addWidget(self.cmb_field, 1)
        lcl.addLayout(frow)
        ll.addWidget(lc)
        self.lineage_panel = QPlainTextEdit()
        self.lineage_panel.setReadOnly(True)
        self.lineage_panel.setMinimumHeight(150)
        ll.addWidget(self.lineage_panel, 1)
        self.btn_trace = QPushButton("Trace field")
        self.btn_trace.clicked.connect(self._on_trace)
        self.bar_trace = ActionBar(self.btn_trace)
        self.hub.add(tab_l, "Trace a field", "The pipeline one field took into one app, back to "
                     "the database table or file.", bar=self.bar_trace)

        # Capacity (controls + dashboard)
        tab_cap = QWidget()
        capl = QVBoxLayout(tab_cap)
        capl.setContentsMargins(0, 10, 0, 0)
        capc = make_card()
        capcl = QVBoxLayout(capc)
        capcl.addWidget(label("CAPACITY REPORT  (App reload + Import)", "section"))
        capcl.addWidget(label("Every app's data-model size and every imported dataset, "
                              "tenant-wide.", "muted"))
        self.chk_cap_orphans = QCheckBox("Include orphan scan")
        tip(self.chk_cap_orphans, "Reads every app's load script to flag imported datasets "
                                        "and data files that no app uses. Slower.")
        capcl.addWidget(self.chk_cap_orphans)
        self.btn_capacity = QPushButton("Scan capacity")
        tip(self.btn_capacity, "No app selection needed. Ranks the biggest savings, shows the "
                               "result below and writes one Excel workbook.")
        self.btn_capacity.clicked.connect(self._on_capacity)
        self.bar_capacity = ActionBar(self.btn_capacity, status="Tenant-wide  ·  no scope needed")
        capl.addWidget(capc)

        # results dashboard (filled after a scan)
        cap_scroll = QScrollArea()
        cap_scroll.setWidgetResizable(True)
        cap_holder = QWidget()
        self.cap_dash = QVBoxLayout(cap_holder)
        self.cap_dash.setContentsMargins(0, 8, 0, 0)
        self.cap_dash.setSpacing(10)
        self.cap_dash.addWidget(label("Run a scan to see the capacity dashboard here.", "muted"))
        self.cap_dash.addStretch(1)
        cap_scroll.setWidget(cap_holder)
        capl.addWidget(cap_scroll, 1)
        self.hub.add(tab_cap, "Capacity report", "Every app's data-model size and every imported "
                     "dataset, ranked by saving.", "tenant-wide", bar=self.bar_capacity)

        # Tenant QVD & field usage (published apps only)
        tab_t = QWidget()
        tl = QVBoxLayout(tab_t)
        tl.setContentsMargins(0, 10, 0, 0)
        tc = make_card()
        tcl = QVBoxLayout(tc)
        tcl.addWidget(label("TENANT QVD & FIELD USAGE", "section"))
        tcl.addWidget(label("Published apps walked back through every upstream app that feeds "
                            "them.", "muted"))
        self.btn_tenant_usage = QPushButton("Scan tenant usage")
        tip(self.btn_tenant_usage, 
            "Scans every PUBLISHED app in the tenant, then walks backward via Qlik's own lineage "
            "graph through every upstream/staging app that feeds it, for the full source-to-model "
            "picture: which QVDs are read, which fields make it into the model, whether a published "
            "app's field is also actually used in a measure, dimension or visual, and for every "
            "field its TRUE origin - traced as far back as the lineage allows, ideally to a "
            "database table and the connection it came from, or to a file, wherever the chain of "
            "producing apps stops.\n\nNo app selection needed, but it opens every app in the "
            "lineage, root and upstream alike, so it can take a while.")
        self.btn_tenant_usage.clicked.connect(self._on_tenant_usage)
        self.bar_tenant = ActionBar(self.btn_tenant_usage,
                                    status="Tenant-wide  ·  no scope needed  ·  can take a while")
        tl.addWidget(tc)
        self.tenant_usage_panel = QPlainTextEdit()
        self.tenant_usage_panel.setReadOnly(True)
        self.tenant_usage_panel.setMinimumHeight(150)
        tl.addWidget(self.tenant_usage_panel, 1)
        self.hub.add(tab_t, "Tenant QVD usage", "Published apps walked back "
                     "through every upstream app that feeds them.", "tenant-wide",
                     bar=self.bar_tenant)

        # Diagnose visibility is its own task: it is troubleshooting, not part
        # of a tenant scan, and it needs no app selection.
        tab_d = QWidget()
        dl = QVBoxLayout(tab_d)
        dl.setContentsMargins(0, 10, 0, 0)
        dc = make_card()
        dcl = QVBoxLayout(dc)
        dcl.addWidget(label("DIAGNOSE APP VISIBILITY", "section"))
        dcl.addWidget(label("Test whether one app GUID is reachable with the current API key.",
                            "muted"))
        dg = QGridLayout()
        dg.addWidget(label("App GUID to test", "muted"), 0, 0)
        self.ed_diag_guid = QLineEdit()
        self.ed_diag_guid.setPlaceholderText("the suspected app's GUID")
        dg.addWidget(self.ed_diag_guid, 0, 1)
        dg.addWidget(label("Consumer app GUID (optional)", "muted"), 1, 0)
        self.ed_diag_consumer = QLineEdit()
        self.ed_diag_consumer.setPlaceholderText("an app known to read a QVD it produces - checks "
                                                 "the native lineage graph too")
        dg.addWidget(self.ed_diag_consumer, 1, 1)
        dg.setColumnStretch(1, 1)
        dcl.addLayout(dg)
        self.btn_diag_visibility = QPushButton("Run diagnostic")
        tip(self.btn_diag_visibility, 
            "A suspected app - one you've confirmed sits in someone's Personal space, say - can be "
            "invisible to this tool's normal app list without ever showing up as an error.\n\n"
            "This checks, one layer at a time, whether THIS API key can see it in the Items API "
            "(what every app list in this tool is built from), reach it directly by GUID, open it "
            "via the Engine API, and whether it appears in another app's native lineage graph as a "
            "producer.")
        self.btn_diag_visibility.clicked.connect(self._on_diag_visibility)
        self.bar_diag = ActionBar(self.btn_diag_visibility)
        self.ed_diag_guid.textChanged.connect(lambda _t: self.refresh_ready())
        dl.addWidget(dc)
        self.diag_visibility_panel = QPlainTextEdit()
        self.diag_visibility_panel.setReadOnly(True)
        self.diag_visibility_panel.setMinimumHeight(120)
        dl.addWidget(self.diag_visibility_panel, 1)
        self.hub.add(tab_d, "Diagnose visibility", "Test whether a specific "
                     "app GUID is reachable with the current key.", "advanced",
                     bar=self.bar_diag)

        self.hub.finish()
        self.refresh_ready()
        return self.hub

    # ---------------- action-bar readiness ----------------
    def refresh_ready(self):
        """Keep every action bar's status line and primary button honest.

        Called on any scope change and whenever a page's own inputs change, so
        a task that cannot run yet says why in the bar instead of letting you
        click and collect a warning dialog (REDESIGN_SPEC.md step 5)."""
        n = len(self.shell.scope)
        apps = f"{n} app{'' if n == 1 else 's'} in scope"
        none_in_scope = "Nothing in scope - use Change scope above"

        types = sum(1 for cb in self.checks.values() if cb.isChecked())
        self.bar_export.set_status(f"{apps}  ·  {types} item type{'' if types == 1 else 's'}")
        self.bar_export.set_ready(
            n and types,
            none_in_scope if not n else "Tick at least one item type to export")

        self.bar_compare.set_status(apps)
        self.bar_compare.set_ready(n >= 2, "Needs at least two apps in scope to compare")

        self.bar_usage.set_status(f"{apps}  ·  read-only")
        self.bar_usage.set_ready(n, none_in_scope)

        csv_count = sum(1 for ed in (self.ed_meas_csv, self.ed_dim_csv) if ed.text().strip())
        self.bar_apply.set_status(f"{apps}  ·  {csv_count} CSV loaded  ·  WRITES to these apps")
        self.bar_apply.set_ready(
            n and csv_count,
            none_in_scope if not n else "Choose a measures and/or dimensions CSV")
        self.btn_dry.setEnabled(bool(n and csv_count))

        self.bar_qvd.set_status(apps)
        self.bar_qvd.set_ready(n, none_in_scope)

        field = self.cmb_field.currentText().strip()
        self.bar_trace.set_status(f"{apps}  ·  {field or 'no field picked'}")
        self.bar_trace.set_ready(
            n == 1 and field,
            "Needs exactly one app in scope" if n != 1 else "Load fields, then pick one")

        self.bar_diag.set_ready(bool(self.ed_diag_guid.text().strip()),
                                "Paste the app GUID to test")

    def _set_all_exports(self, on):
        for cb in self.checks.values():
            cb.setChecked(on)

    # ---------------- shared checks ----------------
    def _need_settings(self):
        if not self.tenant or not self.api_key.strip():
            QMessageBox.warning(self, "Missing settings",
                                "Set the Qlik tenant and API key in Settings first.")
            return True
        if not key_format_ok(self.api_key):
            QMessageBox.critical(self, "Invalid API key",
                                 "The API key looks invalid - it contains spaces or line breaks.\n\n"
                                 "Open Settings and paste only the API key text (no extra lines).")
            self.log("Cancelled: the API key contains spaces or line breaks - re-paste it in Settings.")
            return True
        return False

    def refresh_after_settings(self):
        """Called by the shell when settings change: reload apps if creds are set."""
        if self.tenant and self.api_key.strip():
            sig = (self.tenant, self.api_key)
            if not self.shell.apps or sig != self._loaded_sig:
                self._on_load_apps()

    # ---------------- load apps ----------------
    def _on_load_apps(self):
        if self._need_settings():
            return
        self.scope_bar.btn_load.setEnabled(False)
        self.shell.busy_begin("Loading apps")
        self.log("Loading spaces and apps ...")
        threading.Thread(target=self._load_worker,
                         args=(self.tenant, self.api_key), daemon=True).start()

    def _load_worker(self, tenant, key):
        try:
            try:
                spaces = core.list_spaces(tenant, key)
            except Exception:
                spaces = {}
            apps = core.list_apps(tenant, key)
            for a in apps:
                sid = a["space_id"]
                a["space_name"] = "Personal" if not sid else spaces.get(sid, sid)
            self.sig_loaded.emit(apps)
        except Exception as e:
            self.sig_load_failed.emit(friendly_load_error(e))
        finally:
            self.sig_done.emit("load")

    def _populate(self, apps):
        self._loaded_sig = (self.tenant, self.api_key)
        self.shell.set_apps(apps)
        spaces = len({a["space_name"] for a in apps})
        kept = len(self.shell.scope)
        tail = f"  {kept} of them still in scope." if kept else ""
        self.log(f"Loaded {len(apps)} apps across {spaces} spaces.{tail}")

    def _on_load_failed(self, msg):
        self.log(f"Could not load apps: {msg}")
        QMessageBox.critical(self, "Load apps failed", msg)

    def _on_worker_done(self, which):
        self.shell.busy_end()
        if which == "load":
            self.scope_bar.btn_load.setEnabled(bool(self.tenant and self.api_key.strip()))
            self.shell.refresh_status()
        elif which == "run":
            self.btn_run.setEnabled(True)
        elif which == "analyze":
            self.btn_analyze.setEnabled(True)
        elif which == "usage":
            self.btn_usage.setEnabled(True)
        elif which == "capacity":
            self.btn_capacity.setEnabled(True)
        elif which == "apply":
            self.btn_apply.setEnabled(True)
        elif which == "lineage_fields":
            self.btn_load_fields.setEnabled(True)
        elif which == "lineage":
            self.btn_trace.setEnabled(True)
        elif which == "qvd_usage":
            self.btn_qvd_usage.setEnabled(True)
        elif which == "tenant_usage":
            self.btn_tenant_usage.setEnabled(True)
        elif which == "index":
            self.btn_index.setEnabled(True)
        elif which == "diag_visibility":
            self.btn_diag_visibility.setEnabled(True)

    # ---------------- export ----------------
    def _on_run(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        if not any(cb.isChecked() for cb in self.checks.values()):
            QMessageBox.warning(self, "Nothing selected", "Tick at least one export.")
            return
        targets = self.shell.scope_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to export.")
            return
        self.btn_run.setEnabled(False)
        self.shell.busy_begin("Exporting metadata")
        self.log(f"Exporting {len(targets)} app(s) ...")
        flags = tuple(self.checks[k].isChecked()
                      for k in ("measures", "dimensions", "variables", "script", "visuals"))
        threading.Thread(target=self._export_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("metadata_export"),
                               targets, flags),
                         daemon=True).start()

    def _export_worker(self, tenant, key, out_dir, targets, flags):
        t0 = time.time()
        done = []
        try:
            for a in targets:
                if self.shell.cancel_requested():
                    self.log("Export cancelled.")
                    break
                exporter = core.QlikExporter(tenant, key, a["guid"], out_dir, self.shell.sig_log.emit)
                try:
                    exporter.run(*flags)
                except Exception as e:
                    self.log(f"ERROR exporting {a.get('name', a['guid'])}: {scrub(key, e)}")
                    done.append(a.get("name", a["guid"]))
                finally:
                    exporter.close()
            self.log("All exports finished.")
            if done:
                # One workbook per app, so the record points at the folder the
                # run filled rather than at a single file.
                self._record(out_dir, "metadata_export", "Metadata export",
                             scope=f"{len(done)} apps", started=t0,
                             headline=[reports.num("Apps exported", len(done)),
                                       reports.num("Item types", sum(1 for f in flags if f))])
        finally:
            self.sig_done.emit("run")

    # ---------------- analyze ----------------
    def _on_analyze(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        targets = self.shell.scope_targets()
        if len(targets) < 2:
            QMessageBox.warning(self, "Select apps", "Select at least 2 apps in the list to compare.")
            return
        self.btn_analyze.setEnabled(False)
        self.shell.busy_begin("Analyzing consistency")
        self.log(f"Analyzing {len(targets)} app(s) for measure/dimension consistency ...")
        threading.Thread(target=self._analyze_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("comparison_analysis"),
                               targets),
                         daemon=True).start()

    def _analyze_worker(self, tenant, key, out_dir, targets):
        t0 = time.time()
        measures, dims = [], []
        try:
            for a in targets:
                if self.shell.cancel_requested():
                    self.log("Consistency scan cancelled.")
                    break
                exp = core.QlikExporter(tenant, key, a["guid"], out_dir, self.shell.sig_log.emit)
                try:
                    exp.connect()
                    app_h = exp.call(-1, "OpenDoc", [a["guid"]])["qReturn"]["qHandle"]
                    title = exp.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", a["guid"])
                    varmap = {v["name"]: v["definition"] for v in exp.fetch_variables(app_h) if v.get("name")}
                    mc = dc = 0
                    for mrow in exp.fetch_measures(app_h):
                        full = core.expand_vars(mrow["expression"], varmap)
                        mrow.update(app=title, app_guid=a["guid"], space=a.get("space_name", ""),
                                    expr_expanded=full, has_unexpanded=("$(" in full))
                        measures.append(mrow)
                        mc += 1
                    for drow in exp.fetch_dimensions(app_h):
                        full = core.expand_vars(drow["fields"], varmap)
                        drow.update(app=title, app_guid=a["guid"], space=a.get("space_name", ""),
                                    definition=drow["fields"], def_expanded=full)
                        dims.append(drow)
                        dc += 1
                    self.log(f"  Scanned {title}: {mc} measures, {dc} dimensions")
                finally:
                    exp.close()
            if self.shell.cancel_requested():
                self.log("Consistency analysis cancelled - no report written.")
                return
            if not measures and not dims:
                self.log("No master measures or dimensions found in the selected apps.")
                return
            results = core.analyze_consistency(measures, dims)
            self.sig_consistency_result.emit({
                "results": results,
                "n_measures": len(measures), "n_dims": len(dims),
                "n_apps": len({m["app"] for m in measures} | {d["app"] for d in dims}),
            })
            out_path = core.write_consistency_report(results, measures, dims, out_dir, self.shell.sig_log.emit)
            self.log("Analysis complete: "
                     f"{len(results['measure_name_conflicts'])} measure name-conflicts, "
                     f"{len(results['measure_redundancy'])} measure redundancy groups, "
                     f"{len(results['dimension_name_conflicts'])} dimension name-conflicts, "
                     f"{len(results['dimension_redundancy'])} dimension redundancy groups.")
            self.log(f"Report -> {os.path.basename(out_path)}")
            self._record(out_path, "comparison_analysis", "Cross-app consistency",
                         scope=f"{len(targets)} apps", started=t0, headline=[
                             reports.num("Name conflicts",
                                         len(results["measure_name_conflicts"])
                                         + len(results["dimension_name_conflicts"])),
                             reports.num("Redundancy groups",
                                         len(results["measure_redundancy"])
                                         + len(results["dimension_redundancy"])),
                             reports.num("Master items", len(measures) + len(dims))])
        except Exception as e:
            self.log(f"ERROR: {scrub(key, e)}")
            self.sig_error.emit("Analysis failed", scrub(key, e))
        finally:
            self.sig_done.emit("analyze")

    # ---------------- usage ----------------
    def _on_usage(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        targets = self.shell.scope_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to analyze.")
            return
        self.btn_usage.setEnabled(False)
        self.shell.busy_begin("Analyzing usage")
        self.log(f"Analyzing usage for {len(targets)} app(s) ...")
        threading.Thread(target=self._usage_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("usage_analysis"),
                               targets),
                         daemon=True).start()

    def _usage_worker(self, tenant, key, out_dir, targets):
        t0 = time.time()
        app_results = []
        try:
            for a in targets:
                if self.shell.cancel_requested():
                    self.log("Usage scan cancelled.")
                    break
                exp = core.QlikExporter(tenant, key, a["guid"], out_dir, self.shell.sig_log.emit)
                try:
                    exp.connect()
                    app_h = exp.call(-1, "OpenDoc", [a["guid"]])["qReturn"]["qHandle"]
                    title = exp.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", a["guid"])
                    measures = exp.fetch_measures(app_h)
                    dims = exp.fetch_dimensions(app_h)
                    variables = exp.fetch_variables(app_h)
                    objects = exp.fetch_objects(app_h)
                    model_fields = exp.fetch_model_fields(app_h)
                    result = core.analyze_usage(measures, dims, variables, objects, model_fields)
                    out_path = core.write_usage_report(result, title, a["guid"], out_dir, self.shell.sig_log.emit)
                    app_results.append({"title": title, "guid": a["guid"], "result": result})
                    dyn = result["dynamic"]
                    active = sum(1 for d in dyn if "active" in d["type"])
                    self.log(f"  {title}: {len(result['fields']['unused'])} unused fields, "
                             f"{len(result['master']['unused'])} unused master items, "
                             f"{len(result['variables']['unused'])} unused variables.")
                    if dyn:
                        self.log(f"  WARNING - {title} uses {len(dyn)} dynamic $(...) expressions "
                                 f"({active} active $(=...)): verify the 'Dynamic expressions' sheet manually.")
                    self.log(f"  Report -> {os.path.basename(out_path)}")
                except Exception as e:
                    self.log(f"ERROR analyzing {a.get('name', a['guid'])}: {scrub(key, e)}")
                finally:
                    exp.close()
            self.log("Usage analysis finished.")
            if app_results:
                self.sig_usage_result_q.emit(app_results)
                cand = sum(len(r["result"]["fields"]["unused"])
                           + len(r["result"]["master"]["unused"])
                           + len(r["result"]["variables"]["unused"]) for r in app_results)
                dyn = sum(len(r["result"]["dynamic"]) for r in app_results)
                self._record(out_dir, "usage_analysis", "Usage & leanness",
                             scope=f"{len(app_results)} apps", started=t0,
                             headline=[reports.num("Candidates", cand),
                                       reports.num("Dynamic expressions", dyn),
                                       reports.num("Apps analysed", len(app_results))])
        finally:
            self.sig_done.emit("usage")

    # ---------------- capacity ----------------
    def _on_capacity(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        self.btn_capacity.setEnabled(False)
        self.shell.busy_begin("Scanning tenant capacity")
        with_orphans = self.chk_cap_orphans.isChecked()
        self.log("Scanning tenant capacity (App reload + Import)"
                 + (" with orphan detection - this can take a while ..." if with_orphans else " ..."))
        threading.Thread(target=self._capacity_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("capacity_report"),
                               with_orphans),
                         daemon=True).start()

    def _capacity_worker(self, tenant, key, out_dir, with_orphans):
        t0 = time.time()
        try:
            res = qcap.fetch_two_capacities(tenant, key, log=self.shell.sig_log.emit,
                                            with_orphans=with_orphans,
                                            should_cancel=self.shell.cancel_requested)
            self.sig_capacity_result.emit(res)
            red = (res.get("app_reload") or {}).get("redundancy", {})
            cons = res.get("consumption") or {}
            dv = cons.get("data_volume")
            if dv:
                used, lim = dv.get("localUsage"), dv.get("capacityLimit")
                over = (used - lim) if (isinstance(used, (int, float)) and isinstance(lim, (int, float))) else None
                flag = (f" OVERAGE (over by {qcap.format_bytes(over)})"
                        if dv.get("overage") and over and over > 0
                        else (" (close to limit)" if dv.get("closeToOverage") else ""))
                self.log(f"  Billed capacity (Data for Analysis): "
                         f"{qcap.format_bytes(used)} / {qcap.format_bytes(lim)}{flag}")
            dups = red.get("duplicate_app_clusters", [])
            if dups:
                t = dups[0]
                self.log(f"  Top duplicated report: '{t['base_name']}' - {t.get('count')} copies "
                         f"across {t.get('space_count')} spaces; "
                         f"{qcap.format_bytes(t.get('dedupe_savings_bytes'))} reclaimable if consolidated.")
            out_path = qcap.write_capacity_report(res, out_dir, self.shell.sig_log.emit)
            self.log("Capacity report finished.")
            red2 = red or {}
            inv = (res.get("app_reload") or {}).get("inventory", {}) or {}
            persum = red2.get("personal_summary", {}) or {}
            dup_bytes = sum(c.get("dedupe_savings_bytes", 0)
                            for c in red2.get("duplicate_app_clusters", []))
            self._record(out_path, "capacity_report", "Capacity report - full tenant",
                         scope=f"{inv.get('totals', {}).get('app_count', 0)} apps",
                         started=t0, headline=[
                             reports.num("Billable app data",
                                         persum.get("billable_bytes", 0), unit="bytes"),
                             reports.num("Duplicate reclaim", dup_bytes, unit="bytes"),
                             reports.num("Duplicate clusters",
                                         len(red2.get("duplicate_app_clusters", []))),
                             reports.num("Apps sized",
                                         inv.get("totals", {}).get("sized_app_count", 0))])
        except qcap.ScanCancelled:
            self.log("Capacity scan cancelled - no report written.")
        except Exception as e:
            self.log(f"ERROR building capacity report: {scrub(key, e)}")
            self.sig_error.emit("Capacity report failed", scrub(key, e))
        finally:
            self.sig_done.emit("capacity")

    def _render_capacity(self, res):
        """Render the scan result into the in-app capacity dashboard and hand it to
        the shell so the Home overview can summarise it too."""
        self.shell.last_capacity = res
        self.shell.last_capacity_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        clear_layout(self.cap_dash)

        ar = res.get("app_reload", {}) or {}
        arr = ar.get("redundancy", {}) or {}
        ai = ar.get("inventory", {}) or {}
        cons = res.get("consumption") or {}
        dv = cons.get("data_volume")
        persum = arr.get("personal_summary", {}) or {}
        orph = (res.get("import") or {}).get("orphans") or {}

        # billed meter gauge
        if dv and isinstance(dv.get("capacityLimit"), (int, float)) and dv.get("capacityLimit"):
            used, lim = dv.get("localUsage") or 0, dv.get("capacityLimit")
            pct = used / lim * 100 if lim else 0
            over = used - lim
            status = (f"OVERAGE by {human_bytes(over)}" if dv.get("overage") and over > 0
                      else ("close to limit" if dv.get("closeToOverage") else "ok"))
            meter = MeterBar(warn_at=90, over_at=100)
            meter.set(pct, f"Data for Analysis (billed):  {human_bytes(used)} / {human_bytes(lim)}",
                      status)
            self.cap_dash.addWidget(meter)
        else:
            note = label("No authoritative billed meter (needs a tenant-admin key) - "
                         "showing in-memory proxy figures below.", "muted", wrap=True)
            self.cap_dash.addWidget(note)

        # KPI row
        dup_reclaim = sum(c.get("dedupe_savings_bytes", 0) for c in arr.get("duplicate_app_clusters", []))
        spaces_billable = [s for s in arr.get("space_usage", []) if s.get("billable")]
        top_space = spaces_billable[0] if spaces_billable else None
        specs = [
            ("Billable app data", human_bytes(persum.get("billable_bytes", 0)),
             f"{persum.get('billable_count', 0)} apps  ·  proxy", TEAL),
            ("Duplicate reclaim", human_bytes(dup_reclaim),
             f"{len(arr.get('duplicate_app_clusters', []))} clusters if consolidated", WARN),
            ("Apps sized", str(ai.get("totals", {}).get("sized_app_count", 0)),
             f"of {ai.get('totals', {}).get('app_count', 0)} apps", TEAL),
        ]
        if top_space:
            specs.append(("Top billable space", top_space["space"],
                          f"{human_bytes(top_space['bytes'])} · {top_space['app_count']} apps", TEAL))
        if orph:
            rc = orph.get("reclaimable", {})
            tot = (rc.get("orphan_file_bytes", 0) + rc.get("produced_only_bytes", 0)
                   + rc.get("orphan_dataset_bytes", 0))
            specs.append(("Reclaimable orphans", human_bytes(tot), "files + datasets", BAD))
        row, _ = kpi_row(specs)
        self.cap_dash.addWidget(row)

        # ranked bars: duplicate reclaim + space usage (names always visible)
        charts = QHBoxLayout()
        dups = arr.get("duplicate_app_clusters", [])[:10]
        if dups:
            charts.addWidget(ranked_bars(
                "Top duplicate reports — reclaim if consolidated",
                [(d["base_name"], d.get("dedupe_savings_bytes", 0)) for d in dups],
                colour=WARN, max_n=10, value_fmt=human_bytes), 1)
        sp = spaces_billable[:10]
        if sp:
            charts.addWidget(ranked_bars(
                "Billable app data by space",
                [(s["space"], s["bytes"]) for s in sp],
                colour=TEAL, max_n=10, value_fmt=human_bytes), 1)
        if dups or sp:
            cw = QWidget()
            cw.setLayout(charts)
            self.cap_dash.addWidget(cw)

        # colour-coded action list (top 60; full detail is in the Excel workbook)
        actions = arr.get("action_list", [])[:60]
        if actions:
            self.cap_dash.addWidget(label("ACTION LIST  (billable reclaim candidates first — verify "
                                          "before deleting; full list in the Excel workbook)", "section"))
            headers = ["App", "Space", "Size (MB)", "Age (d)", "Loads ext?", "Notes"]
            rows = [[a["app"], a.get("space", ""), round((a.get("size_bytes") or 0) / 1048576, 1),
                     a.get("age_days", ""),
                     ("yes" if a.get("loads_external") is True else
                      ("no" if a.get("loads_external") is False else "review")),
                     a.get("notes", "")] for a in actions]

            def tint(_i, r):
                note = (r[5] or "").lower()
                if "archive" in note:
                    return TINT_BAD
                if "stale" in note or "duplicate" in note:
                    return TINT_WARN
                if "0 capacity" in note:
                    return TINT_MUTED
                return None

            self.cap_dash.addWidget(colored_table(headers, rows, row_colour=tint, numeric_cols=(2, 3)))
        self.cap_dash.addStretch(1)
        self.shell.refresh_status()

    # ---------------- consistency dashboard ----------------
    def _render_consistency(self, payload):
        results = payload["results"]
        mnc = results["measure_name_conflicts"]
        mr = results["measure_redundancy"]
        dnc = results["dimension_name_conflicts"]
        dr = results["dimension_redundancy"]
        clear_layout(self.cons_dash)
        self.cons_dash.addWidget(label(
            f"Scanned {payload['n_measures']} measures and {payload['n_dims']} dimensions across "
            f"{payload['n_apps']} apps.", "muted"))
        specs = [
            ("Measure conflicts", str(len(mnc)), "same name, diff. calc", BAD if mnc else GOOD),
            ("Measure redundancy", str(len(mr)), "same calc, diff. names", WARN if mr else GOOD),
            ("Dimension conflicts", str(len(dnc)), "same name, diff. calc", BAD if dnc else GOOD),
            ("Dimension redundancy", str(len(dr)), "same calc, diff. names", WARN if dr else GOOD),
        ]
        row, _ = kpi_row(specs)
        self.cons_dash.addWidget(row)
        if not (mnc or mr or dnc or dr):
            ok = label("No name conflicts or redundancy found across the selected apps. ✓", "muted")
            ok.setStyleSheet(f"color: {GOOD}; font-weight: 600;")
            self.cons_dash.addWidget(ok)
            self.cons_dash.addStretch(1)
            return
        self._conflict_section("Measure name conflicts (same name — differing definitions)", mnc)
        self._redundancy_section("Measure redundancy (same definition — different names)", mr)
        self._conflict_section("Dimension name conflicts (same name — differing definitions)", dnc)
        self._redundancy_section("Dimension redundancy (same definition — different names)", dr)
        self.cons_dash.addStretch(1)

    def _conflict_section(self, title, conflicts):
        if not conflicts:
            return
        self.cons_dash.addWidget(label(title.upper(), "section"))
        rows = []
        for c in conflicts[:40]:
            for i, v in enumerate(c["variants"], 1):
                rows.append([c["name"], f"{i}/{c['variant_count']}", "; ".join(v["apps"]), v["expr"]])
        seen = []

        def tint(_i, r):
            if r[0] not in seen:
                seen.append(r[0])
            return TINT_MUTED if (seen.index(r[0]) % 2) else None

        self.cons_dash.addWidget(colored_table(
            ["Name", "Variant", "Apps", "Definition"], rows, row_colour=tint,
            stretch_col=3, wrap=True))
        if len(conflicts) > 40:
            self.cons_dash.addWidget(label(f"... and {len(conflicts) - 40} more (see the Excel workbook).",
                                           "muted"))

    def _redundancy_section(self, title, red):
        if not red:
            return
        self.cons_dash.addWidget(label(title.upper(), "section"))
        rows = [[r["expr"], len(r["names"]), "; ".join(r["names"]), "; ".join(r["apps"]), r["occurrences"]]
                for r in red[:40]]
        self.cons_dash.addWidget(colored_table(
            ["Definition", "Distinct names", "Names", "Apps", "Occurrences"], rows,
            numeric_cols=(1, 4), stretch_col=0, wrap=True))
        if len(red) > 40:
            self.cons_dash.addWidget(label(f"... and {len(red) - 40} more (see the Excel workbook).",
                                           "muted"))

    # ---------------- usage dashboard ----------------
    def _on_usage_results_q(self, app_results):
        self._usage_app_results = app_results
        self._building_usage_combo = True
        self.cmb_usage_app.clear()
        for ar in app_results:
            self.cmb_usage_app.addItem(ar["title"], ar["guid"])
        self.cmb_usage_app.setEnabled(bool(app_results))
        self._building_usage_combo = False
        self.cmb_usage_app.setCurrentIndex(0)
        self._render_usage_q()

    def _render_usage_q(self):
        if self._building_usage_combo or not self._usage_app_results:
            return
        idx = max(0, self.cmb_usage_app.currentIndex())
        ar = self._usage_app_results[idx if idx < len(self._usage_app_results) else 0]
        res, title = ar["result"], ar["title"]
        dyn = res["dynamic"]
        active = sum(1 for d in dyn if "active" in d["type"])
        master, fields = res["master"], res["fields"]
        tables, variables = res["tables"], res["variables"]
        clear_layout(self.usage_dash_q)
        self.usage_dash_q.addWidget(label(f"App: {title}  ·  candidates only — verify before deleting; "
                                          "full detail in the Excel workbook", "muted", wrap=True))
        specs = [
            ("Unused fields", str(len(fields["unused"])), "candidates", WARN if fields["unused"] else GOOD),
            ("Unused master items", str(len(master["unused"])), "measures/dims",
             WARN if master["unused"] else GOOD),
            ("Unused variables", str(len(variables["unused"])), "front-end only",
             WARN if variables["unused"] else GOOD),
            ("Dynamic $()", str(len(dyn)), f"{active} active $(=...)", BAD if active else TEAL),
        ]
        row, _ = kpi_row(specs)
        self.usage_dash_q.addWidget(row)
        if dyn:
            banner = label(f"⚠ {title} uses {len(dyn)} dynamic $(...) expressions ({active} active "
                           "$(=...)). These can HIDE real usage — review the Dynamic expressions list "
                           "before removing any field, master item or variable.", "muted", wrap=True)
            banner.setStyleSheet(f"color: {BAD}; font-weight: 600;")
            self.usage_dash_q.addWidget(banner)
        if fields["unused"]:
            self.usage_dash_q.addWidget(label("UNUSED FIELDS (candidates)", "section"))
            self.usage_dash_q.addWidget(colored_table(
                ["Field", "Table(s)"], [[r["name"], r["tables"]] for r in fields["unused"][:100]],
                stretch_col=1))
        if master["unused"]:
            self.usage_dash_q.addWidget(label("UNUSED MASTER ITEMS", "section"))
            self.usage_dash_q.addWidget(colored_table(
                ["Type", "Name", "Definition"],
                [[r["kind"], r["name"], r["definition"]] for r in master["unused"][:100]],
                stretch_col=2, wrap=True))
        if variables["unused"]:
            self.usage_dash_q.addWidget(label("UNUSED VARIABLES (front-end)", "section"))
            self.usage_dash_q.addWidget(colored_table(
                ["Variable", "Definition", "Script-created?"],
                [[r["name"], r["definition"], "Yes" if r["is_script_created"] else ""]
                 for r in variables["unused"][:100]], stretch_col=1, wrap=True))
        flagged = [t for t in tables if t["flag"]]
        if flagged:
            self.usage_dash_q.addWidget(label("TABLES — NO DATA FIELDS USED (review — may be link tables)",
                                              "section"))
            self.usage_dash_q.addWidget(colored_table(
                ["Table", "Data fields", "Used data fields", "Flag"],
                [[t["table"], t["data_fields"], t["used_data_fields"], t["flag"]] for t in flagged],
                row_colour=lambda _i, _r: TINT_WARN, numeric_cols=(1, 2), stretch_col=3, wrap=True))
        if dyn:
            self.usage_dash_q.addWidget(label("DYNAMIC EXPRESSIONS (review before deleting)", "section"))
            self.usage_dash_q.addWidget(colored_table(
                ["Location", "Type", "Expression"],
                [[d["location"], d["type"], d["expression"]] for d in dyn[:100]],
                row_colour=lambda _i, r: TINT_BAD if "active" in r[1] else None,
                stretch_col=2, wrap=True))
        self.usage_dash_q.addStretch(1)

    # ---------------- apply (write) ----------------
    MODE_MAP = {"Create + update (sync)": "upsert", "Create only": "create",
                "Update only": "update", "Delete": "delete"}

    def _browse_csv(self, lineedit):
        f, _ = QFileDialog.getOpenFileName(self, "Choose CSV",
                                           self.output_dir or os.path.expanduser("~"),
                                           "CSV files (*.csv)")
        if f:
            lineedit.setText(f)

    def _on_apply(self, dry):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings (used for backups).")
            return
        targets = self.shell.scope_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to apply to.")
            return
        meas_path = self.ed_meas_csv.text().strip()
        dim_path = self.ed_dim_csv.text().strip()
        if not meas_path and not dim_path:
            QMessageBox.warning(self, "No CSV", "Choose a measures and/or dimensions CSV.")
            return
        try:
            meas_rows = [r for r in core.read_csv_rows(meas_path) if r.get("name")] if meas_path else []
            dim_rows = [r for r in core.read_csv_rows(dim_path) if r.get("name")] if dim_path else []
        except Exception as e:
            QMessageBox.critical(self, "CSV error", f"Could not read the CSV:\n{e}")
            return
        if not meas_rows and not dim_rows:
            QMessageBox.warning(self, "Empty CSV", "The chosen CSV has no rows with a 'name'.")
            return
        op = self.cmb_mode.currentText()
        mode = self.MODE_MAP[op]
        if not dry:
            box = QMessageBox(self)
            box.setWindowTitle("Confirm apply")
            if self.shell.icon_path and os.path.exists(self.shell.icon_path):
                box.setWindowIcon(QIcon(self.shell.icon_path))
            box.setIcon(QMessageBox.Critical if mode == "delete" else QMessageBox.Warning)
            if mode == "delete":
                box.setText("DELETE master items - this cannot be undone except via the backup.")
            else:
                box.setText("This will modify and SAVE the selected app(s).")
            box.setInformativeText(f"Operation: {op}\nApps: {len(targets)}\n"
                                   f"Measure rows: {len(meas_rows)}\nDimension rows: {len(dim_rows)}\n\n"
                                   "A backup of current master items is exported first.")
            apply_btn = box.addButton("Apply", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not apply_btn:
                return
        self.btn_apply.setEnabled(False)
        self.shell.busy_begin("Dry run" if dry else "Applying master items")
        self.log(("DRY RUN - " if dry else "") + f"Applying to {len(targets)} app(s) [{op}] ...")
        threading.Thread(target=self._apply_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("apply_master_items"),
                               targets, meas_rows, dim_rows, mode, dry), daemon=True).start()

    def _apply_worker(self, tenant, key, out_dir, targets, meas_rows, dim_rows, mode, dry):
        try:
            for a in targets:
                exp = core.QlikExporter(tenant, key, a["guid"], out_dir, self.shell.sig_log.emit)
                try:
                    exp.connect()
                    app_h = exp.call(-1, "OpenDoc", [a["guid"]])["qReturn"]["qHandle"]
                    title = exp.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", a["guid"])
                    if not dry:
                        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                        bprefix = f"BACKUP_{core.safe(title)}_{core.safe(a['guid'])}_{stamp}"
                        if meas_rows:
                            exp.export_measures(app_h, bprefix)
                        if dim_rows:
                            exp.export_dimensions(app_h, bprefix)
                        self.log(f"  {title}: backup of current master items written.")
                    changed = 0
                    for kind, rows in (("measure", meas_rows), ("dimension", dim_rows)):
                        if not rows:
                            continue
                        c = exp.apply_master(app_h, kind, rows, mode, dry)
                        self.log(f"  {title} {kind}s: {c['created']} created, {c['updated']} updated, "
                                 f"{c['deleted']} deleted, {c['skipped']} skipped.")
                        changed += c["created"] + c["updated"] + c["deleted"]
                    if not dry and changed > 0:
                        exp.do_save(app_h)
                        self.log(f"  {title}: saved ({changed} change(s)).")
                    elif not dry:
                        self.log(f"  {title}: no changes to save.")
                except Exception as e:
                    self.log(f"ERROR applying to {a.get('name', a['guid'])}: {scrub(key, e)}")
                finally:
                    exp.close()
            self.log("DRY RUN complete - nothing was written." if dry else "Apply finished.")
        finally:
            self.sig_done.emit("apply")

    # ---------------- QVD field usage report ----------------
    def _on_qvd_usage_text(self, text):
        self.qvd_panel.setPlainText(text)

    def _on_qvd_usage(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        targets = self.shell.scope_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to scan.")
            return
        self.btn_qvd_usage.setEnabled(False)
        self.shell.busy_begin("Scanning QVD field usage")
        self.log(f"Scanning QVD field usage for {len(targets)} app(s) ...")
        threading.Thread(target=self._qvd_usage_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("field_lineage"), targets,
                               self.chk_qvd_upstream.isChecked()), daemon=True).start()

    def _attach_upstream_chains(self, rows, tenant, key, this_app_guid, chain_cache):
        """Resolve each distinct confirmed QVD's true origin once - cached
        across the whole scan, since the same QVD is often shared by several
        apps/fields - and attach it to every row sourced from that QVD."""
        def open_app(guid, _t=tenant, _k=key):
            ex = core.QlikExporter(_t, _k, guid, self._feature_dir("field_lineage"),
                                   self.shell.sig_log.emit)
            try:
                ex.connect()
                ah = ex.call(-1, "OpenDoc", [guid])["qReturn"]["qHandle"]
                lay = ex.call(ah, "GetAppLayout", [])["qLayout"]
                return {"title": lay.get("qTitle", guid), "reload": lay.get("qLastReloadTime", ""),
                        "script": ex.fetch_script(ah)}
            except Exception as oe:
                self.shell.sig_log.emit(f"  (could not open upstream app: {scrub(key, oe)})")
                return None
            finally:
                ex.close()

        for r in rows:
            if r["status"] not in core.QVD_CONFIRMED_STATUSES:
                continue
            qvd = r["qvd_file"]
            if qvd not in chain_cache:
                # ponytail: cached per QVD, not per (QVD, field) - if the QVD is built
                # from more than one upstream table, every field shows the same chain.
                # Upgrade to a (qvd, field) cache key if that turns out to matter in practice.
                self.shell.sig_log.emit(f"  tracing upstream source for {qvd} ...")
                chain_cache[qvd] = core.resolve_qvd_chain(
                    qvd, r["final_field"], tenant, key, this_app_guid, open_app,
                    log=self.shell.sig_log.emit)
            resolved = chain_cache[qvd]
            r["origin_kind"] = resolved["origin_kind"]
            r["origin_label"] = resolved["origin_label"]
            r["chain"] = resolved["chain"]

    def _qvd_usage_worker(self, tenant, key, out_dir, targets, trace_upstream):
        t0 = time.time()
        app_rows = []
        chain_cache = {}  # qvd_file -> resolved chain dict, shared across all apps in this run
        try:
            for a in targets:
                if self.shell.cancel_requested():
                    self.log("QVD field usage scan cancelled.")
                    break
                exp = core.QlikExporter(tenant, key, a["guid"], out_dir, self.shell.sig_log.emit)
                try:
                    exp.connect()
                    app_h = exp.call(-1, "OpenDoc", [a["guid"]])["qReturn"]["qHandle"]
                    title = exp.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", a["guid"])
                    script = exp.fetch_script(app_h)
                    model_fields = exp.fetch_model_fields(app_h)
                    tables = core.parse_load_tables(script)
                    rows = core.analyze_qvd_field_usage(tables, model_fields)
                    if trace_upstream:
                        self._attach_upstream_chains(rows, tenant, key, a["guid"], chain_cache)
                    app_rows.append({"title": title, "guid": a["guid"], "rows": rows})
                    qvds = {r["qvd_file"] for r in rows}
                    self.log(f"  {title}: {len(qvds)} QVD source(s), {len(rows)} field reference(s)")
                except Exception as e:
                    self.log(f"ERROR scanning {a.get('name', a['guid'])}: {scrub(key, e)}")
                finally:
                    exp.close()
            if self.shell.cancel_requested():
                self.log("QVD field usage scan cancelled - no report written.")
                return
            if not app_rows:
                self.log("No apps scanned.")
                return
            text = core.render_qvd_field_usage_text(app_rows)
            self.sig_qvd_usage_done.emit(text)
            out_path = core.write_qvd_usage_report(app_rows, out_dir, self.shell.sig_log.emit)
            self.log(f"QVD field usage report -> {os.path.basename(out_path)}")
            rows = [r for a in app_rows for r in a["rows"]]
            confirmed = sum(1 for r in rows if r["status"] in core.QVD_CONFIRMED_STATUSES)
            not_found = sum(1 for r in rows if r["status"] == "not_found_in_final_model")
            self._record(out_path, "qvd_field_usage", "QVD field usage",
                         scope=f"{len(app_rows)} apps", started=t0, headline=[
                             reports.num("Fields checked", len(rows)),
                             reports.num("Confirmed in model", confirmed),
                             reports.num("Not found", not_found)])
        except Exception as e:
            self.log(f"ERROR: {scrub(key, e)}")
            self.sig_error.emit("QVD field usage scan failed", scrub(key, e))
        finally:
            self.sig_done.emit("qvd_usage")

    # ---------------- tenant QVD & field usage ----------------
    def _on_tenant_usage_text(self, text):
        self.tenant_usage_panel.setPlainText(text)

    def _on_tenant_usage(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        self.btn_tenant_usage.setEnabled(False)
        self.shell.busy_begin("Scanning tenant QVD & field usage")
        self.log("Scanning tenant QVD & field usage (published apps only) ...")
        threading.Thread(target=self._tenant_usage_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("tenant_usage")),
                         daemon=True).start()

    def _tenant_usage_worker(self, tenant, key, out_dir):
        t0 = time.time()
        def open_app_full(guid, _t=tenant, _k=key, _o=out_dir):
            exp = core.QlikExporter(_t, _k, guid, _o, self.shell.sig_log.emit)
            try:
                exp.connect()
                app_h = exp.call(-1, "OpenDoc", [guid])["qReturn"]["qHandle"]
                title = exp.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", guid)
                model_fields = exp.fetch_model_fields(app_h)
                measures = exp.fetch_measures(app_h)
                dims = exp.fetch_dimensions(app_h)
                variables = exp.fetch_variables(app_h)
                objects = exp.fetch_objects(app_h)
                return {
                    "title": title, "script": exp.fetch_script(app_h), "model_fields": model_fields,
                    "usage_result": core.analyze_usage(measures, dims, variables, objects, model_fields),
                    "space_id": core.get_app_space_id(_t, _k, guid),
                }
            except Exception as e:
                self.shell.sig_log.emit(f"  (could not open {guid}: {scrub(_k, e)})")
                return None
            finally:
                exp.close()

        def fetch_lineage(guid, _t=tenant, _k=key):
            return core.fetch_native_lineage(_t, _k, guid)

        try:
            self.log("Listing published apps ...")
            root_apps = core.list_published_apps(tenant, key)
            self.log(f"  {len(root_apps)} published app(s) found - tracing full lineage "
                     "(this can take a while) ...")
            try:
                qvd_inventory = {b: m for b, m in core.list_data_files(tenant, key).items()
                                 if b.endswith(".qvd")}
            except Exception as e:
                qvd_inventory = {}
                self.log(f"  (data file inventory unavailable: {scrub(key, e)})")
            try:
                space_lookup = core.list_spaces_full(tenant, key)
            except Exception as e:
                space_lookup = {}
                self.log(f"  (space list unavailable: {scrub(key, e)})")

            scan_result = core.scan_tenant_lineage(root_apps, open_app_full, fetch_lineage,
                                                    log=self.shell.sig_log.emit,
                                                    cancel_check=self.shell.cancel_requested)
            app_rows = [{"title": v["title"], "guid": g, "rows": v["rows"], "is_root": v["is_root"],
                        "depth": v["depth"], "space_id": v["space_id"]}
                       for g, v in scan_result.items()]

            if self.shell.cancel_requested():
                self.log("Tenant usage scan cancelled - no report written.")
                return
            if not app_rows:
                self.log("No published apps scanned.")
                return
            qvd_ref_rows = core.cross_reference_qvd_inventory(qvd_inventory, app_rows)
            text = core.render_tenant_qvd_usage_text(app_rows, qvd_ref_rows)
            self.sig_tenant_usage_done.emit(text)
            out_path = core.write_tenant_qvd_usage_report(app_rows, qvd_ref_rows, out_dir,
                                                           self.shell.sig_log.emit,
                                                           space_lookup=space_lookup)
            self.log(f"Tenant QVD & field usage report -> {os.path.basename(out_path)}")
            rows = [r for a in app_rows for r in a["rows"]]
            self._record(out_path, "tenant_usage", "Tenant QVD & field usage",
                         scope=f"{len(app_rows)} published apps", started=t0, headline=[
                             reports.num("Published apps", len(app_rows)),
                             reports.num("QVDs referenced", len(qvd_ref_rows)),
                             reports.num("Field rows", len(rows))])
        except Exception as e:
            self.log(f"ERROR: {scrub(key, e)}")
            self.sig_error.emit("Tenant usage scan failed", scrub(key, e))
        finally:
            self.sig_done.emit("tenant_usage")

    # ---------------- diagnose app visibility ----------------
    def _on_diag_visibility_text(self, text):
        self.diag_visibility_panel.setPlainText(text)

    def _on_diag_visibility(self):
        if self._need_settings():
            return
        guid = self.ed_diag_guid.text().strip()
        if not guid:
            QMessageBox.warning(self, "Missing GUID", "Enter the suspected app's GUID.")
            return
        consumer = self.ed_diag_consumer.text().strip() or None
        self.btn_diag_visibility.setEnabled(False)
        self.shell.busy_begin("Diagnosing app visibility")
        self.log(f"Diagnosing visibility for app {guid} ...")
        threading.Thread(target=self._diag_visibility_worker,
                         args=(self.tenant, self.api_key, guid, consumer), daemon=True).start()

    def _diag_visibility_worker(self, tenant, key, guid, consumer):
        try:
            results = core.diagnose_app_visibility(tenant, key, guid, consumer_guid=consumer,
                                                    log=self.shell.sig_log.emit)
            text = core.render_app_visibility_text(guid, results)
            self.sig_diag_visibility_done.emit(text)
            self.log("Diagnostic complete - see the panel below for the verdict.")
        except Exception as e:
            self.log(f"ERROR: {scrub(key, e)}")
            self.sig_error.emit("Visibility diagnostic failed", scrub(key, e))
        finally:
            self.sig_done.emit("diag_visibility")

    # ---------------- field lineage ----------------
    def _on_fields_loaded(self, names):
        self.cmb_field.clear()
        self.cmb_field.addItems(names)
        self.cmb_field.setCurrentIndex(-1)
        comp = self.cmb_field.completer()
        if comp:
            comp.setFilterMode(Qt.MatchContains)
            comp.setCompletionMode(QCompleter.PopupCompletion)

    def _on_trace_done(self, text, html_path):
        self.lineage_panel.setPlainText(text)
        if html_path:
            self.log(f"Lineage HTML -> {os.path.basename(html_path)}")

    def _single_target(self):
        targets = self.shell.scope_targets()
        if len(targets) != 1:
            QMessageBox.warning(self, "Select one app",
                                "Change the scope to exactly one app to trace a field.")
            return None
        return targets[0]

    def _on_load_fields(self):
        if self._need_settings():
            return
        app = self._single_target()
        if not app:
            return
        self.btn_load_fields.setEnabled(False)
        self.shell.busy_begin("Loading fields")
        self.log(f"Loading fields for {app.get('name', app['guid'])} ...")
        threading.Thread(target=self._fields_worker,
                         args=(self.tenant, self.api_key, app), daemon=True).start()

    def _fields_worker(self, tenant, key, app):
        try:
            exp = core.QlikExporter(tenant, key, app["guid"],
                                    self.output_dir or os.path.expanduser("~"), self.shell.sig_log.emit)
            try:
                exp.connect()
                app_h = exp.call(-1, "OpenDoc", [app["guid"]])["qReturn"]["qHandle"]
                fields = exp.fetch_model_fields(app_h)
                names = sorted({f["name"] for f in fields if not f["is_system"]}, key=str.lower)
                self.sig_fields_loaded.emit(names)
                self.shell.sig_log.emit(f"Loaded {len(names)} fields - pick one and click Trace.")
            finally:
                exp.close()
        except Exception as e:
            self.shell.sig_log.emit(f"ERROR loading fields: {scrub(key, e)}")
            self.sig_error.emit("Load fields failed", scrub(key, e))
        finally:
            self.sig_done.emit("lineage_fields")

    def _on_trace(self):
        field = self.cmb_field.currentText().strip()
        if not field:
            QMessageBox.warning(self, "Pick a field", "Load fields and choose one to trace.")
            return
        if self._need_settings():
            return
        app = self._single_target()
        if not app:
            return
        self.btn_trace.setEnabled(False)
        self.shell.busy_begin("Tracing lineage")
        self.log(f"Tracing lineage for '{field}' ...")
        threading.Thread(target=self._trace_worker,
                         args=(self.tenant, self.api_key, self._feature_dir("field_lineage"), app,
                               field, self.chk_native.isChecked()), daemon=True).start()

    def _trace_worker(self, tenant, key, out_dir, app, field, use_native):
        try:
            exp = core.QlikExporter(tenant, key, app["guid"],
                                    out_dir or os.path.expanduser("~"), self.shell.sig_log.emit)
            try:
                exp.connect()
                app_h = exp.call(-1, "OpenDoc", [app["guid"]])["qReturn"]["qHandle"]
                layout = exp.call(app_h, "GetAppLayout", [])["qLayout"]
                title = layout.get("qTitle", app["guid"])
                app_reload = layout.get("qLastReloadTime", "")
                script = exp.fetch_script(app_h)

                tables = core.parse_load_tables(script)
                pipe = core.trace_field_pipeline(field, tables)
                if pipe.get("found"):
                    steps = len(pipe.get("steps", []))
                    src = sorted(pipe.get("external_sources") or [])
                    self.shell.sig_log.emit(f"  Pipeline: {steps} step(s); "
                                            f"origin = {', '.join(src) if src else 'in-app source'}")
                    space_cache, user_cache = {}, {}
                    this_meta = {"space": app.get("space_name", ""), "owner": "", "reload": app_reload}
                    try:
                        m = core.app_meta(tenant, key, app["guid"], space_cache, user_cache)
                        this_meta["owner"] = m.get("owner", "")
                        if not this_meta["space"]:
                            this_meta["space"] = m.get("space", "")
                        if not this_meta["reload"]:
                            this_meta["reload"] = m.get("reload", "")
                    except Exception as me:
                        self.shell.sig_log.emit(f"  (app details unavailable: {scrub(key, me)})")

                    hops = []
                    if use_native and pipe.get("external_sources"):
                        try:
                            graph = core.fetch_native_lineage(tenant, key, app["guid"])
                            self.shell.sig_log.emit(f"  Qlik lineage: {len(graph.get('nodes', {}))} nodes, "
                                                    f"{len(graph.get('edges', []))} edges")

                            def open_app(guid, _t=tenant, _k=key, _o=out_dir):
                                ex = core.QlikExporter(_t, _k, guid,
                                                       _o or os.path.expanduser("~"), self.shell.sig_log.emit)
                                try:
                                    ex.connect()
                                    ah = ex.call(-1, "OpenDoc", [guid])["qReturn"]["qHandle"]
                                    lay = ex.call(ah, "GetAppLayout", [])["qLayout"]
                                    return {"title": lay.get("qTitle", guid),
                                            "reload": lay.get("qLastReloadTime", ""),
                                            "script": ex.fetch_script(ah)}
                                except Exception as oe:
                                    self.shell.sig_log.emit(f"  (could not open upstream app: {scrub(_k, oe)})")
                                    return None
                                finally:
                                    ex.close()

                            def meta_fn(guid, _t=tenant, _k=key):
                                try:
                                    return core.app_meta(_t, _k, guid, space_cache, user_cache)
                                except Exception:
                                    return {}

                            hops = core.trace_upstream_apps(
                                graph, pipe["external_sources"], pipe.get("source_field") or field,
                                open_app, meta_fn, self.shell.sig_log.emit)
                            if not hops:
                                self.shell.sig_log.emit("  (no upstream producing app matched this source "
                                                        "in Qlik lineage)")
                        except Exception as ne:
                            self.shell.sig_log.emit(f"  (Qlik native lineage unavailable: {scrub(key, ne)})")

                    nodes = core.assemble_pipeline_nodes(pipe, title, this_meta, hops)
                    text = core.render_field_pipeline_text(pipe, title, nodes=nodes)
                    html_path = (core.write_field_pipeline_graph_html(
                        pipe, title, app["guid"], out_dir, nodes=nodes) if out_dir else "")
                    self.sig_trace_done.emit(text, html_path)
                    return

                self.shell.sig_log.emit("  (could not resolve the field in the load script "
                                        "- showing the field-usage trace instead)")
                mf = exp.fetch_model_fields(app_h)
                measures = exp.fetch_measures(app_h)
                dims = exp.fetch_dimensions(app_h)
                variables = exp.fetch_variables(app_h)
                objects = exp.fetch_objects(app_h)
                try:
                    lineage = exp.fetch_lineage(app_h)
                except Exception as le:
                    lineage = []
                    self.shell.sig_log.emit(f"  (GetLineage unavailable: {scrub(key, le)})")
                try:
                    file_map = core.list_data_files(tenant, key)
                except Exception as fe:
                    file_map = {}
                    self.shell.sig_log.emit(f"  (source-file dates unavailable: {scrub(key, fe)})")
                tr = core.trace_field_lineage(field, mf, lineage, measures, dims, variables, objects)
                if tr.get("found"):
                    core.enrich_lineage_freshness(tr, app_reload, file_map)
                    core.attach_upstream(tr, self._producer_map)
                text = core.render_field_lineage_text(tr, title)
                html_path = ""
                if out_dir and tr.get("found"):
                    html_path = core.write_field_lineage_html(tr, title, app["guid"], out_dir)
                self.sig_trace_done.emit(text, html_path)
            finally:
                exp.close()
        except Exception as e:
            self.shell.sig_log.emit(f"ERROR tracing: {scrub(key, e)}")
            self.sig_error.emit("Trace failed", scrub(key, e))
        finally:
            self.sig_done.emit("lineage")

    # ---------------- cross-app lineage index ----------------
    def _on_index_built(self, count, pm):
        self._producer_map = pm
        self.lbl_index.setText(f"Cross-app index: {count} apps scanned, {len(pm)} QVDs mapped")

    def _on_build_index(self):
        if self._need_settings():
            return
        if not self.shell.apps:
            QMessageBox.warning(self, "Load apps", "Load apps first - the index scans the loaded apps.")
            return
        self.btn_index.setEnabled(False)
        self.shell.busy_begin("Building cross-app index")
        self.log(f"Building cross-app lineage index over {len(self.shell.apps)} loaded app(s) - "
                 "this can take a while ...")
        threading.Thread(target=self._index_worker,
                         args=(self.tenant, self.api_key, list(self.shell.apps)), daemon=True).start()

    def _index_worker(self, tenant, key, apps):
        index = []
        try:
            for a in apps:
                if self.shell.cancel_requested():
                    self.shell.sig_log.emit("Index build cancelled (partial index).")
                    break
                try:
                    exp = core.QlikExporter(tenant, key, a["guid"],
                                            self.output_dir or os.path.expanduser("~"), self.shell.sig_log.emit)
                    try:
                        exp.connect()
                        app_h = exp.call(-1, "OpenDoc", [a["guid"]])["qReturn"]["qHandle"]
                        layout = exp.call(app_h, "GetAppLayout", [])["qLayout"]
                        script = exp.fetch_script(app_h)
                        stores, reads = core.parse_store_reads(script)
                        index.append({"guid": a["guid"], "name": a.get("name", ""),
                                      "reload": layout.get("qLastReloadTime", ""),
                                      "stores": sorted(stores), "reads": sorted(reads)})
                    finally:
                        exp.close()
                except Exception as e:
                    self.shell.sig_log.emit(f"  (skipped {a.get('name', a['guid'])}: {scrub(key, e)})")
            pm = core.build_producer_map(index)
            producers = sum(1 for app in index if app["stores"])
            self.sig_index_built.emit(len(index), pm)
            self.shell.sig_log.emit(f"Index built: {len(index)} apps scanned, {producers} produce QVDs, "
                                    f"{len(pm)} QVDs mapped. Now trace a field to see the upstream chain.")
        finally:
            self.sig_done.emit("index")
