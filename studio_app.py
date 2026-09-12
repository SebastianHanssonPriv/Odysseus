"""Bufab BI Governance Studio - unified desktop shell.

One window over two products: Qlik Cloud governance and Power BI usage. A left
nav rail switches between a Home overview, the Qlik workspace and the Power BI
workspace; the header, status line, busy indicator, LOG panel and Settings are
shared, but each product has its OWN output folder (set independently in
Settings), and within it every feature writes to its own subfolder. Secrets
(Qlik API key, Power BI client secret) are held in memory only and never
written to disk.

Entry point: main().
"""
from __future__ import annotations

import os
import sys
import json
import threading

import sharepoint
import qlik_core as core

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QMessageBox,
    QFrame, QButtonGroup, QStackedWidget, QPlainTextEdit,
)

from widgets import (
    STYLE, RAIL, RAIL_FG, FONT_HEAD, RunCard,
    make_card, label, load_fonts, app_font,
)
from qlik_view import QlikView
from powerbi_view import PowerBIView
from reports_view import ReportsView
from settings_view import SettingsView, PBI_AUTH_MODES
from home_view import HomeView

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".bufab_bi_studio.json")

if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "app_icon.ico")
HEADER_LOGO = os.path.join(BASE_DIR, "bufab_header.png")

PBI_AUTH_MODES = ["Client secret (in-memory)", "Key Vault", "Managed identity"]

# Layout constants for the width breakpoints (REDESIGN_SPEC.md).
NAV_WIDTH = 184
NAV_WIDTH_WIDE = 220
CONTENT_MAX_WIDTH = 1600


