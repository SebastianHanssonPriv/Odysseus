import json
import csv
import websocket  # pip install websocket-client
import os
import datetime
import re

# ---------- config ----------
OUTPUT_DIR = r"C:\Users\sebastian.hansson\OneDrive - BUFAB\_OutputFolder"
TENANT  = "OUR.eu.qlikcloud.com"   # full host, region included
API_KEY = "DUMMY"
APP_ID  = "FAKE"

# ---------- run switches ----------
EXPORT_MEASURES   = True
EXPORT_DIMENSIONS = True
EXPORT_VARIABLES  = True
EXPORT_SCRIPT     = True
EXPORT_VISUALS    = True


class Engine:
    def __init__(self, app_id):
        self.id = 0
        self.ws = websocket.create_connection(
            f"wss://{TENANT}/app/{app_id}",
            header=[f"Authorization: Bearer {API_KEY}"],
        )
        self.ws.recv()  # consume OnConnected

    def call(self, handle, method, params):
        self.id += 1
        self.ws.send(json.dumps({
            "jsonrpc": "2.0", "id": self.id,
            "handle": handle, "method": method, "params": params,
        }))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]


def safe(s):  # strip characters Windows won't allow in a filename
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("_")


def write_csv(path, rows):
    if not rows:
        print(f"  (nothing to write for {os.path.basename(path)})")
        return
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# ---------- 1. master measures ----------
def export_measures():
    list_def = {"qInfo": {"qType": "MeasureList"},
                "qMeasureListDef": {"qType": "measure"}}
    obj_h = eng.call(app_h, "CreateSessionObject", [list_def])["qReturn"]["qHandle"]
    items = eng.call(obj_h, "GetLayout", [])["qLayout"]["qMeasureList"]["qItems"]
    rows = []
    for it in items:
        mid = it["qInfo"]["qId"]
        mh = eng.call(app_h, "GetMeasure", [mid])["qReturn"]["qHandle"]
        p = eng.call(mh, "GetProperties", [])["qProp"]
        meta = p.get("qMetaDef", {})
        meas = p.get("qMeasure", {})
        fmt = meas.get("qNumFormat", {})
        rows.append({
            "id": mid,
            "name": meta.get("title", ""),
            "label": meas.get("qLabel", ""),
            "label_expression": meas.get("qLabelExpression", ""),
            "expression": meas.get("qDef", ""),
            "description": meta.get("description", ""),
            "tags": ", ".join(meta.get("tags", [])),
            "format_type": fmt.get("qType", ""),
            "format_pattern": fmt.get("qFmt", ""),
            "coloring": json.dumps(meas.get("coloring", {})) if meas.get("coloring") else "",
            "raw_json": json.dumps(p, ensure_ascii=False),
        })
    path = os.path.join(OUTPUT_DIR, f"master_measures_{prefix}.csv")
    write_csv(path, rows)
    print(f"Exported {len(rows)} master measures -> {path}")


# ---------- 2. master dimensions ----------
def export_dimensions():
    list_def = {"qInfo": {"qType": "DimensionList"},
                "qDimensionListDef": {"qType": "dimension"}}
    obj_h = eng.call(app_h, "CreateSessionObject", [list_def])["qReturn"]["qHandle"]
    items = eng.call(obj_h, "GetLayout", [])["qLayout"]["qDimensionList"]["qItems"]
    rows = []
    for it in items:
        did = it["qInfo"]["qId"]
        dh = eng.call(app_h, "GetDimension", [did])["qReturn"]["qHandle"]
        p = eng.call(dh, "GetProperties", [])["qProp"]
        meta = p.get("qMetaDef", {})
        dim = p.get("qDim", {})
        rows.append({
            "id": did,
            "name": meta.get("title", ""),
            "grouping": dim.get("qGrouping", ""),          # N = single, H = drill-down
            "fields": "; ".join(dim.get("qFieldDefs", [])),
            "field_labels": "; ".join(dim.get("qFieldLabels", [])),
            "label_expression": dim.get("qLabelExpression", ""),
            "description": meta.get("description", ""),
            "tags": ", ".join(meta.get("tags", [])),
            "raw_json": json.dumps(p, ensure_ascii=False),
        })
    path = os.path.join(OUTPUT_DIR, f"master_dimensions_{prefix}.csv")
    write_csv(path, rows)
    print(f"Exported {len(rows)} master dimensions -> {path}")


