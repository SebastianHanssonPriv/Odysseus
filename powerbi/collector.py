from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone

from .activity_events import fetch_activity_events
from .auth import PowerBITokenProvider
from .config import load_settings
from .powerbi_client import PowerBIAdminClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Pull one UTC day of Power BI activity events."
    )
    parser.add_argument(
        "--date",
        type=lambda s: datetime.strptime(s, "%Y-%m-%d").date(),
        default=(datetime.now(timezone.utc) - timedelta(days=1)).date(),
        help="UTC day to pull (YYYY-MM-DD). Defaults to yesterday.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Enter credentials in a secure masked window (local development).",
    )
    return parser.parse_args()


def collect(day: date, interactive: bool = False) -> int:
    settings = load_settings(force_interactive=interactive)
    tokens = PowerBITokenProvider(settings)
    client = PowerBIAdminClient(tokens)

    out_dir = settings.output_dir / "activity_events"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"activity_events_{day.isoformat()}.jsonl"

    count = 0
    # JSONL now; in a Fabric Notebook this write becomes a Lakehouse Delta append.
    with out_file.open("w", encoding="utf-8") as fh:
        for event in fetch_activity_events(client, day):
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            count += 1

    print(f"Collected {count} activity events for {day.isoformat()} -> {out_file}")
    return count


if __name__ == "__main__":
    args = _parse_args()
    collect(args.date, interactive=args.interactive)
