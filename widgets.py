"""Shared UI toolkit for Bufab BI Governance Studio.

Palette + stylesheet, small helper functions, and the reusable visual building
blocks (KPI tiles, a threshold meter, a ranked-bar row, a data table) used by
the Home / Qlik / Power BI views. No product logic lives here.

Implements section 2 (visual tokens) and section 9 (reusable widgets) of
REDESIGN_SPEC.md: square corners, hairline borders, transparent cards on a
light ground, a steel accent, and one chart colour plus three status colours.
"""
from __future__ import annotations

import html

import fmt

import os
import urllib.error

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import (
    QColor, QPainter, QBrush, QPen, QPixmap, QFont, QFontDatabase,
)
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QPushButton, QProgressBar, QScrollArea, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
)

# QtCharts ships with the standard PySide6 wheel, but guard the import so the app
# still launches (with table-only fallbacks) on the rare build where it is absent.
try:
    from PySide6.QtCharts import (
        QChart, QChartView, QBarCategoryAxis, QValueAxis, QLineSeries,
    )
    CHARTS_OK = True
except Exception:                                   # pragma: no cover
    CHARTS_OK = False

# --------------------------------------------------------------------- palette
BG      = "#F2F2F3"   # window ground
SURFACE = "#E9E9EA"   # inputs, inset panels, log box
BAR     = "#EBEBEC"   # sticky action bar, scope bar, settings sub-nav selection
RAIL    = "#1D2D3D"   # nav rail and title bar
RAIL_FG = "#CFD8E0"   # rail text, inactive

TEXT  = "#1D1F20"
MUTED = "#8D8E8F"     # == rgba(29,31,32,.55) flattened onto BG
LINE  = "#CACBCC"     # == rgba(29,31,32,.16) flattened onto BG
FAINT = "#E3E3E4"     # == rgba(29,31,32,.08) flattened onto BG

ACCENT       = "#5980A6"
ACCENT_HOVER = "#416180"   # accent-700, also pressed
ACCENT_TINT  = "#EEF6FF"   # accent-100, selected-row fill
ACCENT_DEEP  = "#1D2D3D"   # accent-900, same as RAIL

GOOD = "#4F7F63"
WARN = "#8C6A34"
BAD  = "#9D5148"
WARN_TINT = "#F7F1E6"
WARN_INK  = "#6B4F1D"
BAD_TINT  = "#F7ECEA"
BAD_INK   = "#7A3A38"

ROW_HOVER = "#ECECEE"
TRACK     = "#DEDEE1"      # meter / rank-bar track

# Back-compat aliases: the view modules still import these names. They now
# resolve to the new system, so the old screens pick up the new look for free.

# Type stacks. The TTFs are vendored into fonts/ and registered by load_fonts();
# the stacks fall back to Segoe UI on their own if a face is missing.
FONT_BODY = "'Barlow', 'Segoe UI'"
FONT_HEAD = "'Barlow Condensed', 'Segoe UI Semibold', 'Segoe UI'"
FONT_MONO = "'Consolas'"

_FONT_FILES = ("Barlow-Regular.ttf", "Barlow-Medium.ttf", "BarlowCondensed-SemiBold.ttf")

# QSS has no letter-spacing, so section labels set it on the QFont instead.
# PySide6 exposes this enum flat on QFont in forgiving mode and under
# QFont.SpacingType in strict-enum builds; label() runs on every screen, so
# resolve it once here rather than risk an AttributeError at launch.
_ABS_SPACING = getattr(QFont, "AbsoluteSpacing",
                       getattr(getattr(QFont, "SpacingType", None), "AbsoluteSpacing", None))


# The families the stylesheets above actually ask for. A file loading is not
# evidence that the family the QSS needs exists: a font's family name lives
# inside the file, not in its name. Barlow-SemiBold.ttf registers the family
# "Barlow SemiBold", and renaming it to BarlowCondensed-SemiBold.ttf would not
# make "Barlow Condensed" appear - which is exactly the mistake this catches.
_REQUIRED_FAMILIES = ("Barlow", "Barlow Condensed")


def load_fonts(base_dir):
    """Register the vendored Barlow faces.

    Returns (missing_files, missing_families). The second is what matters:
    FONT_BODY wants "Barlow" and FONT_HEAD wants "Barlow Condensed", and each
    falls back on its own, so a missing heading face must not cost the body
    face as well.
    """
    missing_files, families = [], set()
    for name in _FONT_FILES:
        path = os.path.join(base_dir, "fonts", name)
        fid = QFontDatabase.addApplicationFont(path) if os.path.exists(path) else -1
        if fid == -1:
            missing_files.append(name)
            continue
        families.update(QFontDatabase.applicationFontFamilies(fid) or [])
    missing_families = [f for f in _REQUIRED_FAMILIES if f not in families]
    return missing_files, missing_families


