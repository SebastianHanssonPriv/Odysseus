"""Exact usage aggregations from the activity events.

All counts below are exact — every row is a real recorded view:
  * report_usage_daily   - views per workspace / report / user / day
  * user_report_usage    - times each user opened each report
  * user_daily_usage     - views and distinct reports per user per day

Deliberately excluded (not exact / not available from the Admin APIs):
  time-per-visit (no session-end event exists) and per-page/sheet usage
  (the log records report-level views only).
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd

# Consumption events. ViewReport covers Power BI reports; ViewDashboard covers
# dashboards. Extend this set if you want exports etc. counted as "usage".
VIEW_OPERATIONS = {"ViewReport", "ViewDashboard"}


def _load_events(data_dir: Path) -> pd.DataFrame:
    files = sorted((data_dir / "activity_events").glob("activity_events_*.jsonl"))
    rows: list[dict] = []
    for path in files:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    if not rows:
        raise SystemExit(
            f"No collected events under {data_dir}/activity_events/. "
            "Run main.py first."
        )
    return pd.DataFrame(rows)


def _pick(df: pd.DataFrame, *names: str) -> pd.Series:
    # Activity-event field names vary; take the first column that is present.
    for name in names:
        if name in df.columns:
            return df[name]
    return pd.Series([None] * len(df), index=df.index)


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["user"] = _pick(df, "UserId", "UserKey")
    out["operation"] = _pick(df, "Operation", "Activity")
    out["report"] = _pick(df, "ReportName", "ArtifactName", "ItemName")
    out["report_id"] = _pick(df, "ReportId", "ArtifactId")
    # The audit log spells it "WorkSpaceName" (capital S); accept both.
    out["workspace"] = _pick(df, "WorkspaceName", "WorkSpaceName")
    out["consumption"] = _pick(df, "ConsumptionMethod")
    out["timestamp"] = pd.to_datetime(
        _pick(df, "CreationTime"), utc=True, errors="coerce"
    )
    out["date"] = out["timestamp"].dt.date
    return out


def report_usage_daily(views: pd.DataFrame) -> pd.DataFrame:
    return (
        views.groupby(["workspace", "report", "report_id", "user", "date"])
        .size()
        .reset_index(name="views")
        .sort_values(["date", "views"], ascending=[True, False])
    )


def user_report_usage(views: pd.DataFrame) -> pd.DataFrame:
    return (
        views.groupby(["user", "workspace", "report"])
        .size()
        .reset_index(name="views")
        .sort_values(["user", "views"], ascending=[True, False])
    )


def user_daily_usage(views: pd.DataFrame) -> pd.DataFrame:
    return (
        views.groupby(["user", "date"])
        .agg(views=("report", "size"), distinct_reports=("report", "nunique"))
        .reset_index()
        .sort_values(["date", "views"], ascending=[True, False])
    )


def _parse_args() -> argparse.Namespace:
    default_data = Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser()
    parser = argparse.ArgumentParser(
        description="Aggregate Power BI activity events into exact usage tables."
    )
    parser.add_argument("--data-dir", type=Path, default=default_data)
    return parser.parse_args()


def compute(data_dir: Path) -> dict:
    """Load events and build the three usage frames once, returning them in a dict
    (so both run() and the desktop dashboard share one computation, no double-load).

    Keys: views, report_usage_daily, user_report_usage, user_daily_usage.
    Raises SystemExit if there are no events / no view events to aggregate.
    """
    events = _normalize(_load_events(data_dir))
    views = events[events["operation"].isin(VIEW_OPERATIONS)].dropna(
        subset=["user", "timestamp"]
    )
    if views.empty:
        raise SystemExit("No view events found to aggregate.")
    return {
        "views": views,
        "report_usage_daily": report_usage_daily(views),
        "user_report_usage": user_report_usage(views),
        "user_daily_usage": user_daily_usage(views),
    }


def run(data_dir: Path) -> None:
    frames = compute(data_dir)

    out_dir = data_dir / "analytics"
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = {
        "report_usage_daily.csv": frames["report_usage_daily"],
        "user_report_usage.csv": frames["user_report_usage"],
        "user_daily_usage.csv": frames["user_daily_usage"],
    }
    for name, frame in outputs.items():
        frame.to_csv(out_dir / name, index=False, encoding="utf-8-sig")
        print(f"{name}: {len(frame)} rows -> {out_dir / name}")


def main() -> None:
    run(_parse_args().data_dir)


if __name__ == "__main__":
    main()
