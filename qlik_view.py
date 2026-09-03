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
import datetime
import threading

from PySide6.QtCore import Qt, Signal, QSize, QEvent
from PySide6.QtGui import QIcon, QColor, QBrush
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QPlainTextEdit, QFileDialog, QMessageBox,
    QFrame, QScrollArea, QComboBox, QCompleter, QSplitter,
)

import qlik_core as core
import qlik_capacity as qcap
from widgets import (
    TEAL, BAD, WARN, GOOD, MUTED, ROW_HOVER, make_card, label, ElidedLabel,
    key_format_ok, scrub, friendly_load_error, human_bytes,
    KpiCard, MeterBar, kpi_row, ranked_bars, colored_table, clear_layout,
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
    sig_index_built = Signal(int, object)
    sig_capacity_result = Signal(object)
    sig_consistency_result = Signal(object)
    sig_usage_result_q = Signal(object)

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.apps = []
        self._selected = set()
        self._loaded_sig = None
        self._building = False
        self._hover_row = -1
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

    def log(self, msg):
        self.shell.log(msg)

    # ---------------- layout ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(6)
        split.setChildrenCollapsible(False)
        sel = self._build_selection_card()
        sel.setMinimumHeight(260)        # keep the app table + actions row from collapsing
        tabs = self._build_tabs()
        tabs.setMinimumHeight(220)
        split.addWidget(sel)
        split.addWidget(tabs)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 3)
        split.setSizes([340, 420])
        root.addWidget(split, 1)

    def _search_box(self, placeholder, on_change):
        f = QFrame()
        f.setObjectName("search")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(8, 0, 6, 0)
        lay.setSpacing(2)
        lay.addWidget(QLabel("\U0001F50D"))
        ed = QLineEdit()
        ed.setPlaceholderText(placeholder)
        ed.textChanged.connect(on_change)
        lay.addWidget(ed)
        return f, ed

    def _build_selection_card(self):
        card = make_card()
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 14, 14, 14)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(10)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 1)

        left = QVBoxLayout()
        left.setSpacing(8)
        srow = QHBoxLayout()
        srow.setSpacing(10)
        appbox, self.ed_app = self._search_box("Find app", lambda _t: self._rebuild_table())
        spbox, self.ed_space = self._search_box("Find space", lambda _t: self._rebuild_table())
        srow.addWidget(appbox, 1)
        srow.addWidget(spbox, 1)
        self.btn_load = QPushButton("Load apps")
        self.btn_load.setObjectName("ghost")
        self.btn_load.clicked.connect(self._on_load_apps)
        srow.addWidget(self.btn_load, 0)
        left.addLayout(srow)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Apps", "Space"])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setMinimumHeight(180)
        self.table.setMouseTracking(True)
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.cellEntered.connect(self._on_cell_entered)
        self.table.viewport().installEventFilter(self)
        left.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.btn_all = QPushButton("Select all shown")
        self.btn_all.setObjectName("accent")
        self.btn_all.clicked.connect(self._select_all_shown)
        actions.addWidget(self.btn_all)
        actions.addStretch(1)
        self.lbl_count = QLabel("0 selected")
        self.lbl_count.setObjectName("muted")
        actions.addWidget(self.lbl_count)
        left.addLayout(actions)
        grid.addLayout(left, 0, 0)

        right = QVBoxLayout()
        right.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(label("SELECTED APPS", "section"))
        head.addStretch(1)
        b_clear = QPushButton("Clear all")
        b_clear.setObjectName("ghost")
        b_clear.clicked.connect(self._clear_all)
        head.addWidget(b_clear)
        right.addLayout(head)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QFrame()
        holder.setObjectName("card")
        self.chip_layout = QVBoxLayout(holder)
        self.chip_layout.setContentsMargins(8, 8, 8, 8)
        self.chip_layout.setSpacing(6)
        self.chip_layout.addStretch(1)
        scroll.setWidget(holder)
        right.addWidget(scroll, 1)
        grid.addLayout(right, 0, 1)
        return card

    def _build_tabs(self):
        tabs = QTabWidget()

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
        brow = QHBoxLayout()
        self.btn_run = QPushButton("Run export")
        self.btn_run.setObjectName("accent")
        self.btn_run.clicked.connect(self._on_run)
        brow.addWidget(self.btn_run)
        brow.addStretch(1)
        xl.addLayout(brow)
        xl.addStretch(1)
        tabs.addTab(tab_x, "Extract metadata")

        # Comparison
        tab_c = QWidget()
        cl = QVBoxLayout(tab_c)
        cl.setContentsMargins(0, 10, 0, 0)
        cc = make_card()
        ccl = QVBoxLayout(cc)
        ccl.addWidget(label("CROSS-APP CONSISTENCY  (measures & dimensions)", "section"))
        ccl.addWidget(label("Select 2+ apps in the list above, then run the analysis.", "muted"))
        cl.addWidget(cc)
        crow = QHBoxLayout()
        self.btn_analyze = QPushButton("Analyze consistency")
        self.btn_analyze.setObjectName("accent")
        self.btn_analyze.clicked.connect(self._on_analyze)
        crow.addWidget(self.btn_analyze)
        crow.addStretch(1)
        cl.addLayout(crow)
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
        tabs.addTab(tab_c, "Comparison analysis")

        # Usage
        tab_u = QWidget()
        ul = QVBoxLayout(tab_u)
        ul.setContentsMargins(0, 10, 0, 0)
        uc = make_card()
        ucl = QVBoxLayout(uc)
        ucl.addWidget(label("USAGE & LEANNESS  (what is NOT used)", "section"))
        ucl.addWidget(label("For each selected app: flags unused master items, model fields, tables "
                            "and variables.", "muted"))
        warn = label("Results are CANDIDATES - verify before deleting. Dynamic $(=...) expressions can "
                     "hide usage; the report lists them to review manually.", "muted", wrap=True)
        warn.setStyleSheet(f"color: {BAD};")
        ucl.addWidget(warn)
        ul.addWidget(uc)
        urow = QHBoxLayout()
        self.btn_usage = QPushButton("Analyze usage")
        self.btn_usage.setObjectName("accent")
        self.btn_usage.clicked.connect(self._on_usage)
        urow.addWidget(self.btn_usage)
        urow.addStretch(1)
        ul.addLayout(urow)
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
        tabs.addTab(tab_u, "Usage analysis")

        # Apply (WRITE)
        tab_a = QWidget()
        al = QVBoxLayout(tab_a)
        al.setContentsMargins(0, 10, 0, 0)
        ac = make_card()
        acl = QVBoxLayout(ac)
        acl.addWidget(label("APPLY MASTER ITEMS  (create / update / delete - measures & dimensions)", "section"))
        awarn = label("This WRITES to and SAVES the selected app(s). A backup of the current master "
                      "items is exported first. Use Dry run to preview with no changes.", "muted", wrap=True)
        awarn.setStyleSheet(f"color: {BAD};")
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
        self.chk_dry = QCheckBox("Dry run (preview only - writes nothing)")
        self.chk_dry.setChecked(True)
        acl.addWidget(self.chk_dry)
        al.addWidget(ac)
        arow = QHBoxLayout()
        self.btn_apply = QPushButton("Apply to selected app(s)")
        self.btn_apply.setObjectName("accent")
        self.btn_apply.clicked.connect(self._on_apply)
        arow.addWidget(self.btn_apply)
        arow.addStretch(1)
        al.addLayout(arow)
        al.addStretch(1)
        tabs.addTab(tab_a, "Apply master items")

        # Field lineage
        tab_l = QWidget()
        ll = QVBoxLayout(tab_l)
        ll.setContentsMargins(0, 10, 0, 0)
        lc = make_card()
        lcl = QVBoxLayout(lc)
        lcl.addWidget(label("QVD FIELD USAGE REPORT  (for each selected app: which QVDs it reads, "
                            "and which fields in them are confirmed present in the final data model)",
                            "section"))
        lcl.addWidget(label("Select one or more apps above, then click 'Scan QVD field usage'. Writes "
                            "one combined Excel workbook (qvd_field_usage_*.xlsx) and shows a summary "
                            "below - treat 'not found' fields as a prioritized worklist, not a verdict.",
                            "muted", wrap=True))
        qrow = QHBoxLayout()
        self.btn_qvd_usage = QPushButton("Scan QVD field usage")
        self.btn_qvd_usage.setObjectName("accent")
        self.btn_qvd_usage.clicked.connect(self._on_qvd_usage)
        qrow.addWidget(self.btn_qvd_usage)
        qrow.addStretch(1)
        lcl.addLayout(qrow)

        lcl.addWidget(label("FIELD LINEAGE  (the pipeline a field took INTO this app)", "section"))
        lcl.addWidget(label("Select exactly ONE app above, click 'Load fields', pick a field, then 'Trace'.",
                            "muted", wrap=True))
        irow = QHBoxLayout()
        self.btn_index = QPushButton("Build cross-app index")
        self.btn_index.setObjectName("ghost")
        self.btn_index.clicked.connect(self._on_build_index)
        irow.addWidget(self.btn_index)
        self.lbl_index = QLabel("Cross-app index: not built (only used by the fallback trace)")
        self.lbl_index.setObjectName("muted")
        irow.addWidget(self.lbl_index, 1)
        lcl.addLayout(irow)
        self.chk_native = QCheckBox("Add upstream apps from Qlik's own lineage "
                                    "(extends the pipeline back into the apps that produce the source)")
        self.chk_native.setChecked(True)
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
        frow.addWidget(self.cmb_field, 1)
        self.btn_trace = QPushButton("Trace lineage")
        self.btn_trace.setObjectName("accent")
        self.btn_trace.clicked.connect(self._on_trace)
        frow.addWidget(self.btn_trace)
        lcl.addLayout(frow)
        ll.addWidget(lc)
        self.lineage_panel = QPlainTextEdit()
        self.lineage_panel.setReadOnly(True)
        self.lineage_panel.setMinimumHeight(150)
        ll.addWidget(self.lineage_panel, 1)
        tabs.addTab(tab_l, "Field lineage")

        # Capacity (controls + dashboard)
        tab_cap = QWidget()
        capl = QVBoxLayout(tab_cap)
        capl.setContentsMargins(0, 10, 0, 0)
        capc = make_card()
        capcl = QVBoxLayout(capc)
        capcl.addWidget(label("CAPACITY REPORT  (App reload + Import)", "section"))
        capcl.addWidget(label("Scans every app's data-model size and every imported dataset / data file "
                              "across the whole tenant, ranks the biggest savings, shows the result below "
                              "and writes one Excel workbook. No app selection needed.", "muted", wrap=True))
        self.chk_cap_orphans = QCheckBox("Include orphan scan (reads every app's load script to flag "
                                         "imports no app uses - slower)")
        capcl.addWidget(self.chk_cap_orphans)
        caprow = QHBoxLayout()
        self.btn_capacity = QPushButton("Scan & export capacity report")
        self.btn_capacity.setObjectName("accent")
        self.btn_capacity.clicked.connect(self._on_capacity)
        caprow.addWidget(self.btn_capacity)
        caprow.addStretch(1)
        capcl.addLayout(caprow)
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
        tabs.addTab(tab_cap, "Capacity report")
        return tabs

    def _set_all_exports(self, on):
        for cb in self.checks.values():
            cb.setChecked(on)

    # ---------------- table / selection ----------------
    def _rebuild_table(self):
        qa = self.ed_app.text().strip().lower()
        qs = self.ed_space.text().strip().lower()
        self._building = True
        self._hover_row = -1
        self.table.setRowCount(0)
        for a in self.apps:
            if qa and qa not in (a["name"] or "").lower():
                continue
            if qs and qs not in (a["space_name"] or "").lower():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            it = QTableWidgetItem(a["name"])
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if a["guid"] in self._selected else Qt.Unchecked)
            it.setData(Qt.UserRole, a["guid"])
            sp = QTableWidgetItem(a["space_name"])
            sp.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, it)
            self.table.setItem(row, 1, sp)
        self._building = False
        self._update_count()

    def _on_cell_clicked(self, row, _col):
        it = self.table.item(row, 0)
        if not it:
            return
        guid = it.data(Qt.UserRole)
        self._set_selected(guid, guid not in self._selected)

    def _on_cell_entered(self, row, _col):
        if row == self._hover_row:
            return
        self._set_row_bg(self._hover_row, None)
        self._set_row_bg(row, QColor(ROW_HOVER))
        self._hover_row = row

    def _set_row_bg(self, row, color):
        if row < 0 or row >= self.table.rowCount():
            return
        brush = QBrush(color) if color is not None else QBrush()
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            if it:
                it.setBackground(brush)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.Leave:
            self._set_row_bg(self._hover_row, None)
            self._hover_row = -1
        return super().eventFilter(obj, event)

    def _set_selected(self, guid, on):
        if on:
            self._selected.add(guid)
        else:
            self._selected.discard(guid)
        self._building = True
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            if it and it.data(Qt.UserRole) == guid:
                it.setCheckState(Qt.Checked if on else Qt.Unchecked)
                break
        self._building = False
        self._rebuild_chips()
        self._update_count()

    def _select_all_shown(self):
        self._building = True
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            it.setCheckState(Qt.Checked)
            self._selected.add(it.data(Qt.UserRole))
        self._building = False
        self._rebuild_chips()
        self._update_count()

    def _clear_all(self):
        self._selected.clear()
        self._building = True
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)
        self._building = False
        self._rebuild_chips()
        self._update_count()

    def _make_chip(self, name, guid):
        chip = QFrame()
        chip.setObjectName("chip")
        lay = QHBoxLayout(chip)
        lay.setContentsMargins(10, 4, 6, 4)
        lay.setSpacing(6)
        lay.addWidget(ElidedLabel(name), 1)
        x = QPushButton("✕")
        x.setObjectName("chipx")
        x.setFixedSize(QSize(18, 18))
        x.setCursor(Qt.PointingHandCursor)
        x.clicked.connect(lambda: self._set_selected(guid, False))
        lay.addWidget(x, 0)
        return chip

    def _rebuild_chips(self):
        while self.chip_layout.count():
            item = self.chip_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        by = {a["guid"]: a for a in self.apps}
        for g in sorted(self._selected, key=lambda x: (by.get(x, {}).get("name", "") or "").lower()):
            a = by.get(g)
            if a:
                self.chip_layout.addWidget(self._make_chip(a["name"], g))
        self.chip_layout.addStretch(1)

    def _update_count(self):
        self.lbl_count.setText(f"{len(self._selected)} selected")

    def _selected_targets(self):
        by = {a["guid"]: a for a in self.apps}
        return [by[g] for g in self._selected if g in by]

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
            if not self.apps or sig != self._loaded_sig:
                self._on_load_apps()

    # ---------------- load apps ----------------
    def _on_load_apps(self):
        if self._need_settings():
            return
        self.btn_load.setEnabled(False)
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
        self.apps = apps
        self._loaded_sig = (self.tenant, self.api_key)
        self._selected.clear()
        self.ed_app.clear()
        self.ed_space.clear()
        self._rebuild_table()
        self._rebuild_chips()
        spaces = len({a["space_name"] for a in apps})
        self.log(f"Loaded {len(apps)} apps across {spaces} spaces.")
        self.shell.refresh_status()

    def _on_load_failed(self, msg):
        self.log(f"Could not load apps: {msg}")
        QMessageBox.critical(self, "Load apps failed", msg)

    def _on_worker_done(self, which):
        self.shell.busy_end()
        if which == "load":
            self.btn_load.setEnabled(bool(self.tenant and self.api_key.strip()))
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
        elif which == "index":
            self.btn_index.setEnabled(True)

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
        targets = self._selected_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to export.")
            return
        self.btn_run.setEnabled(False)
        self.shell.busy_begin("Exporting metadata")
        self.log(f"Exporting {len(targets)} app(s) ...")
        flags = tuple(self.checks[k].isChecked()
                      for k in ("measures", "dimensions", "variables", "script", "visuals"))
        threading.Thread(target=self._export_worker,
                         args=(self.tenant, self.api_key, self.output_dir, targets, flags),
                         daemon=True).start()

    def _export_worker(self, tenant, key, out_dir, targets, flags):
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
                finally:
                    exporter.close()
            self.log("All exports finished.")
        finally:
            self.sig_done.emit("run")

    # ---------------- analyze ----------------
    def _on_analyze(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        targets = self._selected_targets()
        if len(targets) < 2:
            QMessageBox.warning(self, "Select apps", "Select at least 2 apps in the list to compare.")
            return
        self.btn_analyze.setEnabled(False)
        self.shell.busy_begin("Analyzing consistency")
        self.log(f"Analyzing {len(targets)} app(s) for measure/dimension consistency ...")
        threading.Thread(target=self._analyze_worker,
                         args=(self.tenant, self.api_key, self.output_dir, targets),
                         daemon=True).start()

    def _analyze_worker(self, tenant, key, out_dir, targets):
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
        targets = self._selected_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to analyze.")
            return
        self.btn_usage.setEnabled(False)
        self.shell.busy_begin("Analyzing usage")
        self.log(f"Analyzing usage for {len(targets)} app(s) ...")
        threading.Thread(target=self._usage_worker,
                         args=(self.tenant, self.api_key, self.output_dir, targets),
                         daemon=True).start()

    def _usage_worker(self, tenant, key, out_dir, targets):
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
                         args=(self.tenant, self.api_key, self.output_dir, with_orphans),
                         daemon=True).start()

    def _capacity_worker(self, tenant, key, out_dir, with_orphans):
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
            qcap.write_capacity_report(res, out_dir, self.shell.sig_log.emit)
            self.log("Capacity report finished.")
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

    def _on_apply(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings (used for backups).")
            return
        targets = self._selected_targets()
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
        dry = self.chk_dry.isChecked()
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
                         args=(self.tenant, self.api_key, self.output_dir, targets,
                               meas_rows, dim_rows, mode, dry), daemon=True).start()

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
        self.lineage_panel.setPlainText(text)

    def _on_qvd_usage(self):
        if self._need_settings():
            return
        if not self.output_dir:
            QMessageBox.warning(self, "Missing settings", "Set an output folder in Settings.")
            return
        targets = self._selected_targets()
        if not targets:
            QMessageBox.warning(self, "Select apps", "Select one or more apps in the list to scan.")
            return
        self.btn_qvd_usage.setEnabled(False)
        self.shell.busy_begin("Scanning QVD field usage")
        self.log(f"Scanning QVD field usage for {len(targets)} app(s) ...")
        threading.Thread(target=self._qvd_usage_worker,
                         args=(self.tenant, self.api_key, self.output_dir, targets), daemon=True).start()

    def _qvd_usage_worker(self, tenant, key, out_dir, targets):
        app_rows = []
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
        except Exception as e:
            self.log(f"ERROR: {scrub(key, e)}")
            self.sig_error.emit("QVD field usage scan failed", scrub(key, e))
        finally:
            self.sig_done.emit("qvd_usage")

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
        targets = self._selected_targets()
        if len(targets) != 1:
            QMessageBox.warning(self, "Select one app",
                                "Select exactly one app in the list above for lineage.")
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
                         args=(self.tenant, self.api_key, self.output_dir, app, field,
                               self.chk_native.isChecked()), daemon=True).start()

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
        if not self.apps:
            QMessageBox.warning(self, "Load apps", "Load apps first - the index scans the loaded apps.")
            return
        self.btn_index.setEnabled(False)
        self.shell.busy_begin("Building cross-app index")
        self.log(f"Building cross-app lineage index over {len(self.apps)} loaded app(s) - "
                 "this can take a while ...")
        threading.Thread(target=self._index_worker,
                         args=(self.tenant, self.api_key, list(self.apps)), daemon=True).start()

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
