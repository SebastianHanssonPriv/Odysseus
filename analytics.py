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
import datetime
import json
import os
import re
from pathlib import Path

import pandas as pd

# Activity events are timestamped in UTC. Bucketing them by their UTC date puts
# the last hour or two of every local evening on the previous day: a view at
# 00:30 Stockholm time on Tuesday is 23:30 UTC Monday. For a Nordic business
# that silently shifts part of every day in every daily table and trend, so the
# day is taken in local time instead.
#
# Set BIGOV_TZ to any IANA name to change it. An unknown or unavailable zone
# falls back to UTC and says so rather than failing the run.
REPORTING_TZ = os.environ.get("BIGOV_TZ", "Europe/Stockholm")

_DAY_FILE = re.compile(r"activity_events_(\d{4}-\d{2}-\d{2})\.jsonl$")

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
    out["date"], out["tz"] = _local_day(out["timestamp"])
    return out


def _local_day(ts: pd.Series) -> tuple[pd.Series, str]:
    """(local calendar day, the zone it was taken in) for a UTC timestamp series."""
    try:
        return ts.dt.tz_convert(REPORTING_TZ).dt.date, REPORTING_TZ
    except Exception:
        # No tz database on this machine, or a bad BIGOV_TZ. UTC is wrong for a
        # Nordic reader but it is honest and it is labelled.
        return ts.dt.date, "UTC"


def collection_coverage(data_dir: Path) -> dict:
    """Which days of the collected series are present, empty, or missing.

    Activity events retain about 28 days, so the accumulated files ARE the
    dataset and a day never collected is gone. A gap shows up in the daily
    tables as a day with no views, which reads as "nobody opened anything"
    when it means "nobody collected anything". The same distinction this tool
    insists on for a model with no events applies to a day with no file.

    Returns {first, last, days_present, days_empty, days_missing}.
    """
    found = {}
    for path in (data_dir / "activity_events").glob("activity_events_*.jsonl"):
        m = _DAY_FILE.search(path.name)
        if m:
            try:
                found[datetime.date.fromisoformat(m.group(1))] = path.stat().st_size
            except ValueError:
                continue
    if not found:
        return {"first": None, "last": None, "days_present": 0,
                "days_empty": [], "days_missing": []}
    first, last = min(found), max(found)
    span = [first + datetime.timedelta(days=i) for i in range((last - first).days + 1)]
    return {
        "first": first,
        "last": last,
        "days_present": len(found),
        "days_empty": sorted(d for d, size in found.items() if size == 0),
        "days_missing": [d for d in span if d not in found],
    }


def coverage_warning(cov: dict) -> str:
    """One line for the log and the dashboard, or "" when the series is whole."""
    if not cov.get("first"):
        return ""
    bits = []
    if cov["days_missing"]:
        shown = ", ".join(d.isoformat() for d in cov["days_missing"][:6])
        more = f" and {len(cov['days_missing']) - 6} more" if len(cov["days_missing"]) > 6 else ""
        bits.append(f"{len(cov['days_missing'])} day(s) never collected ({shown}{more})")
    if cov["days_empty"]:
        bits.append(f"{len(cov['days_empty'])} collected day(s) hold no events")
    if not bits:
        return ""
    return ("Collection gaps: " + "; ".join(bits)
            + ". Those days show as zero views, which is not evidence of zero use. "
              "Activity events retain about 28 days, so a gap older than that "
              "cannot be backfilled.")


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
    all_views = events[events["operation"].isin(VIEW_OPERATIONS)]
    views = all_views.dropna(subset=["user", "timestamp"])
    dropped = len(all_views) - len(views)
    if views.empty:
        raise SystemExit("No view events found to aggregate.")
    cov = collection_coverage(data_dir)
    return {
        "views": views,
        "report_usage_daily": report_usage_daily(views),
        "user_report_usage": user_report_usage(views),
        "user_daily_usage": user_daily_usage(views),
        "coverage": cov,
        "coverage_warning": coverage_warning(cov),
        # Counted rather than dropped in silence: an event with no user or no
        # timestamp cannot be attributed, and the reader should know how many.
        "unattributable_views": dropped,
        "reporting_tz": views["tz"].iloc[0] if "tz" in views else "UTC",
    }


def run(data_dir: Path) -> None:
    frames = compute(data_dir)

    print(f"Days are local calendar days in {frames['reporting_tz']} "
          f"(set BIGOV_TZ to change).")
    if frames["unattributable_views"]:
        print(f"{frames['unattributable_views']} view event(s) had no user or no "
              f"timestamp and are not in any table.")
    if frames["coverage_warning"]:
        print(frames["coverage_warning"])

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
