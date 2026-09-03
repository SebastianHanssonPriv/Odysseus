"""Data-model lineage extraction for one Qlik Cloud app.

Pulls three things the Engine API exposes about how an app's data model was
built. Each has its own accuracy boundary — stated here rather than silently
assumed away, following the same discipline as the Power BI tool's analytics
module docstring:

  script  - GetScript(): the full load-script text. Exact.
  lineage - GetLineage(): LOAD/STORE statement provenance (discriminator +
            statement) recorded when the script last ran. Qlik's own
            documentation and community reports note this can come back
            empty, or with blank discriminator/statement pairs, for scripts
            using precedent load or certain control statements. An empty
            result means "not reported by this call for this script shape",
            not "this app has no data sources" — GetScript is the ground
            truth for what the script actually contains.
  tables  - GetTablesAndKeys(): tables, fields, and key/association info from
            the data model currently resident in the engine for this app.
            Exact for what is loaded; a script line count of zero apps still
            reports whatever the last successful reload left in memory.

A fourth output, qvd_field_usage, goes further: for each QVD the script
reads, which of its fields are confirmed present in the final data model.
That is not a single Engine API call -- it is script.py's parsed LOAD
statements cross-checked against the tables/keys above. See qvd_lineage.py
for the per-field confidence tiers (confirmed / derived_expression /
not_found_in_final_model / unresolved) and script_parser.PARSER_LIMITATIONS
for exactly what the parser does and does not evaluate.
"""

from __future__ import annotations

from .engine_client import QixEngineSession
from .qvd_lineage import resolve_qvd_field_usage, usages_as_dicts

# Positional params for Doc.GetTablesAndKeys, in the API's documented order:
# qWindowSize, qNullSize, qCellHeight, qSyntheticMode, qIncludeSysVars. The
# window/cell-size values only bound how many rows the engine would return to
# a UI viewer; they do not affect which tables/fields/keys exist. If a given
# tenant's engine version rejects this exact parameter list, treat it as a
# version-specific detail to verify against that tenant, not a design flaw —
# GetScript and GetLineage below do not depend on it.
_GET_TABLES_AND_KEYS_PARAMS = [
    {"qcx": 1000, "qcy": 1000},  # qWindowSize
    {"qcx": 0, "qcy": 0},  # qNullSize
    25,  # qCellHeight
    False,  # qSyntheticMode
    False,  # qIncludeSysVars
]


def extract_app_lineage(session: QixEngineSession, app_id: str, app_name: str) -> dict:
    doc = session.doc_handle

    script = session.call(doc, "GetScript")["qScript"]
    lineage = session.call(doc, "GetLineage").get("qLineage", [])

    tables: list = []
    keys: list = []
    tables_error: str | None = None
    try:
        tables_and_keys = session.call(doc, "GetTablesAndKeys", _GET_TABLES_AND_KEYS_PARAMS)
        tables = tables_and_keys.get("qtr", [])
        keys = tables_and_keys.get("qk", [])
    except Exception as exc:  # noqa: BLE001 - degrade to script+lineage only
        # GetScript/GetLineage are the high-confidence outputs; a failure here
        # (e.g. a tenant's engine version wanting a different parameter shape)
        # should not discard those, only be reported alongside them.
        tables_error = str(exc)

    qvd_field_usage: list = []
    qvd_lineage_warnings: list = []
    if tables_error is None:
        # Only meaningful once we actually have the final model to check
        # against -- without it every field would wrongly read as
        # "target_table_unresolved" (looks like a parser failure, when the
        # real cause is the missing GetTablesAndKeys result above).
        usages, qvd_lineage_warnings = resolve_qvd_field_usage(script, tables)
        qvd_field_usage = usages_as_dicts(usages)

    return {
        "app_id": app_id,
        "app_name": app_name,
        "script": script,
        "lineage": lineage,
        "tables": tables,
        "keys": keys,
        "tables_error": tables_error,
        "qvd_field_usage": qvd_field_usage,
        "qvd_lineage_warnings": qvd_lineage_warnings,
    }
