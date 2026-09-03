"""``model-lineage`` command implementation: for every semantic model
(dataset) in the tenant, resolves each table's source -- a warehouse
table/view read directly, or reached through one or more Gen1 dataflows --
via the Scanner API's per-table M code and the Dataflow export API.

Requires the tenant admin setting "Enhance admin APIs responses with DAX
and mashup expressions" (which itself requires "...with detailed metadata")
to be enabled; otherwise the scan succeeds but every table comes back with
status "no_expression_available". See the root README's Power BI section.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from .auth import PowerBITokenProvider
from .config import load_settings
from .model_lineage import DataflowCache, TableSourceResult, resolve_table_source
from .powerbi_client import PowerBIAdminClient
from .scanner import list_workspace_ids, scan_workspaces

_SUMMARY_FIELDS = [
    "workspace_id",
    "workspace_name",
    "dataset_id",
    "dataset_name",
    "table_name",
    "status",
    "hop_count",
    "connector",
    "resolved_table",
    "note",
]

_NO_EXPRESSION_NOTE = (
    "Scanner API returned no M expression for this table. Most likely cause: "
    "the tenant setting 'Enhance admin APIs responses with DAX and mashup "
    "expressions' is not enabled -- see the root README's Power BI section."
)


def run(data_dir: Path, interactive: bool = False, scan_timeout_seconds: int = 600) -> None:
    settings = load_settings(force_interactive=interactive)
    tokens = PowerBITokenProvider(settings)
    client = PowerBIAdminClient(tokens)
    dataflow_cache = DataflowCache(client)

    out_dir = data_dir / "model_lineage"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    jsonl_path = out_dir / f"model_lineage_{stamp}.jsonl"
    summary_path = out_dir / f"model_source_usage_{stamp}.csv"

    workspace_ids = list(list_workspace_ids(client))
    summary_rows = []
    scan_batch_errors = 0

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for workspace in scan_workspaces(client, workspace_ids, scan_timeout_seconds):
            if "scan_batch_error" in workspace:
                scan_batch_errors += 1
                fh.write(json.dumps(workspace, ensure_ascii=False) + "\n")
                continue

            for record, summary in _resolve_workspace_datasets(workspace, dataflow_cache):
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                summary_rows.append(summary)

    with summary_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=_SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"Scanned {len(workspace_ids)} workspaces ({scan_batch_errors} batch errors)")
    print(f"Resolved sources for {len(summary_rows)} tables -> {jsonl_path}")
    print(f"Summary -> {summary_path}")


def _resolve_workspace_datasets(workspace: dict, dataflow_cache: DataflowCache):
    workspace_id = workspace.get("id", "")
    workspace_name = workspace.get("name", "")

    for dataset in workspace.get("datasets", []):
        dataset_id = dataset.get("id", "")
        dataset_name = dataset.get("name", "")
        tables = dataset.get("tables", [])
        dataset_siblings = _dataset_sibling_expressions(tables)

        for table in tables:
            table_name = table.get("name", "")
            expression = _table_expression(table)

            if expression is None:
                result = TableSourceResult(
                    workspace_id=workspace_id,
                    workspace_name=workspace_name,
                    dataset_id=dataset_id,
                    dataset_name=dataset_name,
                    table_name=table_name,
                    status="no_expression_available",
                    note=_NO_EXPRESSION_NOTE,
                )
            else:
                result = resolve_table_source(
                    workspace_id,
                    workspace_name,
                    dataset_id,
                    dataset_name,
                    table_name,
                    expression,
                    dataset_siblings,
                    dataflow_cache,
                )

            record = asdict(result)
            yield record, _summarize(record)


def _table_expression(table: dict) -> str | None:
    # Documented Scanner API shape: table.source -> [{"expression": "..."}].
    source = table.get("source")
    if not isinstance(source, list) or not source:
        return None
    first = source[0]
    return first.get("expression") if isinstance(first, dict) else None


def _dataset_sibling_expressions(tables: list[dict]) -> dict[str, str]:
    siblings = {}
    for t in tables:
        expr = _table_expression(t)
        name = t.get("name")
        if name and expr:
            siblings[name] = expr
    return siblings


def _summarize(record: dict) -> dict:
    direct = record.get("direct_sources") or []
    connector = "; ".join(s["connector"] for s in direct) if direct else None
    resolved_table = direct[0]["resolved_table"] if len(direct) == 1 else None
    return {
        "workspace_id": record["workspace_id"],
        "workspace_name": record["workspace_name"],
        "dataset_id": record["dataset_id"],
        "dataset_name": record["dataset_name"],
        "table_name": record["table_name"],
        "status": record["status"],
        "hop_count": len(record.get("hops") or []),
        "connector": connector,
        "resolved_table": resolved_table,
        "note": record.get("note"),
    }