class MainWindow(QMainWindow):
    sig_log = Signal(str)
    sig_reports_changed = Signal()
    sig_scope_changed = Signal()
    sig_run = Signal(object)         # ("step"|"progress"|"detail"|"finish", payload)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Bufab BI Governance Studio")
        self.resize(1040, 920)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self.icon_path = ICON_PATH

        # shared state
        self.tenant = ""
        self.api_key = ""            # in memory only
        self.output_dir = ""         # the library: one root for everything written
        self.library_url = ""        # the SharePoint address that folder syncs
        # Scope is global and persistent (REDESIGN_SPEC.md, structural change
        # 2): one selection every Qlik task reads, edited only by ScopeSheet.
        # Parsed load-script facts, shared by every tenant-wide Qlik scan for
        # the session and keyed on each app's reload time (see script_cache).
        # About 1 MB for a whole tenant; dropped when the tenant changes.
        self.script_facts = {}
        # The tenant's data-file inventory: {basename: modified date}. Filled
        # once per session by data_file_map() because it now costs one call per
        # space rather than one call in total, and three features want it.
        self.data_files = None
        self.apps = []               # the loaded Qlik app list
        self.scope = set()           # selected app GUIDs
        self.pbi = {"tenant_id": "", "client_id": "", "auth_mode": PBI_AUTH_MODES[0],
                    "key_vault_url": "", "key_vault_secret_name": ""}
        self.pbi_secret = ""         # in memory only
        self.missing_fonts = []      # filled by main(), shown on the About page
        self.last_capacity = None
        self.last_pbi_usage = None
        self.last_capacity_at = ""
        self.last_pbi_at = ""

        # busy indicator state
        self._busy_ops = 0
        self._busy_msg = ""
        self._busy_secs = 0
        self._cancel = threading.Event()     # cooperative cancel for long workers
        self._busy_timer = QTimer(self)
        self._busy_timer.setInterval(1000)
        self._busy_timer.timeout.connect(self._busy_tick)

        self.sig_log.connect(self._append_log)
        self.sig_reports_changed.connect(self._on_reports_changed)
        self.sig_scope_changed.connect(self._on_scope_changed)
        self.sig_run.connect(self._on_run_event)

        self._build()
        self._load_settings()
        self.refresh_status()

    # ---------------- layout ----------------
    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        main = QHBoxLayout()
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)
        root.addLayout(main, 1)

        self.nav = self._build_nav()
        main.addWidget(self.nav)

        content = QWidget()
        content.setMaximumWidth(CONTENT_MAX_WIDTH)
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 12, 16, 16)
        cl.setSpacing(12)
        # Centre the content and cap it, so an ultrawide monitor does not
        # stretch the tables (REDESIGN_SPEC.md, 'Breakpoints'). The lopsided
        # stretch factors give the content everything up to its maximum width
        # and only then split what is left between the two margins.
        main.addStretch(1)
        main.addWidget(content, 1000)
        main.addStretch(1)

        # status line, then the run card (hidden until something runs)
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        cl.addWidget(self.lbl_status)
        self.run_card = RunCard(self._on_cancel)
        cl.addWidget(self.run_card)

        # stacked workspaces
        self.stack = QStackedWidget()
        self.home_view = HomeView(self)
        self.qlik_view = QlikView(self)
        self.powerbi_view = PowerBIView(self)
        self.stack.addWidget(self.home_view)       # 0
        self.stack.addWidget(self.qlik_view)       # 1
        self.reports_view = ReportsView(self)
        self.settings_view = SettingsView(self)
        self.stack.addWidget(self.powerbi_view)    # 2
        self.stack.addWidget(self.reports_view)    # 3
        self.stack.addWidget(self.settings_view)   # 4
        cl.addWidget(self.stack, 1)

        cl.addWidget(self._build_log_card())
        self.go_to("home")
        self._apply_breakpoint(self.width())

    def _build_header(self):
        head = QFrame()
        head.setStyleSheet(f"background: {RAIL};")
        head.setFixedHeight(76)
        lay = QHBoxLayout(head)
        lay.setContentsMargins(22, 0, 22, 0)
        lay.setSpacing(14)
        pm = QPixmap(HEADER_LOGO) if os.path.exists(HEADER_LOGO) else QPixmap()
        if not pm.isNull():
            logo = QLabel()
            logo.setPixmap(pm.scaledToHeight(34, Qt.SmoothTransformation))
            logo.setStyleSheet("background: transparent;")
            lay.addWidget(logo)
        else:
            wm = QLabel("BUFAB")
            wm.setStyleSheet(f"background: transparent; color: white; font-family: {FONT_HEAD}; "
                             "font-size: 21pt; font-weight: 600;")
            lay.addWidget(wm)
        box = QVBoxLayout()
        box.setSpacing(0)
        t = QLabel("BI Governance Studio")
        t.setStyleSheet(f"background: transparent; color: white; font-family: {FONT_HEAD}; "
                        "font-size: 16pt; font-weight: 600;")
        s = QLabel("Govern, document and right-size your Qlik Cloud and Power BI estates")
        s.setStyleSheet(f"background: transparent; color: {RAIL_FG}; font-size: 9pt;")
        box.addWidget(t)
        box.addWidget(s)
        lay.addLayout(box)
        lay.addStretch(1)
        return head

    def _build_nav(self):
        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(NAV_WIDTH)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons = {}
        for key, text in (("home", "  Home"), ("qlik", "  Qlik"), ("powerbi", "  Power BI"),
                          ("reports", "  Reports")):
            b = QPushButton(text)
            b.setObjectName("nav")
            b.setCheckable(True)
            b.clicked.connect(lambda _c=False, k=key: self.go_to(k))
            self._nav_group.addButton(b)
            self._nav_buttons[key] = b
            lay.addWidget(b)
        lay.addStretch(1)
        b_set = QPushButton("  Settings")
        b_set.setObjectName("nav")
        b_set.setCheckable(True)
        b_set.clicked.connect(lambda: self.go_to("settings"))
        self._nav_group.addButton(b_set)
        self._nav_buttons["settings"] = b_set
        lay.addWidget(b_set)
        return nav

    def _build_log_card(self):
        card = make_card()
        card.setMaximumHeight(220)
        lay = QVBoxLayout(card)
        head = QHBoxLayout()
        head.addWidget(label("LOG", "section"))
        head.addStretch(1)
        self.btn_toggle_log = QPushButton("Show log")
        self.btn_toggle_log.setObjectName("ghost")
        self.btn_toggle_log.clicked.connect(self._toggle_log)
        b_clear = QPushButton("Clear log")
        b_clear.setObjectName("ghost")
        b_clear.clicked.connect(lambda: self.log_box.setPlainText(""))
        b_open = QPushButton("Open library")
        b_open.setObjectName("ghost")
        b_open.clicked.connect(self._open_folder)
        head.addWidget(self.btn_toggle_log)
        head.addWidget(b_clear)
        head.addWidget(b_open)
        lay.addLayout(head)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(60)
        self.log_box.setVisible(False)
        lay.addWidget(self.log_box, 1)
        return card

    # ---------------- responsive breakpoints ----------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        # _build creates the rail before the workspaces, and a resize can
        # arrive mid-build, so only react once both exist.
        if getattr(self, "nav", None) is not None and getattr(self, "powerbi_view", None) is not None:
            self._apply_breakpoint(self.width())

    def _apply_breakpoint(self, w):
        """REDESIGN_SPEC.md, 'Breakpoints'. Two things react to window width
        so far: how wide the nav rail is, and how many columns the Qlik task
        hub lays out."""
        rail = NAV_WIDTH_WIDE if w >= 1440 else NAV_WIDTH
        if self.nav.width() != rail:
            self.nav.setFixedWidth(rail)
        cols = 2 if w < 1040 else 3
        self.qlik_view.hub.set_columns(cols)
        self.powerbi_view.hub.set_columns(cols)

    # ---------------- navigation ----------------
    def go_to(self, key):
        idx = {"home": 0, "qlik": 1, "powerbi": 2, "reports": 3, "settings": 4}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        btn = self._nav_buttons.get(key)
        if btn and not btn.isChecked():
            btn.setChecked(True)
        if key == "settings":
            # Re-read the shell each time rather than trusting the copy the
            # fields were filled with when the page was built.
            self.settings_view.reload()
        if key == "reports":
            # The library is a folder, so it can change without this app doing
            # anything - a colleague's run, a file deleted in Explorer. Re-read
            # it every time the page is opened rather than trusting a cache.
            self.reports_view.refresh()

    # ---------------- scope ----------------
    def set_scope(self, guids):
        """Replace the selection. Called from the GUI thread only (the scope
        sheet and the app loader)."""
        self.scope = {g for g in guids if g}
        self._save_settings()
        self.sig_scope_changed.emit()

    def scope_targets(self):
        """The selected apps as the dicts the workers expect, in list order, so
        a task always sees them in the same order the picker showed them."""
        chosen = self.scope
        return [a for a in self.apps if a["guid"] in chosen]

    def set_apps(self, apps):
        """A fresh app list. Any scoped GUID that is no longer on the tenant is
        dropped, so a deleted app cannot linger in the scope forever."""
        self.apps = apps
        live = {a["guid"] for a in apps}
        kept = self.scope & live
        if kept != self.scope:
            self.scope = kept
            self._save_settings()
        self.sig_scope_changed.emit()

    def _on_scope_changed(self):
        self.qlik_view.scope_bar.refresh()
        self.qlik_view.refresh_ready()
        self.refresh_status()

    def reports_changed(self):
        """A run just filed a report in the library. Called from worker
        THREADS, so it only emits - touching widgets off the GUI thread is
        undefined behaviour, which is why every other worker callback in this
        app goes through a signal too."""
        self.sig_reports_changed.emit()

    def _on_reports_changed(self):
        """GUI thread. Rebuild whichever page shows the library."""
        idx = self.stack.currentIndex()
        if idx == 3:
            self.reports_view.refresh()
        elif idx == 0:
            self.home_view.refresh()

    # ---------------- logging ----------------
    def log(self, msg):
        self.sig_log.emit(str(msg))

    def _append_log(self, msg):
        self.log_box.appendPlainText(msg)

    def _toggle_log(self):
        if self.log_box.isVisible():
            self.log_box.setVisible(False)
            self.btn_toggle_log.setText("Show log")
        else:
            self.log_box.setVisible(True)
            self.btn_toggle_log.setText("Hide log")

    # ---------------- the running run (REDESIGN_SPEC.md step 4) ----------------
    def busy_begin(self, msg, steps=()):
        """Start (or join) a run. Called from the GUI thread by each task's
        handler before it starts its worker. `steps` are the run's named
        stages; a run that passes none still gets a title, a clock, a live
        detail line and Cancel."""
        self._busy_ops += 1
        self._busy_msg = msg
        if self._busy_ops == 1:
            self._cancel.clear()
            self._busy_secs = 0
            self.run_card.begin(msg, steps)
            self._busy_render()
            self._busy_timer.start()
        else:
            self.run_card.set_title(msg)
            self._busy_render()

    def busy_end(self):
        if self._busy_ops > 0:
            self._busy_ops -= 1
        if self._busy_ops == 0:
            self._busy_timer.stop()
            self.run_card.end()

    # Workers call these from their own threads, so each one only emits.
    def run_step(self, index, result=""):
        self.sig_run.emit(("step", (index, result)))

    def run_progress(self, done, total, noun=""):
        self.sig_run.emit(("progress", (done, total, noun)))

    def run_detail(self, text):
        self.sig_run.emit(("detail", str(text)))

    def run_finish(self, result=""):
        self.sig_run.emit(("finish", result))

    def _on_run_event(self, event):
        """GUI thread. One dispatcher rather than four signals."""
        kind, payload = event
        if kind == "step":
            self.run_card.step(*payload)
        elif kind == "progress":
            self.run_card.set_progress(*payload)
        elif kind == "detail":
            self.run_card.set_detail(payload)
        elif kind == "finish":
            self.run_card.finish_step(payload)

    def _on_cancel(self):
        self._cancel.set()
        self.run_card.cancelling()
        self.log("Cancel requested - stopping after the current step ...")
        self._busy_render()

    def cancel_requested(self):
        """Workers check this between steps to stop cleanly."""
        return self._cancel.is_set()

    def _busy_render(self):
        m, s = divmod(self._busy_secs, 60)
        h, m = divmod(m, 60)
        clock = f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"
        tail = "  ·  cancelling…" if self._cancel.is_set() else ""
        self.run_card.set_clock(f"working {clock}{tail}")

    def _busy_tick(self):
        self._busy_secs += 1
        self._busy_render()

    # ---------------- status ----------------
    def refresh_status(self):
        t = self.tenant or "(not set)"

        def short(p):
            p = p or "(not set)"
            return "..." + p[-27:] if len(p) > 30 else p

        if not self.api_key.strip():
            qlik = "Qlik: key not set"
        elif not self.apps:
            qlik = "Qlik: click Load apps"
        else:
            qlik = f"Qlik: {len(self.apps)} apps  ·  {len(self.scope)} in scope"
        pbi = "Power BI: " + (self.pbi.get("tenant_id") and "configured" or "not configured")
        self.lbl_status.setText(
            f"Tenant:  {t}   •   Library:  {short(self.output_dir)}   •   {qlik}   •   {pbi}")

    # ---------------- settings ----------------
    def _open_settings(self):
        """Kept as the name other views call to send the user to settings."""
        self.go_to("settings")

    def settings_path(self):
        return SETTINGS_FILE

    def data_file_map(self, log=None):
        """{basename: modified date} for every data file on the tenant, fetched
        once per session.

        core.list_data_files walks one connection per space now, so it is no
        longer something to call inside a single-field trace. Two threads
        racing here both fetch and the second wins, which costs one redundant
        walk and is cheaper than a lock for a value that does not change.
        """
        if self.data_files is None:
            self.data_files = core.list_data_files(self.tenant, self.api_key, log)
        return self.data_files

    def apply_settings(self, qlik_tenant, qlik_key, output_dir, library_url, pbi, pbi_secret):
        if qlik_tenant != self.tenant:
            # Facts are keyed by app GUID, which means nothing on a different
            # tenant. Drop them rather than risk a cross-tenant hit.
            self.script_facts.clear()
            self.data_files = None
        self.tenant = qlik_tenant
        self.api_key = qlik_key
        self.output_dir = output_dir
        self.library_url = library_url
        self.pbi = pbi
        self.pbi_secret = pbi_secret
        self._save_settings()
        self.refresh_status()
        self.log("Settings saved.")
        if output_dir and os.path.isdir(output_dir):
            # Lay the folders out now, so the library reads as an organised
            # place in SharePoint from the start instead of filling in feature
            # by feature as people happen to run things.
            made = sharepoint.prepare_library(output_dir, log=self.log)
            if made:
                self.log(f"Prepared the library layout ({made} folder(s) created) "
                         "and wrote README.txt.")
            self.reports_changed()
        # auto-reload Qlik apps if its creds are set/changed
        if self.tenant and self.api_key.strip():
            self.qlik_view.refresh_after_settings()

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
            self.tenant = s.get("tenant", "")
            # One library folder replaces the per-product pair. Fall back to
            # whichever of those an earlier version saved (they were almost
            # always the same folder) so nobody has to re-pick it.
            self.output_dir = (s.get("output_dir") or s.get("output_dir_qlik")
                               or s.get("output_dir_powerbi") or "")
            self.library_url = s.get("library_url", "")
            self.scope = set(s.get("scope") or [])
            saved_pbi = s.get("pbi", {}) or {}
            for k in self.pbi:
                if k in saved_pbi:
                    self.pbi[k] = saved_pbi[k]
        except Exception:
            pass

    def _save_settings(self):
        try:
            data = {"tenant": self.tenant, "output_dir": self.output_dir,
                    "library_url": self.library_url, "pbi": self.pbi,
                    "scope": sorted(self.scope)}
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def feature_dir(self, product, feature):
        """This feature's own subfolder inside the library, so nothing lands
        loose in one shared folder. Created on demand by whichever writer uses
        it."""
        return os.path.join(self.output_dir or os.path.expanduser("~"), product, feature)

    def _open_folder(self):
        d = self.output_dir
        if d and os.path.isdir(d):
            try:
                os.startfile(d)  # Windows
            except AttributeError:
                QMessageBox.information(self, "Library folder", d)
        else:
            QMessageBox.warning(self, "Library folder",
                               "Set a library folder in Settings first.")


def main():
    app = QApplication(sys.argv)
    missing_files, missing_families = load_fonts(BASE_DIR)
    app.setStyleSheet(STYLE)
    # The body face is used when the body family loaded, whatever happened to
    # the heading face. This was `if missing_fonts` over the whole file list,
    # so one absent heading TTF dropped the entire app to Segoe UI even with
    # Barlow sitting there working.
    app.setFont(app_font() if "Barlow" not in missing_families
                else QFont("Segoe UI", 10))
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    win.missing_fonts = list(missing_families)
    if missing_families:
        # Name the family, and say what falls back because of it. "Barlow
        # fonts not found (BarlowCondensed-SemiBold.ttf)" left the reader to
        # work out that it meant every heading on every screen.
        what = {"Barlow": "body text", "Barlow Condensed": "headings, section "
                "labels, buttons, nav items and big numbers"}
        for fam in missing_families:
            win.log(f"Font family '{fam}' is not available - {what.get(fam, 'some text')} "
                    f"fall back to Segoe UI.")
        if missing_files:
            win.log("  missing file(s): " + ", ".join(missing_files) +
                    " - see fonts\\README.md. Note a family name lives inside the "
                    "file, so renaming another weight will not supply it.")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
