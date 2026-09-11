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

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QDialog, QFileDialog, QMessageBox,
    QFrame, QButtonGroup, QStackedWidget, QPlainTextEdit, QProgressBar,
)

from widgets import (
    STYLE, TEAL, TEAL_DARK, RAIL, RAIL_FG, FONT_HEAD,
    make_card, label, tip, load_fonts, app_font,
)
from qlik_view import QlikView
from powerbi_view import PowerBIView
from reports_view import ReportsView
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


# ============================================================
#  Unified settings dialog
# ============================================================
class SettingsDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self.setMinimumWidth(520)
        if os.path.exists(ICON_PATH):
            self.setWindowIcon(QIcon(ICON_PATH))
        self._main = parent

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        # --- Qlik ---
        lay.addWidget(self._sec("QLIK CLOUD"))
        qg = QGridLayout()
        qg.setHorizontalSpacing(10)
        qg.setVerticalSpacing(8)
        qg.addWidget(self._mut("Tenant (host)"), 0, 0)
        self.ed_q_tenant = QLineEdit(parent.tenant)
        self.ed_q_tenant.setPlaceholderText("yourtenant.eu.qlikcloud.com")
        qg.addWidget(self.ed_q_tenant, 0, 1, 1, 2)
        qg.addWidget(self._mut("API key"), 1, 0)
        self.ed_q_key = QLineEdit(parent.api_key)
        self.ed_q_key.setEchoMode(QLineEdit.Password)
        qg.addWidget(self.ed_q_key, 1, 1, 1, 2)
        qg.setColumnStretch(1, 1)
        lay.addLayout(qg)

        # --- Power BI ---
        lay.addWidget(self._sec("POWER BI"))
        pg = QGridLayout()
        pg.setHorizontalSpacing(10)
        pg.setVerticalSpacing(8)
        p = parent.pbi
        pg.addWidget(self._mut("Tenant ID"), 0, 0)
        self.ed_p_tenant = QLineEdit(p.get("tenant_id", ""))
        pg.addWidget(self.ed_p_tenant, 0, 1, 1, 2)
        pg.addWidget(self._mut("Client ID"), 1, 0)
        self.ed_p_client = QLineEdit(p.get("client_id", ""))
        pg.addWidget(self.ed_p_client, 1, 1, 1, 2)
        pg.addWidget(self._mut("Auth mode"), 2, 0)
        self.cmb_p_mode = QComboBox()
        self.cmb_p_mode.addItems(PBI_AUTH_MODES)
        mode = p.get("auth_mode", PBI_AUTH_MODES[0])
        if mode in PBI_AUTH_MODES:
            self.cmb_p_mode.setCurrentText(mode)
        self.cmb_p_mode.currentTextChanged.connect(self._toggle_mode)
        pg.addWidget(self.cmb_p_mode, 2, 1, 1, 2)
        pg.addWidget(self._mut("Client secret"), 3, 0)
        self.ed_p_secret = QLineEdit(parent.pbi_secret)
        self.ed_p_secret.setEchoMode(QLineEdit.Password)
        self.ed_p_secret.setPlaceholderText("held in memory only - re-enter each session")
        pg.addWidget(self.ed_p_secret, 3, 1, 1, 2)
        pg.addWidget(self._mut("Key Vault URL"), 4, 0)
        self.ed_p_kv = QLineEdit(p.get("key_vault_url", ""))
        pg.addWidget(self.ed_p_kv, 4, 1, 1, 2)
        pg.addWidget(self._mut("Key Vault secret name"), 5, 0)
        self.ed_p_kvsecret = QLineEdit(p.get("key_vault_secret_name", ""))
        pg.addWidget(self.ed_p_kvsecret, 5, 1, 1, 2)
        pg.setColumnStretch(1, 1)
        lay.addLayout(pg)

        # --- one library folder for everything both products write ---
        lay.addWidget(self._sec("LIBRARY FOLDER"))
        og = QGridLayout()
        og.addWidget(self._mut("SharePoint URL"), 0, 0)
        self.ed_library_url = QLineEdit(parent.library_url)
        self.ed_library_url.setPlaceholderText(
            "paste the library's address from the browser, then click Find")
        tip(self.ed_library_url,
            "Paste the SharePoint address of the library, exactly as it appears in the "
            "browser, and click Find synced folder.\n\nStudio writes ordinary files, so it "
            "needs the local folder the OneDrive client syncs that library to - not the "
            "https:// address. That local path is different on every machine, which is why "
            "this looks it up instead of asking you to type it.\n\nThe library has to be "
            "synced first: open it in SharePoint and click Sync.")
        og.addWidget(self.ed_library_url, 0, 1)
        b_find = QPushButton("Find synced folder")
        b_find.setObjectName("ghost")
        b_find.clicked.connect(self._find_synced)
        og.addWidget(b_find, 0, 2)
        og.addWidget(self._mut("Library folder"), 1, 0)
        self.ed_library = QLineEdit(parent.output_dir)
        self.ed_library.setPlaceholderText(r"e.g. C:\Users\you\Bufab\BI Governance - Library")
        tip(self.ed_library, "Point this at a folder that OneDrive or the SharePoint client syncs "
                             "and the library is shared: everyone with access to that library sees "
                             "every report, opens the workbooks in Excel or the browser, and needs "
                             "no copy of Studio.\n\nA plain local folder works too - it is then "
                             "just your own library.")
        og.addWidget(self.ed_library, 1, 1)
        browse = QPushButton("Browse...")
        browse.setObjectName("ghost")
        browse.clicked.connect(lambda: self._browse(self.ed_library))
        og.addWidget(browse, 1, 2)
        og.setColumnStretch(1, 1)
        lay.addLayout(og)
        note_out = self._mut("Everything both products write goes here, each feature in its own "
                             "subfolder: Qlik\\capacity_report, Qlik\\field_lineage, "
                             "powerbi_data\\analytics and so on. Put the folder on a synced "
                             "SharePoint or OneDrive path and it doubles as the shared library.")
        note_out.setWordWrap(True)
        note_out.setStyleSheet("font-size: 8pt;")
        lay.addWidget(note_out)

        note = self._mut("Secrets (Qlik API key, Power BI client secret) are never saved to disk - "
                         "re-enter them each session. Everything else is remembered.")
        note.setWordWrap(True)
        note.setStyleSheet("font-size: 8pt;")
        lay.addWidget(note)

        btns = QHBoxLayout()
        btns.addStretch(1)
        b_close = QPushButton("Close")
        b_close.setObjectName("ghost")
        b_close.clicked.connect(self.reject)
        b_save = QPushButton("Save")
        b_save.setObjectName("accent")
        b_save.clicked.connect(self._on_save)
        btns.addWidget(b_close)
        btns.addWidget(b_save)
        lay.addLayout(btns)
        self._toggle_mode(self.cmb_p_mode.currentText())

    @staticmethod
    def _sec(text):
        return label(text, "section")

    @staticmethod
    def _mut(text):
        return label(text, "muted")

    def _toggle_mode(self, mode):
        # show only the credential fields the chosen mode needs
        secret_mode = mode == "Client secret (in-memory)"
        vault_mode = mode == "Key Vault"
        self.ed_p_secret.setEnabled(secret_mode)
        self.ed_p_kv.setEnabled(vault_mode)
        self.ed_p_kvsecret.setEnabled(vault_mode)

    def _find_synced(self):
        """Turn the pasted SharePoint URL into the local synced folder."""
        url = self.ed_library_url.text().strip()
        info = sharepoint.parse_library_url(url)
        if not info:
            QMessageBox.warning(self, "SharePoint URL",
                                "That does not look like a SharePoint library address.\n\n"
                                "Open the library in the browser and copy the address bar, or "
                                "use Copy link on the folder.")
            return
        found = sharepoint.resolve(url)
        if found:
            self.ed_library.setText(found)
            QMessageBox.information(self, "Found it",
                                    f"{sharepoint.describe(url)}\n\nis synced to:\n{found}")
            return
        chain = "\\".join(info["folders"])
        QMessageBox.warning(
            self, "Not synced on this PC",
            f"The library was recognised:\n  {sharepoint.describe(url)}\n\n"
            "but no synced copy of it was found on this PC.\n\n"
            "Open the library in SharePoint and click Sync, wait for it to finish, then "
            "click Find synced folder again.\n\nOnce synced it appears under your user "
            f"folder, ending in:\n  ...\\{chain}\n\nYou can also point Browse at it "
            "directly.")

    def _browse(self, lineedit):
        d = QFileDialog.getExistingDirectory(self, "Choose the library folder",
                                             lineedit.text() or os.path.expanduser("~"))
        if d:
            lineedit.setText(d)

    def _on_save(self):
        pbi = {
            "tenant_id": self.ed_p_tenant.text().strip(),
            "client_id": self.ed_p_client.text().strip(),
            "auth_mode": self.cmb_p_mode.currentText(),
            "key_vault_url": self.ed_p_kv.text().strip(),
            "key_vault_secret_name": self.ed_p_kvsecret.text().strip(),
        }
        self._main.apply_settings(
            self.ed_q_tenant.text().strip(), self.ed_q_key.text(),
            self.ed_library.text().strip(), self.ed_library_url.text().strip(),
            pbi, self.ed_p_secret.text())
        self.accept()


