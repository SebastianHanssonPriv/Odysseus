"""``extract`` command implementation: pulls script/lineage/table info for
every app in the tenant and writes a JSONL snapshot plus two summary CSVs.

Unlike the Power BI activity log, Qlik's Engine API has no ~28-day retention
cliff forcing a daily cadence — lineage reflects the app's current state, not
a rolling event window. Snapshots are still date-stamped and accumulated
rather than overwritten in place, so lineage/schema drift across apps stays
visible over time; how often to run this is an operational choice, not one
this tool makes for you.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

from auth import QlikTokenProvider
from config import load_settings
from engine_client import QixEngineSession
from lineage import extract_app_lineage
from rest_client import QlikRestClient, list_apps

_SUMMARY_FIELDS = [
    "app_id",
    "app_name",
    "table_count",
    "field_count",
    "key_count",
    "lineage_statement_count",
    "script_line_count",
    "qvd_sources_count",
    "qvd_fields_confirmed_count",
    "qvd_fields_unresolved_count",
    "extracted_at",
    "error",
]

_QVD_USAGE_FIELDS = [
    "app_id",
    "app_name",
    "qvd_source",
    "target_table",
    "source_field",
    "final_field",
    "simple_passthrough",
    "status",
]

# Statuses that mean "this field is confirmed to exist, under this name, in
# the final data model" -- see qvd_lineage.py for the full status taxonomy.
_CONFIRMED_STATUSES = {"confirmed", "confirmed_case_mismatch", "derived_expression"}


def run(data_dir: Path, interactive: bool = False) -> None:
    settings = load_settings(force_interactive=interactive)
    tokens = QlikTokenProvider(settings)
    rest = QlikRestClient(tokens)

    out_dir = data_dir / "lineage"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jsonl_path = out_dir / f"lineage_{stamp}.jsonl"
    summary_path = out_dir / f"lineage_summary_{stamp}.csv"
    qvd_usage_path = out_dir / f"qvd_field_usage_{stamp}.csv"

    summary_rows = []
    qvd_usage_rows = []
    with jsonl_path.open("w", encoding="utf-8") as fh:
        for item in list_apps(rest):
            app_id = item.get("resourceId")
            app_name = item.get("name", "")
            if not app_id:
                continue

            record = _extract_one(tokens, app_id, app_name)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            summary_rows.append(_summarize(record))
            qvd_usage_rows.extend(_qvd_usage_rows(record))

    with summary_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    with qvd_usage_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_QVD_USAGE_FIELDS)
        writer.writeheader()
        writer.writerows(qvd_usage_rows)

    print(f"Extracted lineage for {len(summary_rows)} apps -> {jsonl_path}")
    print(f"Summary -> {summary_path}")
    print(f"QVD field usage ({len(qvd_usage_rows)} rows) -> {qvd_usage_path}")


def _extract_one(tokens: QlikTokenProvider, app_id: str, app_name: str) -> dict:
    extracted_at = datetime.now(timezone.utc).isoformat()
    try:
        with QixEngineSession(tokens, app_id) as session:
            record = extract_app_lineage(session, app_id, app_name)
        record["extracted_at"] = extracted_at
        record["error"] = None
    except Exception as exc:  # noqa: BLE001 - one app's failure must not abort the run
        # Covers websocket/auth/permission failures (e.g. the M2M client
        # lacking access to this app's space) alongside EngineApiError, so a
        # single inaccessible app is recorded and skipped rather than
        # stopping extraction for the rest of the tenant.
        record = {
            "app_id": app_id,
            "app_name": app_name,
            "script": None,
            "lineage": [],
            "tables": [],
            "keys": [],
            "tables_error": None,
            "qvd_field_usage": [],
            "qvd_lineage_warnings": [],
            "extracted_at": extracted_at,
            "error": str(exc),
        }
    return record


def _summarize(record: dict) -> dict:
    tables = record.get("tables") or []
    field_count = sum(len(t.get("qFields", [])) for t in tables)
    script = record.get("script")
    error = record.get("error") or record.get("tables_error")
    usage = record.get("qvd_field_usage") or []
    return {
        "app_id": record["app_id"],
        "app_name": record["app_name"],
        "table_count": len(tables),
        "field_count": field_count,
        "key_count": len(record.get("keys") or []),
        "lineage_statement_count": len(record.get("lineage") or []),
        "script_line_count": (script.count("\n") + 1) if script else 0,
        "qvd_sources_count": len({u["qvd_source"] for u in usage}),
        "qvd_fields_confirmed_count": sum(1 for u in usage if u["status"] in _CONFIRMED_STATUSES),
        "qvd_fields_unresolved_count": sum(1 for u in usage if u["status"] not in _CONFIRMED_STATUSES),
        "extracted_at": record["extracted_at"],
        "error": error,
    }


def _qvd_usage_rows(record: dict) -> list[dict]:
    return [
        {
            "app_id": record["app_id"],
            "app_name": record["app_name"],
            **usage,
        }
        for usage in (record.get("qvd_field_usage") or [])
    ]