# ---------- 3. variables ----------
def export_variables():
    list_def = {"qInfo": {"qType": "VariableList"},
                "qVariableListDef": {"qType": "variable",
                                     "qShowReserved": True,
                                     "qShowConfig": True,
                                     "qData": {"tags": "/tags"}}}
    obj_h = eng.call(app_h, "CreateSessionObject", [list_def])["qReturn"]["qHandle"]
    items = eng.call(obj_h, "GetLayout", [])["qLayout"]["qVariableList"]["qItems"]
    rows = []
    for it in items:
        tags = (it.get("qData", {}) or {}).get("tags", []) or []
        rows.append({
            "id": it.get("qInfo", {}).get("qId", ""),
            "name": it.get("qName", ""),
            "definition": it.get("qDefinition", ""),
            "description": it.get("qDescription", ""),
            "is_script_created": it.get("qIsScriptCreated", False),
            "is_reserved": it.get("qIsReserved", False),
            "tags": ", ".join(tags),
            "raw_json": json.dumps(it, ensure_ascii=False),
        })
    path = os.path.join(OUTPUT_DIR, f"variables_{prefix}.csv")
    write_csv(path, rows)
    print(f"Exported {len(rows)} variables -> {path}")


# ---------- 4. load script ----------
def export_script():
    script = eng.call(app_h, "GetScript", [])["qScript"]
    path = os.path.join(OUTPUT_DIR, f"load_script_{prefix}.qvs")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write(script)
    print(f"Exported load script ({len(script.splitlines())} lines) -> {path}")


# ---------- 5. visuals ----------
def _walk(prop, sheet_title, rows):
    """Recurse the property tree so nested objects (containers) are captured too."""
    info = prop.get("qInfo", {})
    viz = prop.get("visualization") or info.get("qType", "")

    dims, meas = [], []
    hc = prop.get("qHyperCubeDef") or {}
    for d in hc.get("qDimensions", []):
        lib = d.get("qLibraryId", "")
        flds = d.get("qDef", {}).get("qFieldDefs", [])
        dims.append(lib or "; ".join(flds))
    for m in hc.get("qMeasures", []):
        lib = m.get("qLibraryId", "")
        expr = m.get("qDef", {}).get("qDef", "")
        meas.append(lib or expr)
    lo = prop.get("qListObjectDef") or {}
    if lo:
        lib = lo.get("qLibraryId", "")
        flds = lo.get("qDef", {}).get("qFieldDefs", [])
        dims.append(lib or "; ".join(flds))

    title = prop.get("title", "")
    if isinstance(title, dict):  # expression-based title
        title = title.get("qStringExpression", {}).get("qExpr", "")

    if viz:
        rows.append({
            "sheet": sheet_title,
            "object_id": info.get("qId", ""),
            "type": viz,
            "title": title,
            "dimensions": " | ".join(d for d in dims if d),
            "measures": " | ".join(m for m in meas if m),
            "dim_count": len([d for d in dims if d]),
            "measure_count": len([m for m in meas if m]),
            "raw_json": json.dumps(prop, ensure_ascii=False),
        })


def export_visuals():
    sheet_def = {
        "qInfo": {"qType": "SheetList"},
        "qAppObjectListDef": {
            "qType": "sheet",
            "qData": {"title": "/qMetaDef/title", "cells": "/cells"},
        },
    }
    sh = eng.call(app_h, "CreateSessionObject", [sheet_def])["qReturn"]["qHandle"]
    sheets = eng.call(sh, "GetLayout", [])["qLayout"]["qAppObjectList"]["qItems"]

    rows = []
    for s in sheets:
        sheet_title = s.get("qData", {}).get("title", s["qInfo"]["qId"])
        for cell in s.get("qData", {}).get("cells", []):
            obj_id = cell.get("name")
            if not obj_id:
                continue
            try:
                oh = eng.call(app_h, "GetObject", [obj_id])["qReturn"]["qHandle"]
                tree = eng.call(oh, "GetFullPropertyTree", [])["qPropEntry"]
            except RuntimeError:
                continue
            stack = [tree]
            while stack:
                entry = stack.pop()
                _walk(entry.get("qProperty", {}), sheet_title, rows)
                stack.extend(entry.get("qChildren", []) or [])

    path = os.path.join(OUTPUT_DIR, f"visuals_{prefix}.csv")
    write_csv(path, rows)
    print(f"Exported {len(rows)} visual objects across {len(sheets)} sheets -> {path}")


# ---------- run ----------
if not (EXPORT_MEASURES or EXPORT_DIMENSIONS or EXPORT_VARIABLES or EXPORT_SCRIPT or EXPORT_VISUALS):
    raise SystemExit("All export switches are False - nothing to do.")

eng = Engine(APP_ID)
app_h = eng.call(-1, "OpenDoc", [APP_ID])["qReturn"]["qHandle"]
app_title = eng.call(app_h, "GetAppLayout", [])["qLayout"].get("qTitle", APP_ID)
stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
prefix = f"{safe(app_title)}_{safe(APP_ID)}_{stamp}"

if EXPORT_MEASURES:
    export_measures()
if EXPORT_DIMENSIONS:
    export_dimensions()
if EXPORT_VARIABLES:
    export_variables()
if EXPORT_SCRIPT:
    export_script()
if EXPORT_VISUALS:
    export_visuals()

eng.ws.close()
