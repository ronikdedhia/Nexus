import os
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


def execute(sql: str, args: list[Any] = None) -> dict:
    if not _URL:
        _init()

    def _wrap(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": str(v)}
        return {"type": "text", "value": str(v)}

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
    """Idempotent schema catch-up for already-seeded databases."""
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
    out = []
    for row in result.get("rows", []):
        out.append(dict(zip(cols, [v.get("value") for v in row])))
    return out