# ============================================================
#  Main shell
# ============================================================
class MainWindow(QMainWindow):
    sig_log = Signal(str)
    sig_reports_changed = Signal()
    sig_scope_changed = Signal()

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
        self.apps = []               # the loaded Qlik app list
        self.scope = set()           # selected app GUIDs
        self.pbi = {"tenant_id": "", "client_id": "", "auth_mode": PBI_AUTH_MODES[0],
                    "key_vault_url": "", "key_vault_secret_name": ""}
        self.pbi_secret = ""         # in memory only
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

        # status + busy row
        top = QHBoxLayout()
        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("muted")
        self.lbl_status.setWordWrap(True)
        top.addWidget(self.lbl_status, 1)
        self.busy_lbl = QLabel("")
        self.busy_lbl.setObjectName("muted")
        self.busy_lbl.setVisible(False)
        top.addWidget(self.busy_lbl, 0, Qt.AlignVCenter)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setFixedWidth(150)
        self.busy_bar.setVisible(False)
        top.addWidget(self.busy_bar, 0, Qt.AlignVCenter)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("ghost")
        self.btn_cancel.setVisible(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        top.addWidget(self.btn_cancel, 0, Qt.AlignVCenter)
        cl.addLayout(top)

        # stacked workspaces
        self.stack = QStackedWidget()
        self.home_view = HomeView(self)
        self.qlik_view = QlikView(self)
        self.powerbi_view = PowerBIView(self)
        self.stack.addWidget(self.home_view)       # 0
        self.stack.addWidget(self.qlik_view)       # 1
        self.reports_view = ReportsView(self)
        self.stack.addWidget(self.powerbi_view)    # 2
        self.stack.addWidget(self.reports_view)    # 3
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
        b_set.clicked.connect(self._open_settings)
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
        idx = {"home": 0, "qlik": 1, "powerbi": 2, "reports": 3}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        btn = self._nav_buttons.get(key)
        if btn and not btn.isChecked():
            btn.setChecked(True)
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

    # ---------------- busy indicator (GUI-thread; reference counted) ----------------
    def busy_begin(self, msg):
        self._busy_ops += 1
        self._busy_msg = msg
        if self._busy_ops == 1:
            self._cancel.clear()
            self._busy_secs = 0
            self.busy_bar.setVisible(True)
            self.busy_lbl.setVisible(True)
            self.btn_cancel.setText("Cancel")
            self.btn_cancel.setEnabled(True)
            self.btn_cancel.setVisible(True)
            self._busy_render()
            self._busy_timer.start()
        else:
            self._busy_render()

    def busy_end(self):
        if self._busy_ops > 0:
            self._busy_ops -= 1
        if self._busy_ops == 0:
            self._busy_timer.stop()
            self.busy_bar.setVisible(False)
            self.busy_lbl.setVisible(False)
            self.btn_cancel.setVisible(False)

    def _on_cancel(self):
        self._cancel.set()
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("Cancelling…")
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
        self.busy_lbl.setText(f"{self._busy_msg}  ·  working {clock}{tail}")

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
        SettingsDialog(self).exec()

    def apply_settings(self, qlik_tenant, qlik_key, output_dir, library_url, pbi, pbi_secret):
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
    missing_fonts = load_fonts(BASE_DIR)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10) if missing_fonts else app_font())
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    if missing_fonts:
        win.log("Barlow fonts not found (" + ", ".join(missing_fonts) +
                ") - falling back to Segoe UI. Drop the .ttf files in fonts\\ to "
                "get the intended type.")
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