def app_font():
    """The default application font (body face, 10 pt)."""
    return QFont("Barlow", 10)


# Heading sizes available to head_label(). The stylesheet rules below are
# generated from this tuple so the two cannot drift apart.
_HEAD_SIZES = (11, 12, 13, 15, 16)
_HEAD_QSS = "\n".join(
    f"QLabel#head{p} {{ color: {TEXT}; font-family: {FONT_HEAD}; "
    f"font-weight: 600; font-size: {p}pt; }}" for p in _HEAD_SIZES)

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: {FONT_BODY}; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; spacing: 6px; }}

/* cards are transparent line drawings, not white panels */
QFrame#card {{ background: transparent; border: 1px solid {LINE}; border-radius: 0px; }}
QFrame#kpi {{ background: transparent; border: 1px solid {LINE}; border-radius: 0px; }}
QFrame#bar {{ background: {BAR}; border: none; border-top: 1px solid {LINE}; }}

/* Tooltips carry the long explanations that used to sit on the page, so they
   are styled like the rail rather than left as an OS-yellow box. */
QToolTip {{ background: {RAIL}; color: #FFFFFF; border: 1px solid {ACCENT_DEEP};
            border-radius: 0px; padding: 7px 9px; font-family: {FONT_BODY};
            font-size: 9pt; }}

QLabel#muted {{ color: {MUTED}; }}
QLabel#section {{ color: {MUTED}; font-family: {FONT_HEAD}; font-weight: 600; font-size: 8pt; }}
QLabel#kpiCaption {{ color: {MUTED}; font-family: {FONT_HEAD}; font-weight: 600; font-size: 8pt; }}
QLabel#kpiValue {{ color: {TEXT}; font-family: {FONT_HEAD}; font-weight: 600; font-size: 20pt; }}
QLabel#kpiSub {{ color: {MUTED}; font-size: 8.5pt; }}
QLabel#h1 {{ color: {TEXT}; font-family: {FONT_HEAD}; font-weight: 600; font-size: 14pt; }}
{_HEAD_QSS}

QPushButton#accent {{ background: {ACCENT}; color: #FFFFFF; border: none;
    border-radius: 0px; padding: 9px 18px; min-height: 22px;
    font-family: {FONT_HEAD}; font-size: 11pt; font-weight: 600; }}
QPushButton#accent:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#accent:pressed {{ background: {ACCENT_HOVER}; }}
QPushButton#accent:disabled {{ background: {LINE}; color: #FFFFFF; }}

QPushButton#ghost {{ background: transparent; color: {ACCENT_HOVER};
    border: 1px solid {LINE}; border-radius: 0px; padding: 7px 14px; min-height: 18px;
    font-family: {FONT_HEAD}; font-size: 10.5pt; font-weight: 600; }}
QPushButton#ghost:hover {{ background: {BAR}; border-color: {ACCENT}; color: {ACCENT_HOVER}; }}
QPushButton#ghost:disabled {{ color: #B4B5B6; border-color: {FAINT}; }}

/* left navigation rail */
QFrame#nav {{ background: {RAIL}; border: none; }}
QPushButton#nav {{ background: transparent; color: {RAIL_FG}; border: none;
    border-radius: 0px; text-align: left; padding: 12px 18px; min-height: 20px;
    font-family: {FONT_HEAD}; font-size: 11pt; font-weight: 600; }}
QPushButton#nav:hover {{ background: #27394B; color: #FFFFFF; }}
QPushButton#nav:checked {{ background: {BG}; color: {RAIL}; }}

QLineEdit {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px;
    padding: 7px 8px; min-height: 18px; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QComboBox {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px;
    padding: 6px 8px; min-height: 18px; }}
QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QDateEdit {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px;
    padding: 6px 8px; min-height: 18px; }}
QDateEdit:focus {{ border: 1px solid {ACCENT}; }}

QFrame#search {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px; }}
QFrame#search QLineEdit {{ border: none; background: transparent; padding: 6px 4px; }}
QFrame#search QLabel {{ color: {MUTED}; border: none; }}

QTableWidget {{ background: transparent; border: none; gridline-color: transparent;
    outline: 0; }}
QTableWidget::item {{ padding: 6px 6px; border-bottom: 1px solid {FAINT}; }}
QTableWidget::item:hover {{ background: {ROW_HOVER}; }}
QTableWidget::item:selected {{ background: {ACCENT_TINT}; color: {TEXT}; }}
QHeaderView::section {{ background: transparent; color: {MUTED}; border: none;
    border-bottom: 1px solid {LINE}; padding: 8px 6px;
    font-family: {FONT_HEAD}; font-size: 8pt; font-weight: 600; }}
