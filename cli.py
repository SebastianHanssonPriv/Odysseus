"""Unified entry point for the Power BI usage tool.

One executable, three subcommands:
    powerbi-usage collect --interactive
    powerbi-usage raw-export
    powerbi-usage analytics

Heavy modules are imported lazily inside each branch so that --help and
argument parsing stay fast, and a subcommand only loads what it needs
(e.g. analytics/raw-export don't pull in the Azure auth stack).
"""

from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def _default_data_dir() -> Path:
    return Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="powerbi-usage",
        description="Collect and analyze Power BI usage from the Admin APIs.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Collect one UTC day of activity events.")
    collect.add_argument(
        "--date",
        type=_date,
        default=(datetime.now(timezone.utc) - timedelta(days=1)).date(),
        help="UTC day to pull (YYYY-MM-DD). Defaults to yesterday.",
    )
    collect.add_argument(
        "--interactive",
        action="store_true",
        help="Enter credentials in a secure masked window (local development).",
    )

    raw = sub.add_parser("raw-export", help="Export raw events + key map (no aggregation).")
    raw.add_argument("--data-dir", type=Path, default=None)
    raw.add_argument("--no-parquet", action="store_true", help="Skip Parquet output.")
    raw.add_argument("--no-csv", action="store_true", help="Skip CSV output.")

    analytics = sub.add_parser("analytics", help="Build the exact usage aggregations.")
    analytics.add_argument("--data-dir", type=Path, default=None)

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.command == "collect":
        import main as collector

        collector.collect(args.date, interactive=args.interactive)

    elif args.command == "raw-export":
        import raw_export

        data_dir = args.data_dir or _default_data_dir()
        raw_export.export(
            data_dir, want_parquet=not args.no_parquet, want_csv=not args.no_csv
        )

    elif args.command == "analytics":
        import analytics

        analytics.run(args.data_dir or _default_data_dir())


if __name__ == "__main__":
    main()
