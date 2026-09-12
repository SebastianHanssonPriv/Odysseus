"""Settings as a page in the rail, not a modal (REDESIGN_SPEC.md step 6).

Three sections in a sub-nav: **Connections**, **Library**, **About**. The
spec's screen also drew Schedules and Appearance; neither exists here, because
scheduling is Windows Task Scheduler (documented in HOW_TO_RUN.md, nothing for
Studio to configure) and there is no theme to switch. An empty section is worse
than an absent one.

Only the credential fields the chosen Power BI auth mode needs are shown - the
others are hidden rather than greyed, so the page says what this install
actually uses.

Secrets are held in memory only. The Qlik API key and the Power BI client
secret are never written to `~/.bufab_bi_studio.json`.
"""
from __future__ import annotations

import os
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QFileDialog, QMessageBox, QFrame, QScrollArea,
    QButtonGroup,
)

import sharepoint
from widgets import (
    GOOD, BAD, ActionBar, make_card, label, top_bar, tip,
)

PBI_AUTH_MODES = ["Client secret (in-memory)", "Key Vault", "Managed identity"]


class SettingsView(QWidget):
    sig_tested = Signal(bool, str)

    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self.sig_tested.connect(self._on_tested)
        self._build()
        self.reload()

    # ---------------- layout ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(self._build_subnav())

        self.sections = QVBoxLayout()
        inner = QWidget()
        inner.setLayout(self.sections)
        self.sections.setContentsMargins(0, 0, 0, 0)
        self.sections.setSpacing(10)
        self._pages = [self._connections(), self._library(), self._about()]
        for p in self._pages:
            self.sections.addWidget(p)
        self.sections.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        btn_save = QPushButton("Save")
        btn_save.clicked.connect(self._save)
        self.bar = ActionBar(btn_save, status="")
        root.addWidget(self.bar)
        self._show_section(0)

    def _build_subnav(self):
        bar, lay = top_bar("Settings")
        lay.addSpacing(8)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._nav = []
        for i, name in enumerate(("Connections", "Library", "About")):
            b = QPushButton(name)
            b.setObjectName("ghost")
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _c=False, n=i: self._show_section(n))
            group.addButton(b)
            self._nav.append(b)
            lay.addWidget(b)
        lay.addStretch(1)
        self.lbl_saved = QLabel("")
        self.lbl_saved.setObjectName("muted")
        lay.addWidget(self.lbl_saved)
        return bar

    def _show_section(self, index):
        for i, p in enumerate(self._pages):
            p.setVisible(i == index)
        for i, b in enumerate(self._nav):
            b.setChecked(i == index)

    # ---------------- sections ----------------
    def _connections(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)

        lay.addWidget(label("QLIK CLOUD", "section"))
        qg = QGridLayout()
        qg.setHorizontalSpacing(10)
        qg.setVerticalSpacing(8)
        qg.addWidget(label("Tenant (host)", "muted"), 0, 0)
        self.ed_q_tenant = QLineEdit()
        self.ed_q_tenant.setPlaceholderText("yourtenant.eu.qlikcloud.com")
        qg.addWidget(self.ed_q_tenant, 0, 1)
        qg.addWidget(label("API key", "muted"), 1, 0)
        self.ed_q_key = QLineEdit()
        self.ed_q_key.setEchoMode(QLineEdit.Password)
        self.ed_q_key.setPlaceholderText("held in memory only - re-enter each session")
        qg.addWidget(self.ed_q_key, 1, 1)
        self.btn_test = QPushButton("Test")
        tip(self.btn_test, "Asks the tenant for its list of spaces with this key. A cheap, "
                           "read-only call that proves the host and the key work before you "
                           "start a scan.")
        self.btn_test.clicked.connect(self._test_qlik)
        qg.addWidget(self.btn_test, 1, 2)
        qg.setColumnStretch(1, 1)
        lay.addLayout(qg)
        self.lbl_test = label("", "muted")
        self.lbl_test.setWordWrap(True)
        lay.addWidget(self.lbl_test)

        lay.addWidget(label("POWER BI", "section"))
        pg = QGridLayout()
        pg.setHorizontalSpacing(10)
        pg.setVerticalSpacing(8)
        pg.addWidget(label("Tenant ID", "muted"), 0, 0)
        self.ed_p_tenant = QLineEdit()
        pg.addWidget(self.ed_p_tenant, 0, 1)
        pg.addWidget(label("Client ID", "muted"), 1, 0)
        self.ed_p_client = QLineEdit()
        pg.addWidget(self.ed_p_client, 1, 1)
        pg.addWidget(label("Auth mode", "muted"), 2, 0)
        self.cmb_p_mode = QComboBox()
        self.cmb_p_mode.addItems(PBI_AUTH_MODES)
        self.cmb_p_mode.currentTextChanged.connect(self._toggle_mode)
        pg.addWidget(self.cmb_p_mode, 2, 1)
        # Rows 3-5 are the credential fields; only the chosen mode's are shown.
        self.lbl_secret = label("Client secret", "muted")
        pg.addWidget(self.lbl_secret, 3, 0)
        self.ed_p_secret = QLineEdit()
        self.ed_p_secret.setEchoMode(QLineEdit.Password)
        self.ed_p_secret.setPlaceholderText("held in memory only - re-enter each session")
        pg.addWidget(self.ed_p_secret, 3, 1)
        self.lbl_kv = label("Key Vault URL", "muted")
        pg.addWidget(self.lbl_kv, 4, 0)
        self.ed_p_kv = QLineEdit()
        self.ed_p_kv.setPlaceholderText("https://yourvault.vault.azure.net/")
        pg.addWidget(self.ed_p_kv, 4, 1)
        self.lbl_kvsecret = label("Key Vault secret name", "muted")
        pg.addWidget(self.lbl_kvsecret, 5, 0)
        self.ed_p_kvsecret = QLineEdit()
        pg.addWidget(self.ed_p_kvsecret, 5, 1)
        pg.setColumnStretch(1, 1)
        lay.addLayout(pg)
        self.lbl_mode_note = label("", "muted", wrap=True)
        lay.addWidget(self.lbl_mode_note)
        lay.addWidget(label("Secrets are never written to disk. Everything else is remembered in "
                            "~/.bufab_bi_studio.json.", "muted", wrap=True))
        return card

    def _library(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)
        lay.addWidget(label("LIBRARY FOLDER", "section"))
        og = QGridLayout()
        og.setHorizontalSpacing(10)
        og.setVerticalSpacing(8)
        og.addWidget(label("SharePoint URL", "muted"), 0, 0)
        self.ed_library_url = QLineEdit()
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
        og.addWidget(label("Library folder", "muted"), 1, 0)
        self.ed_library = QLineEdit()
        og.addWidget(self.ed_library, 1, 1)
        b_browse = QPushButton("Browse...")
        b_browse.setObjectName("ghost")
        b_browse.clicked.connect(self._browse)
        og.addWidget(b_browse, 1, 2)
        og.setColumnStretch(1, 1)
        lay.addLayout(og)
        lay.addWidget(label(
            "Everything both products write goes here, each feature in its own subfolder: "
            "Qlik\\capacity_report, Qlik\\field_lineage, powerbi_data\\analytics and so on. "
            "Put the folder on a synced SharePoint or OneDrive path and it doubles as the "
            "shared library: colleagues see every report and open the workbooks in Excel or "
            "the browser, with no copy of Studio.", "muted", wrap=True))
        lay.addWidget(label(
            "Saving a library folder creates those subfolders and writes a README.txt "
            "describing them. Reports are kept until you clear them out from the Reports "
            "page - nothing is deleted automatically, because the library is shared.",
            "muted", wrap=True))
        return card

    def _about(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(8)
        lay.addWidget(label("ABOUT", "section"))
        self.lbl_about = label("", "muted", wrap=True)
        lay.addWidget(self.lbl_about)
        lay.addWidget(label(
            "Scheduling and appearance are not settings here. Unattended collection is a "
            "Windows Scheduled Task running collect_daily.bat (see HOW_TO_RUN.md, 'Run it "
            "daily'), and there is nothing for Studio to configure; the look is fixed.",
            "muted", wrap=True))
        return card

    # ---------------- data ----------------
    def reload(self):
        """Fill the fields from the shell. Called whenever the page opens, so
        it never shows a stale copy of the settings."""
        sh = self.shell
        self.ed_q_tenant.setText(sh.tenant)
        self.ed_q_key.setText(sh.api_key)
        self.ed_library.setText(sh.output_dir)
        self.ed_library_url.setText(sh.library_url)
        p = sh.pbi
        self.ed_p_tenant.setText(p.get("tenant_id", ""))
        self.ed_p_client.setText(p.get("client_id", ""))
        mode = p.get("auth_mode", PBI_AUTH_MODES[0])
        self.cmb_p_mode.setCurrentText(mode if mode in PBI_AUTH_MODES else PBI_AUTH_MODES[0])
        self.ed_p_secret.setText(sh.pbi_secret)
        self.ed_p_kv.setText(p.get("key_vault_url", ""))
        self.ed_p_kvsecret.setText(p.get("key_vault_secret_name", ""))
        self._toggle_mode(self.cmb_p_mode.currentText())
        self.lbl_test.setText("")
        self.lbl_saved.setText("")
        missing = ", ".join(sh.missing_fonts) if sh.missing_fonts else ""
        self.lbl_about.setText(
            f"Bufab BI Governance Studio\n"
            f"Settings file:  {sh.settings_path()}\n"
            f"Library:  {sh.output_dir or '(not set)'}\n"
            f"Fonts:  " + (f"Barlow not found ({missing}) - falling back to Segoe UI"
                           if missing else "Barlow loaded"))
        self.bar.set_status("Secrets are not saved - re-enter them each session")

    def _toggle_mode(self, mode):
        """Show only the credential fields this mode needs."""
        secret = mode == "Client secret (in-memory)"
        vault = mode == "Key Vault"
        for w in (self.lbl_secret, self.ed_p_secret):
            w.setVisible(secret)
        for w in (self.lbl_kv, self.ed_p_kv, self.lbl_kvsecret, self.ed_p_kvsecret):
            w.setVisible(vault)
        self.lbl_mode_note.setText(
            "Managed identity takes no credential here: the machine's own identity is used, "
            "which is the right choice for an unattended task." if not (secret or vault)
            else ("Key Vault is the recommended mode for anything unattended - a secret typed "
                  "in here lives only for this session." if vault else ""))

    # ---------------- actions ----------------
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Choose the library folder",
                                             self.ed_library.text() or os.path.expanduser("~"))
        if d:
            self.ed_library.setText(d)

    def _find_synced(self):
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

    def _test_qlik(self):
        host = self.ed_q_tenant.text().strip()
        key = self.ed_q_key.text().strip()
        if not host or not key:
            self.lbl_test.setText("Enter both the tenant host and an API key first.")
            self.lbl_test.setStyleSheet(f"color: {BAD};")
            return
        self.btn_test.setEnabled(False)
        self.lbl_test.setStyleSheet("")
        self.lbl_test.setText("Testing ...")
        threading.Thread(target=self._test_worker, args=(host, key), daemon=True).start()

    def _test_worker(self, host, key):
        """Cheapest read-only proof that the host and key work together."""
        try:
            import qlik_core as core
            spaces = core.list_spaces(host, key)
            self.sig_tested.emit(True, f"Connected. {len(spaces)} space(s) visible to this key.")
        except Exception as e:
            from widgets import friendly_load_error, scrub
            self.sig_tested.emit(False, scrub(key, friendly_load_error(e)))

    def _on_tested(self, ok, msg):
        self.btn_test.setEnabled(True)
        self.lbl_test.setStyleSheet(f"color: {GOOD if ok else BAD};")
        self.lbl_test.setText(msg)

    def _save(self):
        pbi = {
            "tenant_id": self.ed_p_tenant.text().strip(),
            "client_id": self.ed_p_client.text().strip(),
            "auth_mode": self.cmb_p_mode.currentText(),
            "key_vault_url": self.ed_p_kv.text().strip(),
            "key_vault_secret_name": self.ed_p_kvsecret.text().strip(),
        }
        self.shell.apply_settings(
            self.ed_q_tenant.text().strip(), self.ed_q_key.text(),
            self.ed_library.text().strip(), self.ed_library_url.text().strip(),
            pbi, self.ed_p_secret.text())
        self.lbl_saved.setText("Saved")
        self.lbl_about.setText(self.lbl_about.text())
        self.bar.set_status("Saved")
