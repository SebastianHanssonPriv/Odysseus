"""The Reports page - the library, read straight out of the folder.

Every finished run drops a JSON manifest beside its workbook (see reports.py);
this page scans the library folder for them and lists what it finds, newest
first, with each run's headline numbers and how they moved since the previous
run of the same type. There is no database and no server: point two people's
Studio at the same synced SharePoint or OneDrive folder and each sees the
other's runs, because the only shared thing is the folder.

Screen 1g of REDESIGN_SPEC.md, minus the parts the single shared library makes
unnecessary. Writing into the library IS publishing, so there is no share
sheet; Studio has no user accounts, so there is no author, no "shared with me"
and no per-user pinning.
"""
from __future__ import annotations

import os
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QMessageBox, QButtonGroup,
)

import reports
from widgets import (
    BAR, LINE, MUTED, make_card, label, head_label, tip, Tag,
    clear_layout,
)

RETENTION_MONTHS = 12        # what "kept 12 months" in the redesign means


def open_path(path):
    """Open a file or folder in Explorer / Finder / the desktop's file manager."""
    try:
        os.startfile(path)                                   # Windows
    except AttributeError:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


class ReportsView(QWidget):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        self._records = []
        self._filter = "All"
        self._build()

    # ---------------- layout ----------------
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.addWidget(self._build_bar())

        inner = QWidget()
        self.list_lay = QVBoxLayout(inner)
        self.list_lay.setContentsMargins(0, 0, 0, 0)
        self.list_lay.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

    def _build_bar(self):
        bar = QFrame()
        bar.setStyleSheet(f"background: {BAR}; border: none; border-bottom: 1px solid {LINE};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(8)
        lay.addWidget(head_label("Reports", 12))
        lay.addSpacing(8)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        for name in ("All", "Qlik", "Power BI"):
            b = QPushButton(name)
            b.setObjectName("ghost")
            b.setCheckable(True)
            b.setChecked(name == "All")
            b.clicked.connect(lambda _c=False, n=name: self._set_filter(n))
            self._filter_group.addButton(b)
            lay.addWidget(b)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        lay.addWidget(self.lbl_count)
        lay.addStretch(1)
        b_ref = QPushButton("Refresh")
        b_ref.setObjectName("ghost")
        tip(b_ref, "Re-reads the library folder. Use it to pick up runs a colleague has "
                   "written into the same synced library since you opened this page.")
        b_ref.clicked.connect(self.refresh)
        lay.addWidget(b_ref)
        b_lib = QPushButton("Open library")
        b_lib.setObjectName("ghost")
        b_lib.clicked.connect(self._open_library)
        lay.addWidget(b_lib)
        b_clean = QPushButton("Clean up old")
        b_clean.setObjectName("ghost")
        tip(b_clean, f"Lists every report older than {RETENTION_MONTHS} months and asks before "
                     "deleting anything. Nothing is ever removed automatically - the library is "
                     "shared, so a run you delete is gone for everyone.")
        b_clean.clicked.connect(self._clean_up)
        lay.addWidget(b_clean)
        return bar

    # ---------------- data ----------------
    def refresh(self):
        """Re-scan the library and rebuild the list."""
        self._records = reports.scan(self.shell.output_dir)
        self._render()

    def _set_filter(self, name):
        self._filter = name
        self._render()

    def _shown(self):
        if self._filter == "All":
            return self._records
        return [r for r in self._records if r.get("product") == self._filter]

    def _render(self):
        clear_layout(self.list_lay)
        shown = self._shown()
        total = len(self._records)
        self.lbl_count.setText(f"{len(shown)} of {total} report{'' if total == 1 else 's'}"
                               if self._filter != "All" else
                               f"{total} report{'' if total == 1 else 's'}")
        if not self.shell.output_dir:
            self.list_lay.addWidget(self._empty(
                "No library folder yet",
                "Reports are kept in the library folder, and Studio has not been given one. "
                "Set it in Settings - put it on a synced SharePoint or OneDrive path and the "
                "library is shared with everyone who has access to it.", "Open settings",
                self.shell._open_settings))
        elif not shown:
            self.list_lay.addWidget(self._empty(
                "Nothing here yet",
                "Every scan, export and analysis you run is filed here so you can reopen it, "
                "see how its numbers moved since the previous run, and let colleagues on the "
                "same library open it too. Run something to get started.",
                "Go to Qlik", lambda: self.shell.go_to("qlik")))
        else:
            for rec in shown:
                self.list_lay.addWidget(self._card(rec))
        self.list_lay.addStretch(1)

    # ---------------- pieces ----------------
    def _empty(self, title, body, action_text, action):
        card = make_card(blueprint=True)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(8)
        lay.addWidget(head_label(title, 15))
        lay.addWidget(label(body, "muted", wrap=True))
        row = QHBoxLayout()
        b = QPushButton(action_text)
        b.setObjectName("accent")
        b.clicked.connect(lambda: action())
        row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        return card

    def _card(self, rec):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 13, 16, 13)
        lay.setSpacing(8)

        head = QHBoxLayout()
        box = QVBoxLayout()
        box.setSpacing(1)
        box.addWidget(head_label(rec.get("title", "(untitled)"), 13))
        box.addWidget(label(reports.subtitle(rec), "muted"))
        head.addLayout(box)
        head.addStretch(1)
        head.addWidget(Tag(rec.get("product", "?"), "accent"), 0, Qt.AlignTop)
        lay.addLayout(head)

        rows = reports.deltas(rec, reports.previous(self._records, rec))
        if rows:
            grid = QGridLayout()
            grid.setHorizontalSpacing(20)
            grid.setVerticalSpacing(2)
            for col, (name, display, delta) in enumerate(rows):
                grid.addWidget(label(name.upper(), "kpiCaption"), 0, col)
                grid.addWidget(head_label(display, 16), 1, col)
                # Deliberately not colour-coded: a rise is bad for billable
                # data and fine for apps sized, and the page cannot tell which
                # is which. The number and its direction are the information.
                grid.addWidget(label(delta, "muted"), 2, col)
            grid.setColumnStretch(len(rows), 1)
            lay.addLayout(grid)

        foot = QHBoxLayout()
        if rec.get("_exists"):
            target = rec["_path"]
            is_dir = os.path.isdir(target)
            b_open = QPushButton("Open folder" if is_dir else "Open workbook")
            b_open.setObjectName("accent")
            b_open.clicked.connect(lambda _c=False, p=target: open_path(p))
            foot.addWidget(b_open)
            if not is_dir:
                b_dir = QPushButton("Open folder")
                b_dir.setObjectName("ghost")
                b_dir.clicked.connect(lambda _c=False, p=os.path.dirname(target): open_path(p))
                foot.addWidget(b_dir)
        else:
            missing = label("The workbook is no longer in the library.", "muted")
            missing.setStyleSheet(f"color: {MUTED};")
            foot.addWidget(missing)
        foot.addStretch(1)
        b_del = QPushButton("Delete")
        b_del.setObjectName("ghost")
        b_del.clicked.connect(lambda _c=False, r=rec: self._delete(r))
        foot.addWidget(b_del)
        lay.addLayout(foot)
        return card

    # ---------------- actions ----------------
    def _open_library(self):
        d = self.shell.output_dir
        if d and os.path.isdir(d):
            open_path(d)
        else:
            QMessageBox.warning(self, "Library folder", "Set a library folder in Settings first.")

    def _delete(self, rec):
        if QMessageBox.question(
                self, "Delete report",
                f"Delete '{rec.get('title')}' from {reports.short_when(rec.get('created'))}?\n\n"
                "This removes the workbook and its record from the library. The library is "
                "shared, so it is gone for everyone using it.") != QMessageBox.Yes:
            return
        gone = reports.delete(rec)
        self.shell.log(f"Deleted {len(gone)} file(s) from the library.")
        self.refresh()

    def _clean_up(self):
        old = reports.older_than(self._records, RETENTION_MONTHS)
        if not old:
            QMessageBox.information(self, "Clean up",
                                    f"Nothing in the library is older than {RETENTION_MONTHS} "
                                    "months.")
            return
        names = "\n".join(f"  · {r.get('title')} - {reports.short_when(r.get('created'))}"
                          for r in old[:15])
        more = f"\n  ... and {len(old) - 15} more" if len(old) > 15 else ""
        if QMessageBox.question(
                self, "Clean up",
                f"Delete {len(old)} report(s) older than {RETENTION_MONTHS} months?\n\n"
                f"{names}{more}\n\nWorkbooks and records are removed from the shared "
                "library.") != QMessageBox.Yes:
            return
        n = sum(len(reports.delete(r)) for r in old)
        self.shell.log(f"Cleaned up {len(old)} report(s) ({n} file(s)) from the library.")
        self.refresh()
