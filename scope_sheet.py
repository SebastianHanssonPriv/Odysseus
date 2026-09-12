"""The app picker, opened over the page instead of living in it permanently.

Screen 1f of REDESIGN_SPEC.md, and the other half of structural change 2: the
selection itself lives on the shell (`shell.scope`), so every task reads the
same one and this sheet is the only place that edits it. It used to be a
permanent table taking the top third of the Qlik workspace whether or not
anyone was choosing apps.

Nothing is committed until "Use these N apps": Cancel leaves the shell's scope
exactly as it was, so opening the picker to look around cannot lose a
selection.
"""
from __future__ import annotations

import fmt

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
)

from widgets import ROW_HOVER, MUTED, ElidedLabel, head_label, tip


_days_since = fmt.days_since          # one implementation, see fmt.py


def _ago(iso):
    d = _days_since(iso)
    if d is None:
        return ""
    return "reloaded today" if d == 0 else f"reloaded {d} d ago"


class ScopeSheet(QDialog):
    """Modal picker over the app list. `shell.scope` is only written on accept."""

    def __init__(self, shell, parent=None, title="Choose apps"):
        super().__init__(parent)
        self.shell = shell
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(760, 620)
        self.apps = list(shell.apps)
        self._picked = set(shell.scope)
        self._building = False
        self._hover_row = -1

        # A filter is only offered when the tenant actually returned the data
        # it needs. Qlik's Items API does not always carry these, and a filter
        # that silently matches nothing is worse than no filter.
        self._has_published = any(a.get("published") for a in self.apps)
        self._has_reloaded = any(_days_since(a.get("reloaded")) is not None
                                 for a in self.apps)
        self._only_published = False
        self._only_recent = False

        self._build()
        self._rebuild()

    # ---------------- layout ----------------
    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        lay.addWidget(head_label("Choose apps", 15))

        top = QHBoxLayout()
        top.setSpacing(8)
        self.ed_find = QLineEdit()
        self.ed_find.setPlaceholderText("Find app or space")
        self.ed_find.textChanged.connect(lambda _t: self._rebuild())
        top.addWidget(self.ed_find, 1)
        for text, attr, enabled, hint in (
                ("Published only", "_only_published", self._has_published,
                 "Hides apps in a personal space and anything the tenant has not "
                 "reported a publish time for."),
                ("Reloaded < 30 d", "_only_recent", self._has_reloaded,
                 "Keeps apps whose last reload the tenant reported within 30 days.")):
            b = QPushButton(text)
            b.setObjectName("ghost")
            b.setCheckable(True)
            b.setEnabled(enabled)
            tip(b, hint if enabled else
                   "This tenant's Items API did not return the field this filter needs, "
                   "so it is switched off rather than silently matching nothing.")
            b.toggled.connect(lambda on, a=attr: (setattr(self, a, on), self._rebuild()))
            top.addWidget(b)
        lay.addLayout(top)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["App", "Space and last reload"])
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.setMouseTracking(True)
        self.table.cellClicked.connect(self._on_click)
        self.table.cellEntered.connect(self._on_hover)
        self.table.viewport().installEventFilter(self)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        b_shown = QPushButton("Select all shown")
        b_shown.setObjectName("ghost")
        b_shown.clicked.connect(self._select_shown)
        foot.addWidget(b_shown)
        b_none = QPushButton("Clear")
        b_none.setObjectName("ghost")
        b_none.clicked.connect(self._clear)
        foot.addWidget(b_none)
        self.lbl_count = QLabel("")
        self.lbl_count.setObjectName("muted")
        foot.addWidget(self.lbl_count)
        foot.addStretch(1)
        b_cancel = QPushButton("Cancel")
        b_cancel.setObjectName("ghost")
        b_cancel.clicked.connect(self.reject)
        foot.addWidget(b_cancel)
        self.btn_use = QPushButton("Use these apps")
        self.btn_use.setObjectName("accent")
        self.btn_use.clicked.connect(self._accept)
        foot.addWidget(self.btn_use)
        lay.addLayout(foot)

    # ---------------- list ----------------
    def _shown(self):
        q = self.ed_find.text().strip().lower()
        out = []
        for a in self.apps:
            if q and q not in (a.get("name") or "").lower() \
                 and q not in (a.get("space_name") or "").lower():
                continue
            if self._only_published and not a.get("published"):
                continue
            if self._only_recent:
                d = _days_since(a.get("reloaded"))
                if d is None or d > 30:
                    continue
            out.append(a)
        return out

    def _rebuild(self):
        self._building = True
        self._hover_row = -1
        self.table.setRowCount(0)
        for a in self._shown():
            row = self.table.rowCount()
            self.table.insertRow(row)
            it = QTableWidgetItem(a.get("name") or "(unnamed)")
            it.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            it.setCheckState(Qt.Checked if a["guid"] in self._picked else Qt.Unchecked)
            it.setData(Qt.UserRole, a["guid"])
            meta = "  ·  ".join(x for x in (a.get("space_name") or "", _ago(a.get("reloaded"))) if x)
            sp = QTableWidgetItem(meta)
            sp.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(row, 0, it)
            self.table.setItem(row, 1, sp)
        self._building = False
        self._update_count()

    def _update_count(self):
        shown = self.table.rowCount()
        total = len(self.apps)
        extra = f" · showing {shown} of {total}" if shown != total else ""
        self.lbl_count.setText(f"{len(self._picked)} of {total} selected{extra}")
        self.btn_use.setText(f"Use these {len(self._picked)} apps"
                             if len(self._picked) != 1 else "Use this 1 app")

    # ---------------- interaction ----------------
    def _on_click(self, row, _col):
        it = self.table.item(row, 0)
        if not it:
            return
        guid = it.data(Qt.UserRole)
        on = guid not in self._picked
        self._picked.add(guid) if on else self._picked.discard(guid)
        self._building = True
        it.setCheckState(Qt.Checked if on else Qt.Unchecked)
        self._building = False
        self._update_count()

    def _select_shown(self):
        self._building = True
        for row in range(self.table.rowCount()):
            it = self.table.item(row, 0)
            it.setCheckState(Qt.Checked)
            self._picked.add(it.data(Qt.UserRole))
        self._building = False
        self._update_count()

    def _clear(self):
        self._picked.clear()
        self._building = True
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(Qt.Unchecked)
        self._building = False
        self._update_count()

    def _accept(self):
        self.shell.set_scope(self._picked)
        self.accept()

    # ---------------- hover tint ----------------
    def _on_hover(self, row, _col):
        if row == self._hover_row:
            return
        self._row_bg(self._hover_row, None)
        self._row_bg(row, QColor(ROW_HOVER))
        self._hover_row = row

    def _row_bg(self, row, colour):
        if row < 0 or row >= self.table.rowCount():
            return
        brush = QBrush(colour) if colour is not None else QBrush()
        for c in range(self.table.columnCount()):
            it = self.table.item(row, c)
            if it:
                it.setBackground(brush)

    def eventFilter(self, obj, event):
        if obj is self.table.viewport() and event.type() == QEvent.Leave:
            self._row_bg(self._hover_row, None)
            self._hover_row = -1
        return super().eventFilter(obj, event)


