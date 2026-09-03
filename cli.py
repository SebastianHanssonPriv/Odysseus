"""Unified entry point for the Odysseus governance toolkit.

Two platforms, one program:
    odysseus powerbi collect --interactive
    odysseus powerbi raw-export
    odysseus powerbi analytics
    odysseus powerbi model-lineage --interactive
    odysseus qlik extract --interactive

Heavy modules are imported lazily inside each branch so --help and argument
parsing stay fast, and a subcommand only loads what its own platform needs.
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
        prog="odysseus",
        description="Power BI and Qlik Cloud governance toolkit.",
    )
    platform = parser.add_subparsers(dest="platform", required=True)

    powerbi = platform.add_parser("powerbi", help="Power BI usage collection and analytics.")
    powerbi_cmd = powerbi.add_subparsers(dest="command", required=True)

    collect = powerbi_cmd.add_parser("collect", help="Collect one UTC day of activity events.")
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

    raw = powerbi_cmd.add_parser("raw-export", help="Export raw events + key map (no aggregation).")
    raw.add_argument("--data-dir", type=Path, default=None)
    raw.add_argument("--no-parquet", action="store_true", help="Skip Parquet output.")
    raw.add_argument("--no-csv", action="store_true", help="Skip CSV output.")

    analytics = powerbi_cmd.add_parser("analytics", help="Build the exact usage aggregations.")
    analytics.add_argument("--data-dir", type=Path, default=None)

    model_lineage = powerbi_cmd.add_parser(
        "model-lineage",
        help=(
            "For every semantic model, resolve each table's source (a warehouse "
            "table/view, direct or via a Gen1 dataflow) from its M code."
        ),
    )
    model_lineage.add_argument("--data-dir", type=Path, default=None)
    model_lineage.add_argument(
        "--interactive",
        action="store_true",
        help="Enter credentials in a secure masked window (local development).",
    )
    model_lineage.add_argument(
        "--scan-timeout",
        type=int,
        default=600,
        help="Seconds to wait for each Scanner API batch to finish (default: 600).",
    )

    qlik = platform.add_parser("qlik", help="Qlik Cloud data-model lineage extraction.")
    qlik_cmd = qlik.add_subparsers(dest="command", required=True)

    extract = qlik_cmd.add_parser(
        "extract",
        help=(
            "Pull script, lineage, table/key info, and per-QVD field usage "
            "for every app in the tenant."
        ),
    )
    extract.add_argument("--data-dir", type=Path, default=None)
    extract.add_argument(
        "--interactive",
        action="store_true",
        help="Enter credentials in a secure masked window (local development).",
    )

    return parser


def main() -> None:
    args = build_parser().parse_args()

    if args.platform == "powerbi":
        if args.command == "collect":
            from powerbi import collector

            collector.collect(args.date, interactive=args.interactive)

        elif args.command == "raw-export":
            from powerbi import raw_export

            data_dir = args.data_dir or _default_data_dir()
            raw_export.export(
                data_dir, want_parquet=not args.no_parquet, want_csv=not args.no_csv
            )

        elif args.command == "analytics":
            from powerbi import analytics

            analytics.run(args.data_dir or _default_data_dir())

        elif args.command == "model-lineage":
            from powerbi import model_lineage_collector

            model_lineage_collector.run(
                args.data_dir or _default_data_dir(),
                interactive=args.interactive,
                scan_timeout_seconds=args.scan_timeout,
            )

    elif args.platform == "qlik":
        if args.command == "extract":
            from qlik import collector

            collector.run(args.data_dir or _default_data_dir(), interactive=args.interactive)


if __name__ == "__main__":
    main()
