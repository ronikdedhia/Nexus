import os
import base64
import requests
from typing import Any

_URL = None
_TOKEN = None


def _init():
    global _URL, _TOKEN
    url = os.getenv("TURSO_URL", "")
    # Turso CLI gives libsql:// but the HTTP pipeline API needs https://
    _URL = url.replace("libsql://", "https://", 1)
    _TOKEN = os.getenv("TURSO_TOKEN")
    if not _URL or not _TOKEN:
        raise RuntimeError("TURSO_URL and TURSO_TOKEN must be set")


def _wrap(v: Any) -> dict:
    """Encode a Python value as a Hrana v2 value."""
    if v is None:
        return {"type": "null"}
    if isinstance(v, bool):  # must come before int: bool is a subclass of int
        return {"type": "integer", "value": "1" if v else "0"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}  # Hrana floats are JSON numbers, not strings
    return {"type": "text", "value": str(v)}


def _unwrap(v: dict) -> Any:
    """Decode a Hrana v2 value into the matching Python type."""
    t = v.get("type")
    if t == "null":
        return None
    if t == "integer":
        return int(v["value"])
    if t == "float":
        return float(v["value"])
    if t == "blob":
        return base64.b64decode(v.get("base64", ""))
    return v.get("value")


def execute(sql: str, args: list[Any] = None) -> dict:
    if not _URL:
        _init()
    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": sql,
                    "args": [_wrap(a) for a in (args or [])],
                },
            },
            {"type": "close"},
        ]
    }
    resp = requests.post(
        f"{_URL}/v2/pipeline",
        headers={"Authorization": f"Bearer {_TOKEN}"},
        json=payload,
        timeout=10,
    )
    resp.raise_for_status()
    result = resp.json()["results"][0]
    if result["type"] == "error":
        raise RuntimeError(result["error"]["message"])
    return result["response"]["result"]


def affected_rows(result: dict) -> int:
    return int(result.get("affected_row_count", 0) or 0)


# Base tables (created by seed.py). MIGRATIONS below catches up databases seeded by older versions.
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS employees (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        department TEXT,
        role TEXT,
        manager TEXT,
        leave_balance INTEGER,
        email TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS tickets (
        id TEXT PRIMARY KEY,
        title TEXT,
        description TEXT,
        priority TEXT,
        status TEXT,
        created_at TEXT,
        department TEXT,
        resolved_by_kb INTEGER DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS reports (
        id TEXT PRIMARY KEY,
        type TEXT,
        period TEXT,
        summary TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ticket_events (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        status TEXT NOT NULL,
        note TEXT,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS ticket_comments (
        id TEXT PRIMARY KEY,
        ticket_id TEXT NOT NULL,
        author TEXT NOT NULL,
        comment TEXT NOT NULL,
        created_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS leave_requests (
        id TEXT PRIMARY KEY,
        employee_id TEXT NOT NULL,
        employee_name TEXT,
        days INTEGER NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT,
        decided_at TEXT,
        decided_by TEXT
    )""",
]


MIGRATIONS = [
    "ALTER TABLE tickets ADD COLUMN department TEXT",
    "ALTER TABLE tickets ADD COLUMN resolved_by_kb INTEGER DEFAULT 0",
    """CREATE TABLE IF NOT EXISTS leave_requests (
        id TEXT PRIMARY KEY,
        employee_id TEXT NOT NULL,
        employee_name TEXT,
        days INTEGER NOT NULL,
        status TEXT NOT NULL,
        requested_at TEXT,
        decided_at TEXT,
        decided_by TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS metrics_counters (
        name TEXT PRIMARY KEY,
        value INTEGER DEFAULT 0
    )""",
]


def migrate():
    """Idempotent: creates missing tables, then adds columns that older seeded databases lack."""
    for stmt in SCHEMA:
        execute(stmt)
    for stmt in MIGRATIONS:
        try:
            execute(stmt)
        except Exception as e:
            if "duplicate column" not in str(e).lower():
                raise


def ping() -> dict:
    try:
        execute("SELECT 1")
        return {"status": "up"}
    except Exception as e:
        return {"status": "down", "error": str(e)}


def rows_to_dicts(result: dict) -> list[dict]:
    cols = [c["name"] for c in result.get("cols", [])]
    return [dict(zip(cols, [_unwrap(v) for v in row])) for row in result.get("rows", [])]
