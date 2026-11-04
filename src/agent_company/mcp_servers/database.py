"""Agent Company's read-only database MCP server: PostgreSQL (through the pure-Python pg8000 driver) or an SQLite file.

Tools: list_tables, describe_table, query. DATABASE_URL (a secret, set under Agents → Tools & MCP) is "postgresql://user:pass@host:5432/db"
or "sqlite:////absolute/path.db". Read-only three ways: a single SELECT / WITH / EXPLAIN / VALUES / SHOW statement only; the session
itself is read-only (SQLite opened mode=ro with query_only, PostgreSQL in a READ ONLY transaction that is rolled back); and the
account should only be able to read. Rows are capped (ROWS) and statements time out (SECONDS). `--protect DIR` refuses SQLite
files inside DIR (mcp_hub passes Agent Company's data directory: its own databases are never readable here).
"""
import json, os, re, sqlite3, sys, time, urllib.parse
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stdio import ToolError, serve, tool  # noqa: E402

ROWS, MAX_ROWS, SECONDS, CELL = 50, 200, 15, 500
PROTECT = [Path(sys.argv[sys.argv.index("--protect") + 1]).resolve()] if "--protect" in sys.argv else []
READ = re.compile(r"^\s*(select|with|explain|values|show|table)\b", re.I)
WRITE = re.compile(r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|attach|detach|vacuum|reindex|call|do|lock|set|reset|pragma|load_extension)\b", re.I)

def url():
    u = os.getenv("DATABASE_URL", "").strip()
    if not u: raise ToolError("DATABASE_URL isn't set. An administrator sets it under Agents → Tools & MCP.")
    return u

def kind(u): return "sqlite" if u.startswith("sqlite:") else "pg" if u.split(":", 1)[0] in ("postgres", "postgresql") else None

# ---------- connections
def _sqlite(u):
    # sqlite:////abs/file.db and sqlite:///abs/file.db both mean /abs/file.db: always an absolute path
    path = Path("/" + urllib.parse.unquote(u[len("sqlite:"):].split("?", 1)[0]).lstrip("/")).resolve()
    if any(p == path or p in path.parents for p in PROTECT): raise ToolError("That file is inside Agent Company's own data directory, which this server never reads.")
    if not path.is_file(): raise ToolError(f"There's no database file at {path}.")
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    c.execute("PRAGMA query_only = ON")
    end = time.time() + SECONDS
    c.set_progress_handler(lambda: 1 if time.time() > end else 0, 10000)  # 1 interrupts the statement
    return c

def _pg(u):
    try: import pg8000.dbapi as pg
    except ImportError: raise ToolError("PostgreSQL needs the pg8000 driver, which isn't installed in Agent Company's image.")
    p = urllib.parse.urlsplit(u)
    q = dict(urllib.parse.parse_qsl(p.query))
    ssl = None
    if q.get("sslmode", "prefer") in ("require", "verify-ca", "verify-full"):
        import ssl as _ssl
        ssl = _ssl.create_default_context()
        if q["sslmode"] == "require": ssl.check_hostname, ssl.verify_mode = False, _ssl.CERT_NONE
    c = pg.connect(user=urllib.parse.unquote(p.username or ""), password=urllib.parse.unquote(p.password or "") or None, host=p.hostname or "localhost",
                   port=p.port or 5432, database=(p.path or "/").lstrip("/") or None, ssl_context=ssl, timeout=SECONDS + 5, application_name="axor-mcp")
    cur = c.cursor()
    cur.execute("START TRANSACTION READ ONLY")
    cur.execute(f"SET LOCAL statement_timeout = {SECONDS * 1000}")
    return c

def run(sql, params=(), limit=ROWS):
    """(column names, rows, more?) of one read-only statement."""
    u = url(); k = kind(u)
    if not k: raise ToolError("DATABASE_URL must start with postgresql:// or sqlite:///.")
    c = _sqlite(u) if k == "sqlite" else _pg(u)
    try:
        cur = c.cursor()
        try: cur.execute(sql, params)
        except sqlite3.OperationalError as e:
            raise ToolError(f"Stopped after {SECONDS} s." if "interrupt" in str(e).lower() else f"SQLite: {e}")
        except Exception as e:  # noqa: BLE001 - the driver's own error, for the agent to fix its query
            if type(e).__module__.startswith("pg8000"):
                m = e.args[0] if e.args and isinstance(e.args[0], dict) else {}
                raise ToolError("PostgreSQL: " + (m.get("M") or str(e))[:400] + (" (statement timeout)" if m.get("C") == "57014" else ""))
            raise
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchmany(limit + 1) if cols else []
        return cols, rows[:limit], len(rows) > limit
    finally:
        try: c.rollback()
        except Exception: pass  # noqa: BLE001
        c.close()