QTableView::indicator, QCheckBox::indicator {{ width: 16px; height: 16px;
    border: 1px solid {LINE}; border-radius: 0px; background: {SURFACE}; }}
QTableView::indicator:checked, QCheckBox::indicator:checked {{
    background: {ACCENT}; border: 1px solid {ACCENT}; }}

QFrame#chip {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px; }}
QFrame#chip QLabel {{ border: none; background: transparent; }}
QPushButton#chipx {{ border: none; background: transparent; color: {MUTED};
    font-weight: 600; padding: 0px 2px; }}
QPushButton#chipx:hover {{ color: {ACCENT_DEEP}; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 9px 16px;
    border: none; border-bottom: 2px solid transparent;
    font-family: {FONT_HEAD}; font-size: 10.5pt; font-weight: 600; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {ACCENT_HOVER}; border-bottom: 2px solid {ACCENT}; }}

QPlainTextEdit {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: 0px;
    font-family: {FONT_MONO}; font-size: 8.5pt; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C2C3C5; min-height: 30px; border-radius: 0px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C2C3C5; min-width: 30px; border-radius: 0px; }}
QScrollBar::handle:horizontal:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QProgressBar {{ background: {TRACK}; border: 1px solid {LINE}; border-radius: 0px;
    height: 8px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; }}
