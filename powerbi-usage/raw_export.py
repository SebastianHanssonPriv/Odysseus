"""Export the full raw activity-event log — no aggregation, no filtering.

Every event and every field is kept, with values untouched. Nested objects and
arrays are JSON-encoded into their cell so nothing is lost in a flat format
(this is reversible — json.loads gets the structure back).

Outputs (under <data>/raw/):
  activity_events_raw.parquet  recommended: typed, lossless, Fabric-native
  activity_events_raw.csv      convenience copy (UTF-8 BOM, every value quoted)
  key_map.csv                  which columns are keys and what they join to

The dimension tables referenced in key_map (Users, Reports, Workspaces,
Datasets, ...) come from the scanner APIs in the next phase; key_map is the
join contract between this event table and those.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

import pandas as pd

# field -> (role, relates_to, notes). These are the columns that join the event
# log to dimension tables, plus the event's own primary/correlation keys.
KEY_FIELDS: dict[str, tuple[str, str, str]] = {
    "Id": ("primary_key", "ActivityEvents", "Unique id of the audit event (row key)."),
    "UserId": ("foreign_key", "Users", "Actor UPN/email — join to a Users dimension."),
    "UserKey": ("foreign_key", "Users", "Alternate internal user key."),
    "WorkspaceId": ("foreign_key", "Workspaces", "Join to Workspaces (scanner APIs)."),
    "ReportId": ("foreign_key", "Reports", "Join to Reports (scanner APIs)."),
    "ArtifactId": ("foreign_key", "Items", "Generic item id; often equals ReportId."),
    "DatasetId": ("foreign_key", "Datasets", "Join to semantic models / datasets."),
    "CapacityId": ("foreign_key", "Capacities", "Join to capacities."),
    "AppId": ("foreign_key", "Apps", "Join to apps."),
    "AppReportId": ("foreign_key", "Reports", "Report id within an app."),
    "ObjectId": ("foreign_key", "Items", "The object the action targeted."),
    "ActivityId": ("correlation", "ActivityEvents", "Correlates events from one action."),
    "RequestId": ("correlation", "ActivityEvents", "Correlates events from one request."),
}


def _load_events(data_dir: Path) -> list[dict]:
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
            f"No collected events under {data_dir}/activity_events/. Run main.py first."
        )
    return rows


def _encode_nested(value):
    # Keep flat formats lossless: serialize objects/arrays, leave scalars as-is.
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def build_frame(events: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(events)
    # Stable column order: known keys first (in declared order), then the rest
    # alphabetically — so the wide, sparse schema stays readable.
    key_cols = [c for c in KEY_FIELDS if c in df.columns]
    other_cols = sorted(c for c in df.columns if c not in key_cols)
    df = df[key_cols + other_cols]
    for col in df.columns:
        df[col] = df[col].map(_encode_nested)
    return df


def key_map_frame(df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            (field, role, relates_to, notes, field in df.columns)
            for field, (role, relates_to, notes) in KEY_FIELDS.items()
        ],
        columns=["field", "role", "relates_to", "notes", "present_in_data"],
    )


def export(data_dir: Path, want_parquet: bool, want_csv: bool) -> None:
    df = build_frame(_load_events(data_dir))
    out_dir = data_dir / "raw"
    out_dir.mkdir(parents=True, exist_ok=True)

    if want_parquet:
        try:
            df.to_parquet(out_dir / "activity_events_raw.parquet", index=False)
            print(f"activity_events_raw.parquet: {len(df)} rows, {len(df.columns)} cols")
        except ImportError:
            print(
                "Parquet skipped — pyarrow not installed. Run `pip install pyarrow` "
                "for the integrity-safe format."
            )

    if want_csv:
        # UTF-8 BOM so Excel renders Swedish characters correctly; quote every
        # value so commas, quotes, and newlines inside fields never break rows.
        df.to_csv(
            out_dir / "activity_events_raw.csv",
            index=False,
            encoding="utf-8-sig",
            quoting=csv.QUOTE_ALL,
            lineterminator="\n",
        )
        print(f"activity_events_raw.csv: {len(df)} rows, {len(df.columns)} cols")

    key_map_frame(df).to_csv(out_dir / "key_map.csv", index=False, encoding="utf-8-sig")
    print(f"key_map.csv: {len(KEY_FIELDS)} key fields documented")


def _parse_args() -> argparse.Namespace:
    default_data = Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser()
    parser = argparse.ArgumentParser(
        description="Export raw activity events (no aggregation) + key map."
    )
    parser.add_argument("--data-dir", type=Path, default=default_data)
    parser.add_argument("--no-parquet", action="store_true", help="Skip Parquet output.")
    parser.add_argument("--no-csv", action="store_true", help="Skip CSV output.")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    export(args.data_dir, want_parquet=not args.no_parquet, want_csv=not args.no_csv)
