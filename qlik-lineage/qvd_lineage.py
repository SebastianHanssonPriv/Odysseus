"""Joins the QVD-load statements parsed from a script against the app's
actual final data model to answer: for each QVD this app's script reads,
which of its fields are confirmed present in the final data model.

script_parser.py only knows what the SCRIPT says it loads; this module
checks that against what the ENGINE says is actually resident (from
GetTablesAndKeys), which is what turns "the script mentions this field"
into "this field is confirmed used in the final model" -- a field can be
loaded and then dropped, joined away, or simply no longer exist if the
parser's table/rename resolution missed a construct it doesn't track.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from script_parser import LoadedField, QvdLoadStatement, parse_qvd_loads

# Per-field resolution status, most to least confident:
#   confirmed                 Simple pass-through field, found under its
#                              final name in its resolved target table.
#   confirmed_case_mismatch   Found only via a case-insensitive match --
#                              worth checking, since Qlik field names are
#                              case-sensitive.
#   derived_expression        The script computes this field from an
#                              expression rather than passing the QVD column
#                              through unchanged; found in the final model,
#                              but "used" here means "used as an input to a
#                              computation", not "passed through as-is".
#   not_found_in_final_model  Present in the script's LOAD but not present
#                              under this name in the resolved target table
#                              -- likely dropped, joined away, or renamed by
#                              a construct this parser does not track (see
#                              script_parser.PARSER_LIMITATIONS).
#   target_table_unresolved   The parser could not determine which final
#                              table this LOAD's fields land in, so no
#                              lookup against the model was possible.
#   wildcard_unresolved       LOAD * FROM ... (qvd) -- the QVD is confirmed
#                              used, but its field list is not resolvable
#                              from script text alone.


@dataclass
class FieldUsage:
    qvd_source: str
    target_table: str | None
    source_field: str
    final_field: str
    simple_passthrough: bool
    status: str


def resolve_qvd_field_usage(script: str, final_tables: list[dict]) -> tuple[list[FieldUsage], list[str]]:
    """final_tables: the "qtr" list from a GetTablesAndKeys result, i.e.
    [{"qName": "Sales", "qFields": [{"qName": "OrderId"}, ...]}, ...].
    """
    statements, warnings = parse_qvd_loads(script)
    fields_by_table = {
        t.get("qName"): {f.get("qName") for f in t.get("qFields", [])} for t in final_tables
    }

    usages: list[FieldUsage] = []
    for stmt in statements:
        if stmt.wildcard:
            usages.append(
                FieldUsage(
                    qvd_source=stmt.source_path,
                    target_table=stmt.target_table,
                    source_field="*",
                    final_field="*",
                    simple_passthrough=False,
                    status="wildcard_unresolved",
                )
            )
            continue
        usages.extend(_resolve_field(stmt, f, fields_by_table) for f in stmt.fields)

    return usages, warnings


def _resolve_field(stmt: QvdLoadStatement, loaded_field: LoadedField, fields_by_table: dict) -> FieldUsage:
    base = dict(
        qvd_source=stmt.source_path,
        target_table=stmt.target_table,
        source_field=loaded_field.source_name,
        final_field=loaded_field.final_name,
        simple_passthrough=loaded_field.simple_passthrough,
    )

    table_fields = fields_by_table.get(stmt.target_table) if stmt.target_table else None
    if table_fields is None:
        return FieldUsage(**base, status="target_table_unresolved")

    if loaded_field.final_name in table_fields:
        status = "confirmed" if loaded_field.simple_passthrough else "derived_expression"
        return FieldUsage(**base, status=status)

    if loaded_field.final_name.lower() in {f.lower() for f in table_fields}:
        return FieldUsage(**base, status="confirmed_case_mismatch")

    return FieldUsage(**base, status="not_found_in_final_model")


def usages_as_dicts(usages: list[FieldUsage]) -> list[dict]:
    return [asdict(u) for u in usages]