"""


# ------------------------------------------------------------------ tiny helpers
def key_format_ok(key):
    """Reject API keys with spaces / non-ASCII before they are used (mirrors the
    original Qlik tool's guard)."""
    key = (key or "").strip()
    if not key:
        return False
    if any(c.isspace() for c in key):
        return False
    return key.isascii()


def scrub(key, text):
    """Remove the API key from any string before it is logged."""
    text = str(text)
    return text.replace(key, "<api key hidden>") if key and key in text else text


def friendly_load_error(e):
    if isinstance(e, urllib.error.HTTPError):
        if e.code in (401, 403):
            return ("The API key was rejected (unauthorized). Check the key and that "
                    "it has access to this tenant.")
        if e.code == 404:
            return ("Tenant not found. Check the tenant host in Settings "
                    "(e.g. yourtenant.eu.qlikcloud.com).")
        if e.code == 429:
            return "The tenant is busy (rate limited). Wait a moment and try again."
        return f"The server returned an error (HTTP {e.code})."
    if isinstance(e, urllib.error.URLError):
        return ("Could not reach the tenant. Check the host in Settings and your "
                "internet connection.")
    return "Could not load apps. Double-check the tenant and API key in Settings."


# One implementation in fmt.py, re-exported here under the name the GUI call
# sites already use. It used to be written out three times, and a drift
# between the copies made a report's delta disagree with the dashboard the
# number came from.
human_bytes = fmt.human_bytes


def head_label(text, pt):
    """A heading or big number in the condensed face, at one of _HEAD_SIZES.

    Sized by object name so the rule lives in the app-wide stylesheet. The two
    alternatives both fail here: setFont is overridden by the global
    `QWidget { font-size }` rule and silently renders at 10pt, and a
    per-instance setStyleSheet is applied at polish time - after the layout has
    already asked the label how tall it wants to be - which leaves a big number
    clipped and overlapping the line beneath it."""
    if pt not in _HEAD_SIZES:
        pt = min(_HEAD_SIZES, key=lambda p: abs(p - pt))
    lab = QLabel(text)
    lab.setObjectName(f"head{pt}")
    return lab


def label(text, obj=None, wrap=False):
    lbl = QLabel(text)
    if obj:
        lbl.setObjectName(obj)
    if wrap:
        lbl.setWordWrap(True)
    if obj in ("section", "kpiCaption") and _ABS_SPACING is not None:
        f = lbl.font()
        f.setLetterSpacing(_ABS_SPACING, 1.4)
        lbl.setFont(f)
    return lbl


def tip(widget, text):
    """Attach a long explanation to a control as a hover tooltip.

    Qt only word-wraps a tooltip when its text is rich text - a long plain
    string is drawn as one line running off the screen - so the text is
    escaped and wrapped in HTML here. Blank lines become paragraph breaks.
    Returns the widget, so it can be used inline."""
    paras = [html.escape(" ".join(p.split())) for p in text.split("\n\n")]
    widget.setToolTip("<html>" + "<br><br>".join(paras) + "</html>")
    return widget


class ElidedLabel(QLabel):
    """A label that crops its text with an ellipsis to fit the available width."""

    def __init__(self, text):
        super().__init__()
        self._full = text
        self.setText(text)
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def minimumSizeHint(self):
        h = super().minimumSizeHint().height()
        return QSize(0, h)

    def resizeEvent(self, event):
        fm = self.fontMetrics()
        self.setText(fm.elidedText(self._full, Qt.ElideRight, max(0, self.width())))
        super().resizeEvent(event)


# ------------------------------------------------------------------------ Card
class Card(QFrame):
    """A transparent panel with a hairline border. `blueprint=True` adds the
    corner registration marks that are the design system's signature - four
    small crosshairs drawn just inside the corners."""

    MARK = 11        # arm-to-arm length of the + mark
    INSET = 7        # distance from the corner

    def __init__(self, blueprint=False):
        super().__init__()
        self.setObjectName("card")
        self._blueprint = blueprint

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._blueprint:
            return
        p = QPainter(self)
        pen = QPen(QColor(LINE))
        pen.setWidth(1)
        p.setPen(pen)
        p.setOpacity(0.55)
        half = self.MARK // 2
        w, h = self.width(), self.height()
        for cx, cy in ((self.INSET + half, self.INSET + half),
                       (w - self.INSET - half, self.INSET + half),
                       (self.INSET + half, h - self.INSET - half),
                       (w - self.INSET - half, h - self.INSET - half)):
            p.drawLine(cx - half, cy, cx + half, cy)
            p.drawLine(cx, cy - half, cx, cy + half)
        p.end()


def top_bar(title, pt=12):
    """A page-heading bar: the BAR fill with a hairline under it, and the page
    title already in it. Returns (frame, layout) so the caller fills the rest
    of the row. Three pages had this same frame spelled out by hand."""
    bar = QFrame()
    bar.setStyleSheet(f"background: {BAR}; border: none; border-bottom: 1px solid {LINE};")
    lay = QHBoxLayout(bar)
    lay.setContentsMargins(10, 5, 10, 5)
    lay.setSpacing(8)
    lay.addWidget(head_label(title, pt))
    return bar, lay


def make_card(blueprint=False):
    return Card(blueprint)


# ------------------------------------------------------------------- KPI / meter
class KpiTile(QFrame):
    """A metric tile: section label, big condensed value, optional sub line.
    No accent stripe - the border and the type carry it."""

    def __init__(self, caption="", value="-", sub="", accent=None):
        super().__init__()
        self.setObjectName("kpi")
        self.setMinimumWidth(150)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(3)
        self._cap = label(caption.upper(), "kpiCaption")
        self._val = label(value, "kpiValue")
        self._sub = label(sub, "kpiSub", wrap=True)
        self._sub.setVisible(bool(sub))
        box.addWidget(self._cap)
        box.addWidget(self._val)
        box.addWidget(self._sub)

    def set(self, value=None, sub=None, accent=None):
        if value is not None:
            self._val.setText(str(value))
        if sub is not None:
            self._sub.setText(sub)
            self._sub.setVisible(bool(sub))
        return self


class Meter(QWidget):
    """A horizontal threshold gauge: a square track with a filled portion
    coloured green / amber / red by how close `pct` is to the limit. Painted
    directly so the colour can react to the value without per-state stylesheets."""

    TRACK_H = 16

    def __init__(self, warn_at=90.0, over_at=100.0):
        super().__init__()
        self._pct = 0.0
        self._caption = ""
        self._status = ""
        self._warn = warn_at
        self._over = over_at
        self.setMinimumHeight(54)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set(self, pct, caption="", status=""):
        self._pct = max(0.0, float(pct or 0.0))
        self._caption = caption
        self._status = status
        self.update()

    def _colour(self):
        if self._pct >= self._over:
            return QColor(BAD)
        if self._pct >= self._warn:
            return QColor(WARN)
        return QColor(GOOD)

    def paintEvent(self, _event):
        p = QPainter(self)
        w, h = self.width(), self.height()
        track_y = h - self.TRACK_H - 4
        # caption + status line above the track
        f = self.font()
        f.setPointSize(8)
        f.setBold(True)
        if _ABS_SPACING is not None:
            f.setLetterSpacing(_ABS_SPACING, 1.4)
        p.setFont(f)
        p.setPen(QPen(QColor(MUTED)))
        p.drawText(0, 14, self._caption.upper())
        if self._status:
            p.setPen(QPen(self._colour()))
            p.drawText(0, 0, w, 14, Qt.AlignRight, self._status)
        # square track + fill
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(TRACK)))
        p.drawRect(0, track_y, w, self.TRACK_H)
        frac = min(1.0, self._pct / self._over) if self._over else 0.0
        fill_w = int(w * frac)
        if fill_w > 0:
            p.setBrush(QBrush(self._colour()))
            p.drawRect(0, track_y, max(4, fill_w), self.TRACK_H)
        # percentage, right-aligned inside the track
        p.setPen(QPen(QColor(TEXT)))
        f.setPointSize(8)
        if _ABS_SPACING is not None:
            f.setLetterSpacing(_ABS_SPACING, 0)
        p.setFont(f)
        p.drawText(0, track_y, w - 6, self.TRACK_H,
                   Qt.AlignRight | Qt.AlignVCenter, f"{self._pct:.1f}%")
        p.end()


