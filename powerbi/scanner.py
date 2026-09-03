"""Tenant-wide metadata scan via the Power BI Admin "Scanner API".

Workflow (Admin - WorkspaceInfo):
  1. Enumerate every workspace (GetGroupsAsAdmin, paginated).
  2. Trigger a scan over batches of up to 100 workspaces at a time
     (PostWorkspaceInfo), asking for dataset schema + dataset expressions
     (the per-table M code this tool actually needs) + datasource details +
     lineage.
  3. Poll GetScanStatus until the scan finishes.
  4. Fetch GetScanResult for the finished scan's full JSON.

datasetExpressions=True -- the per-table M code -- requires a tenant admin
to have enabled BOTH "Enhance admin APIs responses with detailed metadata"
AND "...with DAX and mashup expressions" in the Admin portal's tenant
settings. If either is off, this workflow still succeeds and returns
workspace/dataset inventory, but every table's M expression comes back
missing -- not an error this tool can detect from here, just the tenant's
own configuration. See the root README's Power BI section.
"""

from __future__ import annotations

import time
from typing import Iterator

from .powerbi_client import PowerBIAdminClient

_WORKSPACE_PAGE_SIZE = 5000
_SCAN_BATCH_SIZE = 100
_SCAN_POLL_INTERVAL_SECONDS = 5


def list_workspace_ids(client: PowerBIAdminClient) -> Iterator[str]:
    """Every non-personal workspace in the tenant, paginated."""
    skip = 0
    while True:
        payload = client.get(
            "groups",
            params={
                "$top": _WORKSPACE_PAGE_SIZE,
                "$skip": skip,
                "$filter": "type eq 'Workspace'",
            },
        )
        rows = payload.get("value", [])
        for row in rows:
            workspace_id = row.get("id")
            if workspace_id:
                yield workspace_id
        if len(rows) < _WORKSPACE_PAGE_SIZE:
            break
        skip += _WORKSPACE_PAGE_SIZE


def _batched(items: list[str], size: int) -> Iterator[list[str]]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def scan_workspaces(
    client: PowerBIAdminClient,
    workspace_ids: list[str],
    scan_timeout_seconds: int = 600,
) -> Iterator[dict]:
    """Yield each scanned workspace's JSON (datasets/dataflows/reports/...).

    One batch's failure or timeout is reported and skipped rather than
    aborting the whole tenant scan, matching the per-app resilience pattern
    in the Qlik tool's collector.
    """
    for batch in _batched(workspace_ids, _SCAN_BATCH_SIZE):
        try:
            yield from _scan_one_batch(client, batch, scan_timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - one batch must not abort the run
            yield {"scan_batch_error": str(exc), "workspace_ids": batch}


def _scan_one_batch(client: PowerBIAdminClient, batch: list[str], scan_timeout_seconds: int) -> Iterator[dict]:
    trigger = client.post(
        "workspaces/getInfo",
        params={
            "lineage": "True",
            "datasourceDetails": "True",
            "datasetSchema": "True",
            "datasetExpressions": "True",
            "getArtifactUsers": "False",
        },
        json={"workspaces": batch},
    )
    scan_id = trigger["id"]

    deadline = time.time() + scan_timeout_seconds
    while True:
        status = client.get(f"workspaces/scanStatus/{scan_id}")
        state = status.get("status")
        if state == "Succeeded":
            break
        if state in ("Failed", "Undefined"):
            raise RuntimeError(f"Scan {scan_id} ended with status {state}")
        if time.time() > deadline:
            raise TimeoutError(f"Scan {scan_id} did not finish within {scan_timeout_seconds}s")
        time.sleep(_SCAN_POLL_INTERVAL_SECONDS)

    result = client.get(f"workspaces/scanResult/{scan_id}")
    yield from result.get("workspaces", [])
