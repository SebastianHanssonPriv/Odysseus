"""Gen1 dataflow definition export via the Power BI Admin API.

Dataflow inventory (id, name, workspace) already comes back as part of each
scanned workspace's payload from scanner.scan_workspaces() -- no separate
listing call is needed. This module only handles pulling one dataflow's
full definition (entities + the actual M code for each), which the scanner
does not include, since datasetSchema/datasetExpressions apply to datasets,
not dataflows.
"""

from __future__ import annotations

from .powerbi_client import PowerBIAdminClient


def export_dataflow(client: PowerBIAdminClient, dataflow_id: str) -> dict:
    """Return the dataflow's definition JSON (entities[], "pbi:mashup", ...).

    Based on the Common Data Model model.json shape Power BI dataflows are
    stored in: a root-level "pbi:mashup" object whose "document" field holds
    one M section with a `shared <EntityName> = <expr>;` member per entity
    -- not verified against a live tenant response by this tool's author,
    so callers should treat an unexpected shape as "flag it", not "assume
    it's empty". See mashup_parser.split_shared_queries and the root
    README's Power BI section.
    """
    return client.get(f"dataflows/{dataflow_id}/export")