# ------------------------------------------------------------------ tag / banner
class Tag(QLabel):
    """A small square status/label chip. tone: accent | neutral | outline."""

    def __init__(self, text, tone="neutral"):
        super().__init__(text)
        fill, ink, border = {
            "accent":  (ACCENT_TINT, ACCENT_DEEP, "transparent"),
            "neutral": ("#F5F5F8", "#424244", "transparent"),
            "outline": ("transparent", ACCENT_HOVER, ACCENT),
            "warn":    (WARN_TINT, WARN_INK, "transparent"),
            "bad":     (BAD_TINT, BAD_INK, "transparent"),
        }.get(tone, ("#F5F5F8", "#424244", "transparent"))
        self.setStyleSheet(
            f"background: {fill}; color: {ink}; border: 1px solid {border};"
            f"border-radius: 0px; padding: 2px 7px; font-size: 8.5pt; font-weight: 600;")
        self.setAlignment(Qt.AlignCenter)


class Banner(QFrame):
    """A framed warning / error block - the 'candidates, not a delete list' and
    'this writes' notices. tone: warn | bad."""

    def __init__(self, text, tone="warn"):
        super().__init__()
        fill, ink = (BAD_TINT, BAD_INK) if tone == "bad" else (WARN_TINT, WARN_INK)
        self.setStyleSheet(f"background: {fill}; border: 1px solid {ink}; border-radius: 0px;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 11, 14, 11)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setStyleSheet(f"background: transparent; color: {ink}; border: none;")
        lay.addWidget(body, 1)


# ------------------------------------------------------------------- run card
class RunCard(QFrame):
    """What a long run looks like while it runs (REDESIGN_SPEC.md step 4).

    Replaces the indeterminate bar and the wall of log text with: what is
    running, how long it has been going, a determinate meter once a worker
    reports counts, and the run's named steps with the result of each one as it
    finishes. The log is still there, one click away in its drawer.

    One card on the shell rather than one per task page, which is also what the
    design's own note implies: you can leave the page, the job keeps running.
    """

    def __init__(self, on_cancel):
        super().__init__()
        self.setObjectName("card")
        self._steps = []          # [(name, label)]
        self._at = -1
        self._results = {}        # index -> what that step found
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        self.lbl_title = head_label("", 13)
        head.addWidget(self.lbl_title)
        head.addStretch(1)
        self.lbl_clock = QLabel("")
        self.lbl_clock.setObjectName("muted")
        head.addWidget(self.lbl_clock)
        self.btn_cancel = QPushButton("Cancel run")
        self.btn_cancel.setObjectName("ghost")
        self.btn_cancel.clicked.connect(on_cancel)
        head.addWidget(self.btn_cancel)
        lay.addLayout(head)

        self.meter = QProgressBar()
        self.meter.setTextVisible(True)
        self.meter.setFixedHeight(16)
        lay.addWidget(self.meter)

        self.steps_box = QVBoxLayout()
        self.steps_box.setSpacing(2)
        lay.addLayout(self.steps_box)

        self.lbl_detail = QLabel("")
        self.lbl_detail.setObjectName("muted")
        lay.addWidget(self.lbl_detail)
        self.setVisible(False)

    # ---- lifecycle ----
    def begin(self, title, steps=()):
        self._steps = list(steps)
        self._at = -1
        self._results = {}
        self.lbl_title.setText(title)
        self.lbl_detail.setText("")
        self.btn_cancel.setText("Cancel run")
        self.btn_cancel.setEnabled(True)
        self.set_progress(None, None, "")
        self._render_steps()
        self.setVisible(True)

    def end(self):
        self.setVisible(False)

    def set_title(self, title):
        self.lbl_title.setText(title)

    def set_clock(self, text):
        self.lbl_clock.setText(text)

    def cancelling(self):
        self.btn_cancel.setText("Cancelling…")
        self.btn_cancel.setEnabled(False)

    # ---- progress ----
    def set_progress(self, done, total, noun=""):
        """A determinate meter once a worker knows its total; indeterminate
        until then, because a fake percentage is worse than an honest spinner."""
        if total:
            self.meter.setRange(0, int(total))
            self.meter.setValue(int(done or 0))
            pct = int(round((done or 0) / total * 100))
            self.meter.setFormat(f"{done or 0} of {total} {noun}".strip() + f"  ·  {pct}%")
        else:
            self.meter.setRange(0, 0)
            self.meter.setFormat("")

    def set_detail(self, text):
        self.lbl_detail.setText(text)

    def step(self, index, result=""):
        """Mark step `index` as the one running; everything before it is done.
        `result` is what the step that just finished found."""
        if 0 <= self._at < len(self._steps) and result:
            self._results[self._at] = result
        self._at = index
        self._render_steps()

    def finish_step(self, result=""):
        if 0 <= self._at < len(self._steps) and result:
            self._results[self._at] = result
        self._at = len(self._steps)
        self._render_steps()

    def _render_steps(self):
        clear_layout(self.steps_box)
        for i, name in enumerate(self._steps):
            row = QHBoxLayout()
            row.setSpacing(8)
            if i < self._at:
                mark, colour, state = "\u2713", GOOD, self._results.get(i, "done")
            elif i == self._at:
                mark, colour, state = "\u2192", ACCENT, self._results.get(i, "running")
            else:
                mark, colour, state = "\u00b7", MUTED, "pending"
            m = QLabel(mark)
            m.setStyleSheet(f"color: {colour}; background: transparent;")
            m.setFixedWidth(12)
            row.addWidget(m)
            n = QLabel(name)
            n.setStyleSheet("background: transparent;" if i <= self._at
                            else f"color: {MUTED}; background: transparent;")
            row.addWidget(n)
            row.addStretch(1)
            r = QLabel(state)
            r.setObjectName("muted")
            row.addWidget(r)
            holder = QWidget()
            holder.setLayout(row)
            self.steps_box.addWidget(holder)


# ------------------------------------------------------------------- action bar
class ActionBar(QFrame):
    """One primary action per page, bottom-right, always visible.

    REDESIGN_SPEC.md step 5. Replaces the buttons that used to sit mid-page
    wherever the form happened to end: the bar is pinned below the scrolling
    page, so the thing you came to click never scrolls away. Secondary actions
    sit to its left, and the left end carries a status line saying what the run
    will cover - or why the primary is greyed out, which is better than letting
    someone click and collect a warning dialog.
    """

    HEIGHT = 40

    def __init__(self, primary, secondary=(), status=""):
        super().__init__()
        self.setObjectName("bar")
        self.primary = primary
        primary.setObjectName("accent")
        primary.setMinimumHeight(self.HEIGHT)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 14, 6)
        lay.setSpacing(8)
        self.lbl_status = QLabel(status)
        self.lbl_status.setObjectName("muted")
        lay.addWidget(self.lbl_status)
        lay.addStretch(1)
        for b in secondary:
            b.setObjectName("ghost")
            b.setMinimumHeight(self.HEIGHT)
            lay.addWidget(b)
        lay.addWidget(primary)
        self._base = status

    def set_status(self, text):
        self._base = text
        self.lbl_status.setText(text)

    def set_ready(self, ok, why=""):
        """Enable or disable the primary. `why` replaces the status line while
        it is disabled, so the bar says what is missing."""
        self.primary.setEnabled(bool(ok))
        self.lbl_status.setText(self._base if ok or not why else why)


