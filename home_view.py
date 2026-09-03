"""Home overview for Bufab BI Governance Studio.

A single screen that summarises both estates at a glance: Qlik capacity status
(from the last capacity scan) and Power BI usage status (from the last analytics
build). Rebuilt whenever a scan finishes or the user returns to this tab. When a
side has no data yet, it shows a prompt + a button that jumps to that workspace.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QScrollArea, QFrame,
)

from widgets import (
    TEAL, WARN, GOOD, BAD, make_card, label, human_bytes,
    KpiCard, MeterBar, kpi_row, ranked_bars, clear_layout,
)


class HomeView(QWidget):
    def __init__(self, shell):
        super().__init__()
        self.shell = shell
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(2, 2, 2, 2)
        self.body.setSpacing(12)
        scroll.setWidget(holder)
        outer.addWidget(scroll)
        self.refresh()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def refresh(self):
        clear_layout(self.body)
        self.body.addWidget(label("Estate overview", "h1"))
        self.body.addWidget(label("A snapshot of both BI estates. Run a scan in each workspace to "
                                  "populate or refresh these cards.", "muted", wrap=True))
        self.body.addWidget(self._qlik_card())
        self.body.addWidget(self._powerbi_card())
        self.body.addStretch(1)

    # ---------------- Qlik ----------------
    def _qlik_card(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("QLIK CLOUD — CAPACITY", "section"))
        if self.shell.last_capacity_at:
            head.addWidget(label(f"· scanned {self.shell.last_capacity_at}", "muted"))
        head.addStretch(1)
        btn = QPushButton("Open Qlik workspace")
        btn.setObjectName("ghost")
        btn.clicked.connect(lambda: self.shell.go_to("qlik"))
        head.addWidget(btn)
        lay.addLayout(head)

        res = self.shell.last_capacity
        if not res:
            lay.addWidget(label("No capacity scan yet. Open the Qlik workspace → Capacity report → "
                                "Scan to populate this.", "muted", wrap=True))
            return card

        ar = res.get("app_reload", {}) or {}
        arr = ar.get("redundancy", {}) or {}
        ai = ar.get("inventory", {}) or {}
        cons = res.get("consumption") or {}
        dv = cons.get("data_volume")
        persum = arr.get("personal_summary", {}) or {}

        if dv and isinstance(dv.get("capacityLimit"), (int, float)) and dv.get("capacityLimit"):
            used, lim = dv.get("localUsage") or 0, dv.get("capacityLimit")
            pct = used / lim * 100 if lim else 0
            over = used - lim
            status = (f"OVERAGE by {human_bytes(over)}" if dv.get("overage") and over > 0
                      else ("close to limit" if dv.get("closeToOverage") else "ok"))
            meter = MeterBar(warn_at=90, over_at=100)
            meter.set(pct, f"Data for Analysis (billed):  {human_bytes(used)} / {human_bytes(lim)}",
                      status)
            lay.addWidget(meter)

        dup_reclaim = sum(c.get("dedupe_savings_bytes", 0) for c in arr.get("duplicate_app_clusters", []))
        spaces_billable = [s for s in arr.get("space_usage", []) if s.get("billable")]
        specs = [
            ("Billable app data", human_bytes(persum.get("billable_bytes", 0)),
             f"{persum.get('billable_count', 0)} apps", TEAL),
            ("Duplicate reclaim", human_bytes(dup_reclaim),
             f"{len(arr.get('duplicate_app_clusters', []))} clusters", WARN),
            ("Apps sized", str(ai.get("totals", {}).get("sized_app_count", 0)),
             f"of {ai.get('totals', {}).get('app_count', 0)}", TEAL),
        ]
        row, _ = kpi_row(specs)
        lay.addWidget(row)

        dups = arr.get("duplicate_app_clusters", [])[:6]
        if dups:
            lay.addWidget(ranked_bars(
                "Top duplicate reports — reclaim",
                [(d["base_name"], d.get("dedupe_savings_bytes", 0)) for d in dups],
                colour=WARN, max_n=6, value_fmt=human_bytes))
        return card

    # ---------------- Power BI ----------------
    def _powerbi_card(self):
        card = make_card()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 16)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(label("POWER BI — USAGE", "section"))
        if self.shell.last_pbi_at:
            head.addWidget(label(f"· built {self.shell.last_pbi_at}", "muted"))
        head.addStretch(1)
        btn = QPushButton("Open Power BI workspace")
        btn.setObjectName("ghost")
        btn.clicked.connect(lambda: self.shell.go_to("powerbi"))
        head.addWidget(btn)
        lay.addLayout(head)

        res = self.shell.last_pbi_usage
        if not res:
            lay.addWidget(label("No usage analytics yet. Open the Power BI workspace → Usage analytics "
                                "→ Build to populate this.", "muted", wrap=True))
            return card

        specs = [
            ("Total views", f"{res['total_views']:,}", f"over {res['days']} day(s)", TEAL),
            ("Distinct reports", f"{res['distinct_reports']:,}", "viewed", TEAL),
            ("Active users", f"{res['distinct_users']:,}", "≥1 view", GOOD),
            ("Least-viewed", str(len(res.get("low_usage", []))), "to review", WARN),
        ]
        row, _ = kpi_row(specs)
        lay.addWidget(row)

        if res.get("top_reports"):
            lay.addWidget(ranked_bars(
                "Top reports by views",
                [(n, v) for n, v in res["top_reports"][:6]],
                colour=TEAL, max_n=6))
        return card
