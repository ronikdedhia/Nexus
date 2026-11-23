from datetime import datetime, timedelta, timezone

import db
import tools
from conftest import balance


def _ticket(ticket_id, priority="medium", hours_old=30, status="open"):
    created = (datetime.now(timezone.utc) - timedelta(hours=hours_old)).isoformat()
    db.execute(
        "INSERT INTO tickets (id, title, description, priority, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        [ticket_id, f"title {ticket_id}", "desc", priority, status, created],
    )


# ── db encoding ──────────────────────────────────────────────────────────────

def test_values_round_trip_with_python_types(fake_db):
    db.execute("CREATE TABLE t (i INTEGER, f REAL, s TEXT, b INTEGER, n TEXT)")
    db.execute("INSERT INTO t VALUES (?, ?, ?, ?, ?)", [7, 1.5, "x", True, None])
    row = db.rows_to_dicts(db.execute("SELECT * FROM t"))[0]
    assert row == {"i": 7, "f": 1.5, "s": "x", "b": 1, "n": None}


def test_employee_numbers_are_ints(fake_db):
    emp, _ = tools.resolve_employee("EMP001")
    assert emp["leave_balance"] == 18


# ── employee resolution ─────────────────────────────────────────────────────

def test_resolve_by_id_case_insensitive(fake_db):
    emp, err = tools.resolve_employee("emp002")
    assert err is None and emp["name"] == "Arjun Mehta"


def test_resolve_exact_name_and_unique_partial(fake_db):
    assert tools.resolve_employee("sneha patel")[0]["id"] == "EMP003"
    assert tools.resolve_employee("Meera")[0]["id"] == "EMP009"


def _add_second_priya():
    db.execute("INSERT INTO employees (id, name, department, leave_balance) VALUES ('EMP099', 'Priya Nair', 'HR', 10)")


def test_ambiguous_partial_name_is_an_error(fake_db):
    _add_second_priya()
    emp, err = tools.resolve_employee("Priya")
    assert emp is None and "more than one employee" in err and "EMP001" in err and "EMP099" in err


def test_short_fragment_is_rejected(fake_db):
    emp, err = tools.resolve_employee("an")  # only Anita matches, but 2 letters is too vague to act on
    assert emp is None and "too short" in err


def test_like_wildcards_are_escaped(fake_db):
    emp, err = tools.resolve_employee("%_%")
    assert emp is None and "No employee found" in err


# ── leave ───────────────────────────────────────────────────────────────────

def test_apply_leave_ambiguous_name_deducts_nothing(fake_db):
    _add_second_priya()
    result = tools.apply_leave.invoke({"identifier": "Priya", "days": 1})
    assert "more than one employee" in result["error"]
    assert (balance("EMP001"), balance("EMP099")) == (18, 10)
    assert db.rows_to_dicts(db.execute("SELECT COUNT(*) AS n FROM leave_requests"))[0]["n"] == 0


def test_apply_leave_auto_approves_short_requests(fake_db):
    result = tools.apply_leave.invoke({"identifier": "Priya Sharma", "days": 2})
    assert result["status"] == "approved" and result["new_balance"] == 16
    assert balance("EMP001") == 16


def test_apply_leave_long_request_is_pending_without_deduction(fake_db):
    result = tools.apply_leave.invoke({"identifier": "EMP001", "days": 5})
    assert result["status"] == "pending"
    assert balance("EMP001") == 18


def test_apply_leave_insufficient_balance(fake_db):
    result = tools.apply_leave.invoke({"identifier": "EMP008", "days": 5})
    assert "Insufficient" in result["error"]


def test_approving_twice_deducts_once(fake_db):
    req = tools.apply_leave.invoke({"identifier": "EMP001", "days": 5})
    first = tools.decide_leave_request(req["request_id"], approve=True)
    second = tools.decide_leave_request(req["request_id"], approve=True)
    assert first["status"] == "approved"
    assert "already" in second["error"]
    assert balance("EMP001") == 13


def test_approval_with_insufficient_balance_stays_pending(fake_db):
    req = tools.apply_leave.invoke({"identifier": "EMP001", "days": 5})
    db.execute("UPDATE employees SET leave_balance = 3 WHERE id = 'EMP001'")
    result = tools.decide_leave_request(req["request_id"], approve=True)
    assert "Insufficient" in result["error"]
    status = db.rows_to_dicts(db.execute("SELECT status FROM leave_requests WHERE id = ?", [req["request_id"]]))[0]
    assert status["status"] == "pending" and balance("EMP001") == 3


# ── reports ─────────────────────────────────────────────────────────────────

def _reports():
    for rid, period in [("R1", "Q4 2025"), ("R2", "Q1 2025"), ("R3", "Q1 2026"), ("R4", "Q2 2025")]:
        db.execute("INSERT INTO reports (id, type, period, summary) VALUES (?, 'sales', ?, ?)", [rid, period, f"sum {period}"])


def test_report_defaults_to_latest_period_across_years(fake_db):
    _reports()
    result = tools.generate_report.invoke({"report_type": "sales"})
    assert result["period"] == "Q1 2026"
    assert result["available_periods"] == ["Q1 2025", "Q2 2025", "Q4 2025", "Q1 2026"]


def test_report_by_period(fake_db):
    _reports()
    assert tools.generate_report.invoke({"report_type": "SALES", "period": "q2 2025"})["id"] == "R4"
    assert "error" in tools.generate_report.invoke({"report_type": "sales", "period": "Q3 2030"})


# ── SLA sweep ───────────────────────────────────────────────────────────────

def test_sla_sweep_escalates_each_ticket_once(fake_db):
    _ticket("T-OLD", hours_old=30)
    _ticket("T-NEW", hours_old=1)
    _ticket("T-HIGH", priority="high", hours_old=50)
    first = tools.check_sla_breaches()
    second = tools.check_sla_breaches()
    assert [e["ticket_id"] for e in first] == ["T-OLD"]
    assert second == []
    events = db.rows_to_dicts(db.execute("SELECT * FROM ticket_events WHERE ticket_id = 'T-OLD'"))
    assert len(events) == 1


# ── LLM config ──────────────────────────────────────────────────────────────

def test_default_model_is_gpt_oss_120b(monkeypatch):
    import llm
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    monkeypatch.setattr(llm, "_clients", {})
    assert llm.get_llm(0).model_name == "openai/gpt-oss-120b"


def test_model_override_and_per_temperature_clients(monkeypatch):
    import llm
    monkeypatch.setenv("GROQ_MODEL", "qwen/qwen3.6-27b")
    monkeypatch.setattr(llm, "_clients", {})
    cold, warm = llm.get_llm(0.2), llm.get_llm(0.5)
    assert cold.model_name == "qwen/qwen3.6-27b"
    assert (cold.temperature, warm.temperature) == (0.2, 0.5)


def test_no_retired_model_ids_left_in_code():
    import pathlib
    root = pathlib.Path(tools.__file__).parent
    offenders = [p.name for p in root.glob("*.py") if p.name != "llm.py" and "llama-3.3-70b" in p.read_text()]
    assert offenders == []
