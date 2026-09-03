"""Bufab BI Governance Studio - unified desktop shell.

One window over two products: Qlik Cloud governance and Power BI usage. A left
nav rail switches between a Home overview, the Qlik workspace and the Power BI
workspace; the header, status line, busy indicator, LOG panel, output folder and
Settings are shared. Secrets (Qlik API key, Power BI client secret) are held in
memory only and never written to disk.

Entry point: main().
"""
from __future__ import annotations

import os
import sys
import json
import threading

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QIcon, QPixmap, QFont
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QLineEdit, QPushButton, QComboBox, QDialog, QFileDialog, QMessageBox,
    QFrame, QButtonGroup, QStackedWidget, QPlainTextEdit, QProgressBar,
)

from widgets import STYLE, TEAL, TEAL_DARK, make_card, label
from qlik_view import QlikView
from powerbi_view import PowerBIView
from home_view import HomeView

SETTINGS_FILE = os.path.join(os.path.expanduser("~"), ".bufab_bi_studio.json")

if getattr(sys, "frozen", False):
    BASE_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_PATH = os.path.join(BASE_DIR, "app_icon.ico")
HEADER_LOGO = os.path.join(BASE_DIR, "bufab_header.png")

PBI_AUTH_MODES = ["Client secret (in-memory)", "Key Vault", "Managed identity"]


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

        # --- shared ---
        lay.addWidget(self._sec("SHARED"))
        og = QGridLayout()
        og.addWidget(self._mut("Output folder"), 0, 0)
        self.ed_out = QLineEdit(parent.output_dir)
        og.addWidget(self.ed_out, 0, 1)
        browse = QPushButton("Browse...")
        browse.setObjectName("ghost")
        browse.clicked.connect(self._browse)
        og.addWidget(browse, 0, 2)
        og.setColumnStretch(1, 1)
        lay.addLayout(og)

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

    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder",
                                             self.ed_out.text() or os.path.expanduser("~"))
        if d:
            self.ed_out.setText(d)

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
            self.ed_out.text().strip(), pbi, self.ed_p_secret.text())
        self.accept()


# ============================================================
#  Main shell
# ============================================================
class MainWindow(QMainWindow):
    sig_log = Signal(str)

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
        self.output_dir = ""
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

        main.addWidget(self._build_nav())

        content = QWidget()
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 12, 16, 16)
        cl.setSpacing(12)
        main.addWidget(content, 1)

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
        self.stack.addWidget(self.powerbi_view)    # 2
        cl.addWidget(self.stack, 1)

        cl.addWidget(self._build_log_card())
        self.go_to("home")

    def _build_header(self):
        head = QFrame()
        head.setStyleSheet(f"background: {TEAL};")
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
            wm.setStyleSheet("background: transparent; color: white; font-size: 21pt; "
                             "font-weight: 800; letter-spacing: 1px;")
            lay.addWidget(wm)
        box = QVBoxLayout()
        box.setSpacing(0)
        t = QLabel("BI Governance Studio")
        t.setStyleSheet("background: transparent; color: white; font-size: 16pt; font-weight: 700;")
        s = QLabel("Govern, document and right-size your Qlik Cloud and Power BI estates")
        s.setStyleSheet("background: transparent; color: #CFE0E6; font-size: 9pt;")
        box.addWidget(t)
        box.addWidget(s)
        lay.addLayout(box)
        lay.addStretch(1)
        return head

    def _build_nav(self):
        nav = QFrame()
        nav.setObjectName("nav")
        nav.setFixedWidth(184)
        lay = QVBoxLayout(nav)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(2)
        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        self._nav_buttons = {}
        for key, text in (("home", "  Home"), ("qlik", "  Qlik"), ("powerbi", "  Power BI")):
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
        self.btn_toggle_log = QPushButton("Hide log")
        self.btn_toggle_log.setObjectName("ghost")
        self.btn_toggle_log.clicked.connect(self._toggle_log)
        b_clear = QPushButton("Clear log")
        b_clear.setObjectName("ghost")
        b_clear.clicked.connect(lambda: self.log_box.setPlainText(""))
        b_open = QPushButton("Open output folder")
        b_open.setObjectName("ghost")
        b_open.clicked.connect(self._open_folder)
        head.addWidget(self.btn_toggle_log)
        head.addWidget(b_clear)
        head.addWidget(b_open)
        lay.addLayout(head)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(60)
        lay.addWidget(self.log_box, 1)
        return card

    # ---------------- navigation ----------------
    def go_to(self, key):
        idx = {"home": 0, "qlik": 1, "powerbi": 2}.get(key, 0)
        self.stack.setCurrentIndex(idx)
        btn = self._nav_buttons.get(key)
        if btn and not btn.isChecked():
            btn.setChecked(True)

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
        o = self.output_dir or "(not set)"
        if len(o) > 44:
            o = "..." + o[-41:]
        if not self.api_key.strip():
            qlik = "Qlik: key not set"
        elif not getattr(self.qlik_view, "apps", None):
            qlik = "Qlik: click Load apps"
        else:
            qlik = f"Qlik: {len(self.qlik_view.apps)} apps"
        pbi = "Power BI: " + (self.pbi.get("tenant_id") and "configured" or "not configured")
        self.lbl_status.setText(f"Tenant:  {t}   •   Output:  {o}   •   {qlik}   •   {pbi}")

    # ---------------- settings ----------------
    def _open_settings(self):
        SettingsDialog(self).exec()

    def apply_settings(self, qlik_tenant, qlik_key, output_dir, pbi, pbi_secret):
        self.tenant = qlik_tenant
        self.api_key = qlik_key
        self.output_dir = output_dir
        self.pbi = pbi
        self.pbi_secret = pbi_secret
        self._save_settings()
        self.refresh_status()
        self.log("Settings saved.")
        # auto-reload Qlik apps if its creds are set/changed
        if self.tenant and self.api_key.strip():
            self.qlik_view.refresh_after_settings()

    def _load_settings(self):
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as f:
                s = json.load(f)
            self.tenant = s.get("tenant", "")
            self.output_dir = s.get("output_dir", "")
            saved_pbi = s.get("pbi", {}) or {}
            for k in self.pbi:
                if k in saved_pbi:
                    self.pbi[k] = saved_pbi[k]
        except Exception:
            pass

    def _save_settings(self):
        try:
            data = {"tenant": self.tenant, "output_dir": self.output_dir, "pbi": self.pbi}
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _open_folder(self):
        d = self.output_dir
        if d and os.path.isdir(d):
            try:
                os.startfile(d)  # Windows
            except AttributeError:
                QMessageBox.information(self, "Output folder", d)
        else:
            QMessageBox.warning(self, "Output folder", "Folder does not exist yet.")


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLE)
    app.setFont(QFont("Segoe UI", 10))
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