# ------------------------------------------------------------------- task router
class TaskHub(QWidget):
    """A workspace as a hub of task cards plus one page per task.

    Replaces a tab strip (REDESIGN_SPEC.md, structural change 1): the hub is
    the landing page, opening a task swaps in its page, and a breadcrumb with a
    back arrow gets you out. Build the pages however you like, register each
    one with `add`, call `finish` once, then `open(-1)` shows the hub.

    Every page sits behind a scroll area. Without one, a page taller than the
    pane it lives in gets squeezed below its minimum and Qt draws its widgets
    on top of each other.
    """

    COLS_MAX = 3

    def __init__(self, crumb_root):
        super().__init__()
        self._root = crumb_root
        self._tasks = []          # (title, desc, badge)
        self._cards = []
        self._cols = 0
        self.stack = QStackedWidget()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        lay.addWidget(self._build_crumb())
        lay.addWidget(self.stack, 1)

    # ---- building ----
    def add(self, page, title, desc, badge="", bar=None):
        """Register a built page. `bar` is an ActionBar pinned below it, OUTSIDE
        the scroll area, so it stays put while the page scrolls."""
        self._tasks.append((title, desc, badge))
        body = _in_scroll(page, no_hscroll=True)
        if bar is None:
            self.stack.addWidget(body)
            return
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(body, 1)
        lay.addWidget(bar)
        self.stack.addWidget(wrap)

    def finish(self):
        """Build the hub and put it at stack index 0, so a task's index is its
        stack index minus one."""
        inner = QWidget()
        self._grid = QGridLayout(inner)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(10)
        self._grid.setVerticalSpacing(10)
        self._cards = [self._task_card(i, t, d, b)
                       for i, (t, d, b) in enumerate(self._tasks)]
        self.set_columns(self.COLS_MAX)
        self.stack.insertWidget(0, _in_scroll(inner))
        self.open(-1)

    # ---- navigation ----
    def open(self, idx):
        """idx -1 is the hub; 0..n-1 are the task pages."""
        self.stack.setCurrentIndex(idx + 1)
        on_task = idx >= 0
        self.btn_back.setVisible(on_task)
        self.lbl_crumb.setText(self._root + "  /  " +
                               (self._tasks[idx][0] if on_task else "Tasks"))

    def set_columns(self, cols):
        """Reflow the hub. Driven by MainWindow.resizeEvent."""
        cols = max(1, min(cols, self.COLS_MAX))
        if cols == self._cols or not self._cards:
            return
        self._cols = cols
        for i, card in enumerate(self._cards):
            self._grid.removeWidget(card)
            self._grid.addWidget(card, i // cols, i % cols)
        for c in range(self.COLS_MAX):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)
        self._grid.setRowStretch(self._grid.rowCount(), 1)

    # ---- pieces ----
    def _task_card(self, idx, title, desc, badge):
        card = make_card(blueprint=True)
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 12)
        lay.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(head_label(title, 13))
        head.addStretch(1)
        if badge:
            head.addWidget(Tag(badge, "bad" if badge == "writes" else "neutral"))
        lay.addLayout(head)
        lay.addWidget(label(desc, "muted", wrap=True), 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b = QPushButton("OPEN  \u2192")
        b.setObjectName("ghost")
        b.clicked.connect(lambda _c=False, i=idx: self.open(i))
        row.addWidget(b)
        lay.addLayout(row)
        return card

    def _build_crumb(self):
        bar = QFrame()
        # Same fill as the sticky action bar, but the rule sits under it rather
        # than over it, because this one heads the page.
        bar.setStyleSheet(f"background: {BAR}; border: none; "
                          f"border-bottom: 1px solid {LINE};")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(10, 5, 10, 5)
        lay.setSpacing(10)
        self.btn_back = QPushButton("\u2190  All tasks")
        self.btn_back.setObjectName("ghost")
        self.btn_back.clicked.connect(lambda: self.open(-1))
        lay.addWidget(self.btn_back)
        self.lbl_crumb = head_label("", 12)
        lay.addWidget(self.lbl_crumb)
        lay.addStretch(1)
        return bar


def _in_scroll(widget, no_hscroll=False):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    if no_hscroll:
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    return scroll


# ------------------------------------------------------------------- chart helper
def _chart_view(chart):
    chart.setBackgroundVisible(False)
    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setMinimumHeight(220)
    view.setStyleSheet("background: transparent;")
    return view


def _placeholder(text):
    lbl = label(text, "muted", wrap=True)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setMinimumHeight(160)
    return lbl


def line_chart(title, x_labels, values, colour=ACCENT):
    """A simple trend line. x_labels are shown as categories along the bottom."""
    if not CHARTS_OK:
        return _placeholder(title + "\n(charts unavailable)")
    if not x_labels:
        return _placeholder(title + "\n(no data)")
    series = QLineSeries()
    series.setColor(QColor(colour))
    for i, v in enumerate(values):
        series.append(i, float(v or 0))
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    chart.legend().setVisible(False)
    cat_axis = QBarCategoryAxis()
    cat_axis.append([str(c) for c in x_labels])
    cat_axis.setLabelsAngle(-45)
    val_axis = QValueAxis()
    val_axis.setLabelFormat("%.0f")
    chart.addAxis(cat_axis, Qt.AlignBottom)
    chart.addAxis(val_axis, Qt.AlignLeft)
    series.attachAxis(cat_axis)
    series.attachAxis(val_axis)
    return _chart_view(chart)


# --------------------------------------------------------------------- DataTable
_STRIPE_CACHE = {}


def _tone_stripe(colour):
    """A 5 px colour bar used as the row's status marker in the first cell,
    instead of tinting the whole row.

    Callers still pass the pale row tints they used for full-row fills, and a
    pale tint disappears at 5 px wide - so a light colour is darkened until it
    reads as a bar. Cached per colour; tables here run to hundreds of rows."""
    c = QColor(colour)
    if c.lightnessF() > 0.75:
        c = c.darker(170)
    key = c.name()
    if key not in _STRIPE_CACHE:
        pm = QPixmap(5, 18)
        pm.fill(c)
        _STRIPE_CACHE[key] = pm
    return _STRIPE_CACHE[key]


def colored_table(headers, rows, row_colour=None, numeric_cols=(), stretch_col=0, wrap=False):
    """Build a read-only QTableWidget from headers + rows (list of lists).

    row_colour(i, row) -> a hex string / QColor / None. A returned colour is
    drawn as a 4 px status bar at the left of the first cell rather than a
    full-row tint, so severity reads without flooding the row.
    numeric_cols right-aligns those column indices. stretch_col is the column
    that fills the remaining width. wrap=True word-wraps cells.
    """
    t = QTableWidget(len(rows), len(headers))
    t.setHorizontalHeaderLabels([str(h) for h in headers])
    t.verticalHeader().setVisible(False)
    t.setShowGrid(False)
    t.setSelectionMode(QAbstractItemView.NoSelection)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setFocusPolicy(Qt.NoFocus)
    t.setAlternatingRowColors(False)
    if wrap:
        t.setWordWrap(True)
    for r, row in enumerate(rows):
        tone = row_colour(r, row) if row_colour else None
        for c, val in enumerate(row):
            it = QTableWidgetItem("" if val is None else str(val))
            if c in numeric_cols:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignTop if wrap else
                                    Qt.AlignRight | Qt.AlignVCenter)
            if c == 0 and tone is not None:
                it.setData(Qt.DecorationRole, _tone_stripe(tone))
            t.setItem(r, c, it)
    hh = t.horizontalHeader()
    hh.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    for c in range(len(headers)):
        hh.setSectionResizeMode(c, QHeaderView.Stretch if c == stretch_col
                                else QHeaderView.ResizeToContents)
    if wrap:
        t.resizeRowsToContents()
    t.setMinimumHeight(140)
    return t


