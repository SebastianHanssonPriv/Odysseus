"""Per-table source resolution for Power BI semantic models (datasets).

Combines the scanner API's per-table M expression with the dataflow export
API's per-entity M code to answer, for each table in each semantic model,
which warehouse table/view (or other data source) it ultimately reads from
-- chasing through Gen1 dataflow references when a table's own source is
"Get Data > Power BI dataflows" rather than a direct connector call, and
through same-document staging-query references at both the dataset and the
dataflow level.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .dataflow_admin import export_dataflow
from .mashup_parser import MAX_REFERENCE_DEPTH, resolve_source, split_shared_queries

# Dataflow-boundary hops (dataset -> dataflow -> dataflow -> ...) are capped
# independently of mashup_parser's own same-document reference-chasing cap,
# since a dataflow can itself be built on another dataflow (linked entities).
_MAX_DATAFLOW_HOPS = MAX_REFERENCE_DEPTH


@dataclass
class TableSourceResult:
    workspace_id: str
    workspace_name: str
    dataset_id: str
    dataset_name: str
    table_name: str
    status: str
    hops: list[dict] = field(default_factory=list)
    direct_sources: list[dict] = field(default_factory=list)
    note: str | None = None


class DataflowCache:
    """Fetches and parses each dataflow's export at most once per run."""

    def __init__(self, client):
        self._client = client
        self._queries: dict[str, dict[str, str] | None] = {}  # None = export/parse failed
        self._errors: dict[str, str] = {}

    def queries_for(self, dataflow_id: str) -> dict[str, str] | None:
        if dataflow_id not in self._queries:
            try:
                definition = export_dataflow(self._client, dataflow_id)
                document = definition.get("pbi:mashup", {}).get("document", "")
                if not document:
                    self._queries[dataflow_id] = None
                    self._errors[dataflow_id] = (
                        "export succeeded but no pbi:mashup.document found in the "
                        "response -- unexpected/unverified shape, see "
                        "dataflow_admin.export_dataflow's docstring"
                    )
                else:
                    self._queries[dataflow_id] = split_shared_queries(document)
            except Exception as exc:  # noqa: BLE001 - one dataflow's failure must not abort the run
                self._queries[dataflow_id] = None
                self._errors[dataflow_id] = str(exc)
        return self._queries[dataflow_id]

    def error_for(self, dataflow_id: str) -> str | None:
        return self._errors.get(dataflow_id)


def resolve_table_source(
    workspace_id: str,
    workspace_name: str,
    dataset_id: str,
    dataset_name: str,
    table_name: str,
    expression: str,
    dataset_siblings: dict[str, str],
    dataflow_cache: DataflowCache,
) -> TableSourceResult:
    base = dict(
        workspace_id=workspace_id,
        workspace_name=workspace_name,
        dataset_id=dataset_id,
        dataset_name=dataset_name,
        table_name=table_name,
    )
    hops: list[dict] = []
    current_expr = expression
    current_siblings = dataset_siblings
    current_label = f"dataset:{dataset_name}.{table_name}"

    for _ in range(_MAX_DATAFLOW_HOPS + 1):
        resolution = resolve_source(current_expr, sibling_queries=current_siblings)

        if resolution.status == "dataflow_reference":
            ref = resolution.dataflow_ref
            hops.append(
                {
                    "from": current_label,
                    "dataflow_workspace_id": ref.workspace_id,
                    "dataflow_id": ref.dataflow_id,
                    "entity": ref.entity,
                    "same_document_hops": resolution.reference_chain,
                }
            )
            if not ref.dataflow_id or not ref.entity:
                return TableSourceResult(
                    **base,
                    status="dataflow_reference_incomplete",
                    hops=hops,
                    note="dataflow reference found but workspaceId/dataflowId/entity could not all be extracted from the M code",
                )

            queries = dataflow_cache.queries_for(ref.dataflow_id)
            if queries is None:
                return TableSourceResult(
                    **base,
                    status="dataflow_export_failed",
                    hops=hops,
                    note=dataflow_cache.error_for(ref.dataflow_id),
                )
            if ref.entity not in queries:
                return TableSourceResult(
                    **base,
                    status="dataflow_entity_not_found",
                    hops=hops,
                    note=f"entity '{ref.entity}' not found among {len(queries)} parsed queries in dataflow {ref.dataflow_id}",
                )

            current_expr = queries[ref.entity]
            current_siblings = queries
            current_label = f"dataflow:{ref.dataflow_id}.{ref.entity}"
            continue

        if resolution.status in ("direct_source", "multiple_direct_sources"):
            return TableSourceResult(
                **base,
                status=resolution.status,
                hops=hops,
                direct_sources=[asdict(s) for s in resolution.direct_sources],
            )

        return TableSourceResult(**base, status="unresolved", hops=hops, note=resolution.note)

    return TableSourceResult(
        **base,
        status="max_hops_exceeded",
        hops=hops,
        note=f"dataflow reference chain exceeded {_MAX_DATAFLOW_HOPS} hops",
    )
