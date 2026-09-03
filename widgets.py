"""Shared UI toolkit for Bufab BI Governance Studio.

Palette + stylesheet, small helper functions, and the reusable visual building
blocks (KPI cards, a threshold gauge, QtChart builders, a colour-coded table)
used by the Home / Qlik / Power BI views. No product logic lives here.
"""
from __future__ import annotations

import urllib.error

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QColor, QPainter, QBrush, QPen
from PySide6.QtWidgets import (
    QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView, QSizePolicy,
)

# QtCharts ships with the standard PySide6 wheel, but guard the import so the app
# still launches (with table-only fallbacks) on the rare build where it is absent.
try:
    from PySide6.QtCharts import (
        QChart, QChartView, QBarSet, QBarSeries, QHorizontalBarSeries,
        QBarCategoryAxis, QValueAxis, QLineSeries, QPieSeries,
    )
    CHARTS_OK = True
except Exception:                                   # pragma: no cover
    CHARTS_OK = False

# --- palette ---
TEAL = "#315C6D"
TEAL_DARK = "#274A57"
BG = "#EEF1F3"
CARD = "#FFFFFF"
TEXT = "#1F2A30"
MUTED = "#5B6B72"
BORDER = "#D5DCDF"
GOOD = "#2E7D52"
WARN = "#C77700"
BAD = "#B00020"
ROW_HOVER = "#E8F0F2"

# chart series colours (kept few and on-brand)
SERIES = ["#315C6D", "#C77700", "#2E7D52", "#7A5C9E", "#3E7CB1", "#B0566F"]

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
QLabel {{ background: transparent; }}
QCheckBox {{ background: transparent; spacing: 6px; }}
QFrame#card {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QFrame#kpi {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#section {{ color: {MUTED}; font-weight: 600; font-size: 9pt; }}
QLabel#kpiCaption {{ color: {MUTED}; font-weight: 600; font-size: 8pt; }}
QLabel#kpiValue {{ color: {TEXT}; font-weight: 700; font-size: 20pt; }}
QLabel#kpiSub {{ color: {MUTED}; font-size: 8pt; }}
QLabel#h1 {{ color: {TEXT}; font-size: 15pt; font-weight: 700; }}

QPushButton#accent {{ background: {TEAL}; color: #FFFFFF; border: none;
    border-radius: 6px; padding: 8px 16px; font-weight: 600; }}
QPushButton#accent:hover {{ background: {TEAL_DARK}; }}
QPushButton#accent:disabled {{ background: {BORDER}; color: #FFFFFF; }}

QPushButton#ghost {{ background: {CARD}; color: {TEAL}; border: 1px solid {BORDER};
    border-radius: 6px; padding: 7px 12px; }}
QPushButton#ghost:hover {{ background: #F0F3F4; }}
QPushButton#ghost:disabled {{ color: #AEB8BC; border-color: #E4E9EB; }}

/* left navigation rail */
QFrame#nav {{ background: {TEAL_DARK}; border: none; }}
QPushButton#nav {{ background: transparent; color: #CFE0E6; border: none;
    text-align: left; padding: 12px 18px; font-size: 11pt; font-weight: 600; }}
QPushButton#nav:hover {{ background: {TEAL}; color: #FFFFFF; }}
QPushButton#nav:checked {{ background: {BG}; color: {TEAL_DARK}; }}

QLineEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 8px; }}
QLineEdit:focus {{ border: 1px solid {TEAL}; }}
QComboBox {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}
QDateEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; padding: 5px 8px; }}

QFrame#search {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 6px; }}
QFrame#search QLineEdit {{ border: none; padding: 6px 4px; }}
QFrame#search QLabel {{ color: {MUTED}; border: none; }}

QTableWidget {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    gridline-color: {BG}; outline: 0; }}
QTableWidget::item {{ padding: 4px 6px; }}
QHeaderView::section {{ background: {CARD}; color: {MUTED}; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px 6px; font-weight: 600; }}
QTableView::indicator, QCheckBox::indicator {{ width: 16px; height: 16px;
    border: 1px solid {BORDER}; border-radius: 3px; background: {CARD}; }}
QTableView::indicator:checked, QCheckBox::indicator:checked {{
    background: {TEAL}; border: 1px solid {TEAL}; }}

QFrame#chip {{ background: #F4F7F8; border: 1px solid {BORDER}; border-radius: 13px; }}
QFrame#chip QLabel {{ border: none; background: transparent; }}
QPushButton#chipx {{ border: none; background: transparent; color: {MUTED};
    font-weight: 600; padding: 0px 2px; }}
QPushButton#chipx:hover {{ color: {TEAL_DARK}; }}

QFrame#banner {{ border-radius: 8px; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 16px;
    font-weight: 600; border: none; }}