# ---------------------------------------------------------------------- RankRow
class RankRow(QWidget):
    """One row of a ranked list: the dimension name (left, elided with a
    tooltip), its value (right), and a proportional square bar beneath. Unlike a
    category bar chart, the label is ALWAYS visible - which is what usage
    reporting needs."""

    BAR_H = 7

    def __init__(self, name, value_text, frac, colour=ACCENT):
        super().__init__()
        self._name = name
        self._value = value_text
        self._frac = max(0.0, min(1.0, frac))
        self._colour = colour
        self.setMinimumHeight(34)
        self.setToolTip(name)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _event):
        p = QPainter(self)
        w = self.width()
        f = self.font()
        f.setPointSize(9)
        p.setFont(f)
        fm = p.fontMetrics()
        vw = fm.horizontalAdvance(self._value) + 6
        name = fm.elidedText(self._name, Qt.ElideRight, max(10, w - vw - 8))
        p.setPen(QPen(QColor(TEXT)))
        p.drawText(0, 0, w - vw - 6, 16, Qt.AlignLeft | Qt.AlignVCenter, name)
        p.setPen(QPen(QColor(MUTED)))
        p.drawText(w - vw, 0, vw, 16, Qt.AlignRight | Qt.AlignVCenter, self._value)
        by = 21
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(TRACK)))
        p.drawRect(0, by, w, self.BAR_H)
        fill = int(w * self._frac)
        if fill > 0:
            p.setBrush(QBrush(QColor(self._colour)))
            p.drawRect(0, by, max(3, fill), self.BAR_H)
        p.end()