class ScopeBar(QFrame):
    """The one-line summary of the scope that replaces the permanent table:
    a count, the first few names, and the button that opens the sheet."""

    MAX_NAMES = 3

    def __init__(self, shell, on_load=None):
        super().__init__()
        self.shell = shell
        self.setObjectName("card")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(12)
        self.lbl_count = head_label("", 13)
        lay.addWidget(self.lbl_count)
        # Elided, not wrapped: three app names plus "+9 more" would otherwise
        # set the window's minimum width from whatever the longest app is called.
        self.lbl_names = ElidedLabel("")
        self.lbl_names.setStyleSheet(f"color: {MUTED};")
        lay.addWidget(self.lbl_names, 1)
        if on_load:
            self.btn_load = QPushButton("Load apps")
            self.btn_load.setObjectName("ghost")
            self.btn_load.clicked.connect(on_load)
            lay.addWidget(self.btn_load)
        self.btn_change = QPushButton("Change scope")
        self.btn_change.setObjectName("accent")
        self.btn_change.clicked.connect(self._open)
        lay.addWidget(self.btn_change)
        self.refresh()

    def _open(self):
        if not self.shell.apps:
            self.shell.log("Load apps first - the picker has nothing to show yet.")
            return
        ScopeSheet(self.shell, self).exec()

    def refresh(self):
        n = len(self.shell.scope)
        total = len(self.shell.apps)
        self.lbl_count.setText(f"{n} app{'' if n == 1 else 's'} in scope")
        targets = self.shell.scope_targets()
        if not total:
            text = "No apps loaded yet"
        elif not targets:
            text = f"Nothing selected  ·  {total} apps on the tenant"
        else:
            names = [t.get("name") or "(unnamed)" for t in targets[:self.MAX_NAMES]]
            more = len(targets) - len(names)
            text = ", ".join(names) + (f"  +{more} more" if more else "")
        self._set_names(text)
        self.btn_change.setEnabled(bool(total))

    def _set_names(self, text):
        self.lbl_names._full = text
        self.lbl_names.setToolTip(text)
        self.lbl_names.setText(text)
