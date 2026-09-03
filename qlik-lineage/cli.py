"""Unified entry point for the Qlik data-model lineage tool.

One executable, one subcommand today:
    qlik-lineage extract --interactive

Built on engine_client.py, a reusable Engine API connector meant to be
imported by future Qlik governance tools (usage, content inventory, etc.)
without each one re-solving auth and websocket connection handling.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def _default_data_dir() -> Path:
    return Path(os.environ.get("OUTPUT_DIR", "./data")).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="qlik-lineage",
        description="Extract Qlik Cloud app data-model lineage via the Engine API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    extract = sub.add_parser(
        "extract",
        help="Pull script, lineage, and table/key info for every app in the tenant.",
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

    if args.command == "extract":
        import collector

        collector.run(args.data_dir or _default_data_dir(), interactive=args.interactive)


if __name__ == "__main__":
    main()