def ranked_bars(title, items, colour=ACCENT, max_n=10, value_fmt=None):
    """A card titled `title` with up to `max_n` RankRow rows from items =
    [(name, value), ...] (already sorted desc). Bars are scaled to the top value."""
    value_fmt = value_fmt or (lambda v: f"{v:,}")
    card = make_card()
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(8)
    lay.addWidget(label(title, "section"))
    if not items:
        lay.addWidget(label("(no data)", "muted"))
        return card
    top = items[:max_n]
    maxv = max((v for _, v in top), default=0) or 1
    for name, val in top:
        lay.addWidget(RankRow(name, value_fmt(val), (val / maxv), colour))
    return card


def kpi_row(specs):
    """Lay a list of (caption, value, sub, accent) specs into a row of KpiTiles.
    Returns (container_widget, [tiles]) so callers can update the tiles later."""
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    tiles = []
    for spec in specs:
        caption, value = spec[0], spec[1]
        sub = spec[2] if len(spec) > 2 else ""
        tile = KpiTile(caption, value, sub)
        tiles.append(tile)
        lay.addWidget(tile, 1)
    return wrap, tiles


def clear_layout(lay):
    """Remove and delete every widget/sub-layout in a layout (used to re-render
    a dashboard panel in place).

    setParent(None) before deleteLater is not belt-and-braces: deleteLater runs
    on the next event-loop pass, so until then the widget is still a visible
    child of the panel and the re-rendered content is drawn on top of the old
    content. Reparenting takes it off screen now."""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w:
            w.setParent(None)
            w.deleteLater()
        else:
            child = item.layout()
            if child:
                clear_layout(child)


# Names the view modules still import.
