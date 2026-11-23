"""Test fixtures. No network: Turso's HTTP API is emulated on top of in-memory SQLite, so db.py's real
request/response encoding is exercised; Groq and Qdrant are replaced per test where needed."""
import os
import sys
import sqlite3
import threading

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Set before any app module is imported (several read env at import time).
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ["NEXUS_ENABLE_SCHEDULER"] = "false"
os.environ["TURSO_URL"] = "libsql://fake.turso.test"
os.environ["TURSO_TOKEN"] = "fake-token"

import db  # noqa: E402


class FakeTurso:
    """Minimal Hrana v2 pipeline server backed by sqlite3."""

    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.lock = threading.Lock()

    @staticmethod
    def _decode_arg(a):
        t = a["type"]
        if t == "null":
            return None
        if t == "integer":
            assert isinstance(a["value"], str), "Hrana integers must be sent as strings"
            return int(a["value"])
        if t == "float":
            assert isinstance(a["value"], (int, float)), "Hrana floats must be sent as JSON numbers"
            return float(a["value"])
        return a["value"]

    @staticmethod
    def _encode_val(v):
        if v is None:
            return {"type": "null"}
        if isinstance(v, int):
            return {"type": "integer", "value": str(v)}
        if isinstance(v, float):
            return {"type": "float", "value": v}
        return {"type": "text", "value": str(v)}

    def handle(self, payload):
        stmt = payload["requests"][0]["stmt"]
        args = [self._decode_arg(a) for a in stmt.get("args", [])]
        with self.lock:
            try:
                cur = self.conn.execute(stmt["sql"], args)
                rows = cur.fetchall()
                self.conn.commit()
            except Exception as e:
                return {"results": [{"type": "error", "error": {"message": str(e)}}, {"type": "ok"}]}
        cols = [{"name": d[0]} for d in (cur.description or [])]
        return {"results": [{"type": "ok", "response": {"type": "execute", "result": {
            "cols": cols,
            "rows": [[self._encode_val(v) for v in r] for r in rows],
            "affected_row_count": cur.rowcount if cur.rowcount > 0 else 0,
        }}}, {"type": "ok"}]}


class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


EMPLOYEES = [
    ("EMP001", "Priya Sharma", "Engineering", "Senior SWE", "Rajesh Kumar", 18, "priya@nexus.io"),
    ("EMP002", "Arjun Mehta", "Sales", "Account Executive", "Sneha Patel", 12, "arjun@nexus.io"),
    ("EMP003", "Sneha Patel", "Sales", "Sales Manager", "Vikram Singh", 8, "sneha@nexus.io"),
    ("EMP005", "Anita Roy", "HR", "HR Head", "CEO", 6, "anita@nexus.io"),
    ("EMP008", "Rajesh Kumar", "Engineering", "VP Engineering", "CEO", 4, "rajesh@nexus.io"),
    ("EMP009", "Meera Iyer", "Engineering", "SWE II", "Priya Sharma", 22, "meera@nexus.io"),
]


@pytest.fixture
def fake_db(monkeypatch):
    server = FakeTurso()

    def fake_post(url, headers=None, json=None, timeout=None):
        assert url == "https://fake.turso.test/v2/pipeline"
        assert headers["Authorization"] == "Bearer fake-token"
        return _Resp(server.handle(json))

    monkeypatch.setattr(db.requests, "post", fake_post)
    monkeypatch.setattr(db, "_URL", None)
    db.migrate()
    for emp in EMPLOYEES:
        db.execute(
            "INSERT INTO employees (id, name, department, role, manager, leave_balance, email) VALUES (?, ?, ?, ?, ?, ?, ?)",
            list(emp),
        )
    return server


def balance(emp_id: str) -> int:
    return db.rows_to_dicts(db.execute("SELECT leave_balance FROM employees WHERE id = ?", [emp_id]))[0]["leave_balance"]
