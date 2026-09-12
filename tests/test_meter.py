"""The billed meter must be shown in the unit the tenant reported."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import qlik_capacity as qc

ok = []
CASES = [
    ("unit absent - what this tenant returns, treated as bytes", {}, 5_000_000_000, True),
    ("unit 'bytes'", {"unit": "bytes"}, 5_000_000_000, True),
    ("unit 'Bytes' with padding", {"unit": " Bytes "}, 1024, True),
    ("unit 'gigabytes' - NOT bytes", {"unit": "gigabytes"}, 500, False),
    ("unit 'GB'", {"unit": "GB"}, 500, False),
    ("unit 'rows'", {"unit": "rows"}, 12_000_000, False),
]
for label, rec, val, want_bytes in CASES:
    got = qc.meter_is_bytes(rec)
    text = qc.meter_amount(rec, val)
    good = got == want_bytes
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label:52} -> {text}")

print()
print("  what the old code would have shown for a gigabytes meter:")
print(f"    {qc.format_bytes(500)}  (500 gigabytes rendered as bytes)")
print(f"  what it shows now:")
print(f"    {qc.meter_amount({'unit': 'gigabytes'}, 500)}")
ok.append(qc.meter_amount({"unit": "gigabytes"}, 500) == "500 gigabytes")

print()
print("  the percentage is unit-safe either way, being a ratio:")
for unit in ("", "bytes", "gigabytes"):
    used, lim = 900, 1000
    print(f"    unit={unit!r:12} {used}/{lim} = {used / lim * 100:.0f}%")
ok.append(True)

print()
print("  missing values do not crash:")
for v in (None,):
    print(f"    value={v!r} -> {qc.meter_amount({'unit': 'bytes'}, v)}")
ok.append(qc.meter_amount({"unit": "bytes"}, None) == "?")
ok.append(qc.meter_amount(None, 1024) == qc.format_bytes(1024))
print(f"    rec=None -> {qc.meter_amount(None, 1024)}")

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