QTabBar::tab:selected {{ color: {TEAL}; border-bottom: 2px solid {TEAL}; }}

QPlainTextEdit {{ background: {CARD}; border: 1px solid {BORDER}; border-radius: 8px;
    font-family: 'Consolas'; font-size: 9pt; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #C2CDD1; min-height: 30px; border-radius: 5px; }}
QScrollBar::handle:vertical:hover {{ background: {TEAL}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #C2CDD1; min-width: 30px; border-radius: 5px; }}
QScrollBar::handle:horizontal:hover {{ background: {TEAL}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}
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


def human_bytes(n):
    """Compact byte formatter (matches qlik_capacity.format_bytes output style)."""
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:,.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:,.1f} TB"


def make_card():
    f = QFrame()
    f.setObjectName("card")
    return f


def label(text, obj=None, wrap=False):
    lbl = QLabel(text)
    if obj:
        lbl.setObjectName(obj)
    if wrap:
        lbl.setWordWrap(True)
    return lbl


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


# ----------------------------------------------------------------- KPI / gauges
class KpiCard(QFrame):
    """A small metric tile: caption + big value + optional sub line, with an
    accent stripe down the left edge."""

    def __init__(self, caption="", value="-", sub="", accent=TEAL):
        super().__init__()
        self.setObjectName("kpi")
        self.setMinimumWidth(150)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        stripe = QFrame()
        stripe.setFixedWidth(4)
        stripe.setStyleSheet(f"background: {accent}; border-top-left-radius: 10px; "
                             "border-bottom-left-radius: 10px;")
        outer.addWidget(stripe)
        box = QVBoxLayout()
        box.setContentsMargins(14, 12, 14, 12)
        box.setSpacing(2)
        self._cap = label(caption.upper(), "kpiCaption")
        self._val = label(value, "kpiValue")
        self._sub = label(sub, "kpiSub", wrap=True)
        self._sub.setVisible(bool(sub))
        box.addWidget(self._cap)
        box.addWidget(self._val)
        box.addWidget(self._sub)
        outer.addLayout(box, 1)

    def set(self, value=None, sub=None, accent=None):
        if value is not None:
            self._val.setText(str(value))
        if sub is not None:
            self._sub.setText(sub)
            self._sub.setVisible(bool(sub))
        return self


class MeterBar(QWidget):
    """A horizontal threshold gauge: a track with a filled portion coloured
    green / amber / red by how close `pct` is to the limit. Painted directly so
    the colour can react to the value without per-state stylesheets."""

    def __init__(self, warn_at=90.0, over_at=100.0):
        super().__init__()
        self._pct = 0.0
        self._caption = ""
        self._status = ""
        self._warn = warn_at
        self._over = over_at
        self.setMinimumHeight(56)
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
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        track_h = 18
        track_y = h - track_h - 4
        # caption line
        p.setPen(QPen(QColor(MUTED)))
        f = self.font()
        f.setPointSize(8)
        f.setBold(True)
        p.setFont(f)
        p.drawText(2, 14, self._caption)
        if self._status:
            p.setPen(QPen(self._colour()))
            p.drawText(0, 14, w - 2, 14, Qt.AlignRight, self._status)
        # track
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(BORDER)))
        p.drawRoundedRect(2, track_y, w - 4, track_h, 6, 6)
        # fill (capped at the track width; % text shows the true value)
        frac = min(1.0, self._pct / self._over) if self._over else 0.0
        fill_w = int((w - 4) * frac)
        if fill_w > 0:
            p.setBrush(QBrush(self._colour()))
            p.drawRoundedRect(2, track_y, max(6, fill_w), track_h, 6, 6)
        # pct text
        p.setPen(QPen(QColor(TEXT)))
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(2, track_y, w - 8, track_h, Qt.AlignRight | Qt.AlignVCenter,
                   f"{self._pct:.1f}%")
        p.end()


# ------------------------------------------------------------------- chart helpers
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


def bar_chart(title, categories, values, horizontal=False, colour=TEAL):
    """A single-series bar chart. `categories` and `values` are equal-length lists.
    Horizontal is nicer for long category labels (report / app names)."""
    if not CHARTS_OK:
        return _placeholder(title + "\n(charts unavailable)")
    if not categories:
        return _placeholder(title + "\n(no data)")
    bar_set = QBarSet("")
    bar_set.setColor(QColor(colour))
    for v in values:
        bar_set.append(float(v or 0))
    series = QHorizontalBarSeries() if horizontal else QBarSeries()
    series.append(bar_set)
    series.setLabelsVisible(False)
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    chart.legend().setVisible(False)
    cat_axis = QBarCategoryAxis()
    cat_axis.append([str(c) for c in categories])
    val_axis = QValueAxis()
    val_axis.setLabelFormat("%.0f")
    if horizontal:
        chart.addAxis(val_axis, Qt.AlignBottom)
        chart.addAxis(cat_axis, Qt.AlignLeft)
    else:
        chart.addAxis(cat_axis, Qt.AlignBottom)
        chart.addAxis(val_axis, Qt.AlignLeft)
        cat_axis.setLabelsAngle(-45)
    series.attachAxis(cat_axis)
    series.attachAxis(val_axis)
    return _chart_view(chart)


def line_chart(title, x_labels, values, colour=TEAL):
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


def donut_chart(title, labels, values):
    """A part-of-whole donut. Good for 'data by space' style splits."""
    if not CHARTS_OK:
        return _placeholder(title + "\n(charts unavailable)")
    if not labels:
        return _placeholder(title + "\n(no data)")
    series = QPieSeries()
    series.setHoleSize(0.45)
    for i, (lab, val) in enumerate(zip(labels, values)):
        sl = series.append(f"{lab}", float(val or 0))
        sl.setColor(QColor(SERIES[i % len(SERIES)]))
        sl.setLabelVisible(False)
    chart = QChart()
    chart.addSeries(series)
    chart.setTitle(title)
    chart.legend().setAlignment(Qt.AlignRight)
    return _chart_view(chart)


# --------------------------------------------------------------- colour-coded table
def colored_table(headers, rows, row_colour=None, numeric_cols=(), stretch_col=0, wrap=False):
    """Build a read-only QTableWidget from headers + rows (list of lists).

    row_colour(i, row) -> a hex string / QColor / None to tint that row (used to
    flag severity). numeric_cols right-aligns those column indices. stretch_col is
    the column that fills the remaining width (e.g. a long definition/expression).
    wrap=True word-wraps cells and grows row heights so long text stays readable.
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
        tint = row_colour(r, row) if row_colour else None
        if tint is not None and not isinstance(tint, QColor):
            tint = QColor(tint)
        for c, val in enumerate(row):
            it = QTableWidgetItem("" if val is None else str(val))
            if c in numeric_cols:
                it.setTextAlignment(Qt.AlignRight | Qt.AlignTop if wrap else
                                    Qt.AlignRight | Qt.AlignVCenter)
            if tint is not None:
                it.setBackground(QBrush(tint))
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


class RankBar(QWidget):
    """One row of a ranked list: the dimension name (left, elided with a tooltip),
    its value (right), and a proportional bar beneath. Unlike a category bar chart,
    the label is ALWAYS visible — which is what usage reporting needs."""

    def __init__(self, name, value_text, frac, colour=TEAL):
        super().__init__()
        self._name = name
        self._value = value_text
        self._frac = max(0.0, min(1.0, frac))
        self._colour = colour
        self.setMinimumHeight(36)
        self.setToolTip(name)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
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
        by, bh = 21, 9
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(BORDER)))
        p.drawRoundedRect(0, by, w, bh, 4, 4)
        fill = int(w * self._frac)
        if fill > 0:
            p.setBrush(QBrush(QColor(self._colour)))
            p.drawRoundedRect(0, by, max(4, fill), bh, 4, 4)
        p.end()


def ranked_bars(title, items, colour=TEAL, max_n=10, value_fmt=None):
    """A card titled `title` with up to `max_n` RankBar rows from items = [(name,
    value), ...] (already sorted desc). Bars are scaled to the top value."""
    value_fmt = value_fmt or (lambda v: f"{v:,}")
    card = make_card()
    lay = QVBoxLayout(card)
    lay.setContentsMargins(14, 12, 14, 12)
    lay.setSpacing(8)
    lay.addWidget(label(title, "section"))
    if not items:
        lay.addWidget(label("(no data)", "muted"))
        return card
    top = items[:max_n]
    maxv = max((v for _, v in top), default=0) or 1
    for name, val in top:
        lay.addWidget(RankBar(name, value_fmt(val), (val / maxv), colour))
    return card


def kpi_row(specs):
    """Lay a list of (caption, value, sub, accent) specs into a row of KpiCards.
    Returns (container_widget, [cards]) so callers can update the cards later."""
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(10)
    cards = []
    for spec in specs:
        caption, value = spec[0], spec[1]
        sub = spec[2] if len(spec) > 2 else ""
        accent = spec[3] if len(spec) > 3 else TEAL
        card = KpiCard(caption, value, sub, accent)
        cards.append(card)
        lay.addWidget(card, 1)
    return wrap, cards


def clear_layout(lay):
    """Remove and delete every widget/sub-layout in a layout (used to re-render
    a dashboard panel in place)."""
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w:
            w.deleteLater()
        else:
            child = item.layout()
            if child:
                clear_layout(child)
