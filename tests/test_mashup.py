"""M comments must not become sources, and URLs must not become comments."""
import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import mashup_parser as mp

ok = []

def one(label, expr, want_status, want_connectors, siblings=None):
    r = mp.resolve_source(expr, siblings or {})
    conns = [d.connector for d in r.direct_sources]
    good = r.status == want_status and conns == want_connectors
    ok.append(good)
    print(f"  {'ok  ' if good else 'FAIL'} {label}")
    if not good:
        print(f"       status={r.status!r} want {want_status!r}")
        print(f"       connectors={conns} want {want_connectors}")
    return r

print("a commented-out source is not a source:")
one("// line comment hiding an old Sql.Database",
    '''let
    // Old source, replaced Jan 2026:
    // Source = Sql.Database("old-server", "OldDB"),
    Source = Sql.Database("new-server", "NewDB"),
    Nav = Source{[Schema="dbo",Item="Orders"]}[Data]
in Nav''',
    "direct_source", ["Sql.Database"])

one("/* block */ comment hiding a second connector",
    '''let
    /* was: Source = PostgreSQL.Database("pg","old") */
    Source = Sql.Database("srv","DB"),
    Nav = Source{[Schema="dbo",Item="T"]}[Data]
in Nav''',
    "direct_source", ["Sql.Database"])

print()
print("a URL is not a comment:")
r = one("https:// inside Web.Contents survives",
        'let Source = Web.Contents("https://api.example.com/v1/orders") in Source',
        "direct_source", ["Web.Contents"])
assert "https://api.example.com/v1/orders" in r.direct_sources[0].connection_args, r
ok.append(True)
print(f"       args kept: {r.direct_sources[0].connection_args}")

one("a // inside a SQL query string survives",
    'let Source = Sql.Database("srv","DB",[Query="SELECT 1 -- note"' + "]) in Source",
    "direct_source", ["Sql.Database"])

print()
print("M string escaping:")
r = mp.strip_m_comments('let x = "a ""quoted"" // not a comment" in x')
print(f"   {r}")
ok.append("// not a comment" in r)
print(f"  {'ok  ' if ok[-1] else 'FAIL'} a doubled quote keeps the scan inside the string")

print()
print("two genuine sources are still two:")
one("two real Sql.Database calls",
    '''let
    A = Sql.Database("srv1","DB1"),
    B = Sql.Database("srv2","DB2"),
    C = Table.Combine({A,B})
in C''',
    "multiple_direct_sources", ["Sql.Database", "Sql.Database"])

print()
print("split_shared_queries:")
doc = '''section Section1;
// shared Ghost = let Source = Sql.Database("gone","X") in Source;
shared Orders = let Source = Sql.Database("srv","DB") in Source;
/* shared Old = let Source = Web.Contents("http://x") in Source; */
shared Customers = let Source = Sql.Database("srv","DB2") in Source;
'''
qs = mp.split_shared_queries(doc)
print("   queries found:", sorted(qs))
ok.append(sorted(qs) == ["Customers", "Orders"])
print(f"  {'ok  ' if ok[-1] else 'FAIL'} commented-out queries are not queries")

print()
print("malformed input must not crash:")
for label, v in (("unterminated string", 'let x = "abc'),
                 ("unterminated block comment", "let x = 1 /* oops"),
                 ("trailing line comment, no newline", "let x = 1 // end"),
                 ("empty", ""), ("None", None)):
    try:
        mp.strip_m_comments(v)
        print(f"  ok   {label}")
        ok.append(True)
    except Exception as e:
        print(f"  FAIL {label}: {e}")
        ok.append(False)

print()
print(f"{sum(ok)} of {len(ok)} pass")
sys.exit(0 if all(ok) else 1)