def cell(v):
    if v is None: return None
    if isinstance(v, (int, float, bool, str)): s = v
    elif isinstance(v, (datetime, date)): s = v.isoformat()
    elif isinstance(v, Decimal): s = str(v)
    elif isinstance(v, (bytes, bytearray, memoryview)): s = f"<{len(bytes(v))} bytes>"
    else: s = json.dumps(v, default=str) if isinstance(v, (dict, list)) else str(v)
    return s[:CELL] + "…" if isinstance(s, str) and len(s) > CELL else s

def table(cols, rows, more, limit):
    if not cols: return "The statement returned no columns."
    out = {"columns": cols, "rows": [[cell(v) for v in r] for r in rows], "row_count": len(rows)}
    if more: out["note"] = f"More rows than the {limit} shown: narrow the query or add LIMIT/OFFSET."
    return json.dumps(out, ensure_ascii=False)

# ---------- tools
def list_tables(a):
    u = url()
    if kind(u) == "sqlite":
        cols, rows, _ = run("SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name", limit=MAX_ROWS)
        return table(["name", "type"], rows, False, MAX_ROWS)
    cols, rows, more = run("""SELECT table_schema AS schema, table_name AS name, table_type AS type FROM information_schema.tables
                              WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2""", limit=MAX_ROWS)
    return table(cols, rows, more, MAX_ROWS)

def describe_table(a):
    name = (a.get("table") or "").strip()
    if not re.fullmatch(r"[A-Za-z_][\w$]*(\.[A-Za-z_][\w$]*)?", name): raise ToolError("Give a table name, e.g. users or public.users.")
    u = url()
    if kind(u) == "sqlite":
        cols, rows, _ = run(f"SELECT name, type, \"notnull\", pk FROM pragma_table_info(?)", (name,), limit=MAX_ROWS)
        if not rows: raise ToolError(f"There's no table {name}.")
        n = run(f'SELECT COUNT(*) FROM "{name}"', limit=1)[1][0][0]
        return table(["column", "type", "not_null", "primary_key"], rows, False, MAX_ROWS) + f"\nrows: {n}"
    schema, _, tbl = name.rpartition(".")
    cols, rows, _ = run("""SELECT column_name AS column, data_type AS type, is_nullable AS nullable, column_default AS default
                           FROM information_schema.columns WHERE table_name = %s AND table_schema = COALESCE(NULLIF(%s, ''), current_schema())
                           ORDER BY ordinal_position""", (tbl, schema), limit=MAX_ROWS)
    if not rows: raise ToolError(f"There's no table {name}.")
    est = run("SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)", (name,), limit=1)[1]
    return table(cols, rows, False, MAX_ROWS) + (f"\nabout {est[0][0]} rows" if est and est[0][0] is not None and est[0][0] >= 0 else "")

def query(a):
    sql = (a.get("sql") or "").strip().rstrip(";").strip()
    if not sql: raise ToolError("Give a SELECT statement.")
    if ";" in sql: raise ToolError("One statement at a time.")
    if not READ.match(sql) or WRITE.search(re.sub(r"'(?:[^']|'')*'", "''", sql)):  # no exemption for EXPLAIN: EXPLAIN ANALYZE runs its statement
        raise ToolError("Only reading is allowed: a single SELECT (or WITH … SELECT, EXPLAIN, VALUES, SHOW).")
    limit = max(1, min(int(a.get("limit") or ROWS), MAX_ROWS))
    return table(*run(sql, limit=limit), limit)

TOOLS = [
    tool("list_tables", "The database's tables and views.", {}, (), "List tables"),
    tool("describe_table", "A table's columns (name, type, nullable, default) and roughly how many rows it has.",
         {"table": {"type": "string", "description": "Table name, optionally schema.table."}}, ["table"], "Describe a table"),
    tool("query", "Run one read-only SQL statement (SELECT, WITH … SELECT, EXPLAIN, VALUES, SHOW) and get its rows as JSON. "
         f"At most {MAX_ROWS} rows (default {ROWS}); statements stop after {SECONDS} s. Writes are refused.",
         {"sql": {"type": "string", "description": "One SELECT statement."}, "limit": {"type": "integer", "description": f"Rows to return, 1 to {MAX_ROWS}."}}, ["sql"], "Query"),
]

if __name__ == "__main__":
    serve("axor-database", "1.0", TOOLS, {"list_tables": list_tables, "describe_table": describe_table, "query": query})
