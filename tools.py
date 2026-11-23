import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from db import execute, rows_to_dicts, affected_rows
from llm import get_llm
from rag import best_match, search_with_scores
from metrics import (
    record_ticket_created,
    record_ticket_auto_resolved,
    record_ticket_escalated,
)

def _get_util_llm(temperature: float = 0.2):
    return get_llm(temperature)


URGENT_KEYWORDS = ("urgent", "asap", "critical", "down", "outage", "blocked", "immediately")
LOW_KEYWORDS = ("whenever", "no rush", "not urgent", "low priority")

AUTO_RESOLVE_THRESHOLD = float(os.getenv("AUTO_RESOLVE_THRESHOLD", "0.72"))
SLA_HOURS = float(os.getenv("SLA_HOURS", "24"))

DEPARTMENT_KEYWORDS = {
    "IT": ("vpn", "laptop", "password", "email", "wifi", "software", "access", "login", "hardware", "printer", "network"),
    "HR": ("leave", "payroll", "salary", "onboarding", "benefits", "policy", "reimbursement", "hr "),
    "Finance": ("invoice", "expense", "vendor", "payment", "budget", "reimbursement", "finance"),
    "Sales": ("crm", "lead", "deal", "quote", "pipeline"),
    "Marketing": ("campaign", "brand", "social", "content", "creative"),
    "Operations": ("facility", "office", "travel", "seating", "supplies"),
}


def classify_department(text: str) -> str:
    """Best-effort department routing from free text, defaults to IT (largest ticket volume today)."""
    text = text.lower()
    for dept, keywords in DEPARTMENT_KEYWORDS.items():
        if any(k in text for k in keywords):
            return dept
    return "IT"


def _like_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_employee(identifier: str, columns: str = "*") -> tuple[Optional[dict], Optional[str]]:
    """Find exactly one employee by ID or name. Returns (employee, None) or (None, error).

    Exact ID / exact name wins. Otherwise a partial name match (3+ characters) is used only when it is
    unique, so "Priya" with two Priyas returns an "ambiguous" error instead of silently picking one."""
    ident = (identifier or "").strip()
    if not ident:
        return None, "An employee ID or name is required"
    exact = rows_to_dicts(execute(
        f"SELECT {columns} FROM employees WHERE UPPER(id) = UPPER(?) OR LOWER(name) = LOWER(?) LIMIT 2",
        [ident, ident],
    ))
    if len(exact) == 1:
        return exact[0], None
    if not exact and len(ident) < 3:
        return None, f"'{ident}' is too short to match a name. Use the employee ID or more of the name."
    partial = exact or rows_to_dicts(execute(
        f"SELECT {columns} FROM employees WHERE name LIKE ? ESCAPE '\\' ORDER BY id LIMIT 6",
        [f"%{_like_escape(ident)}%"],
    ))
    if not partial:
        return None, f"No employee found matching '{ident}'"
    if len(partial) == 1:
        return partial[0], None
    names = ", ".join(f"{e.get('name')} ({e.get('id')})" for e in partial[:5] if "name" in e) or "several employees"
    return None, f"'{ident}' matches more than one employee: {names}. Ask the user which one they mean."


def report_period_key(period: str):
    """Sort key for periods like 'Q2 2025' (chronological, unlike plain string sort)."""
    m = re.search(r"Q([1-4])\s*(\d{4})", period or "", re.IGNORECASE)
    if m:
        return (int(m.group(2)), int(m.group(1)), "")
    return (0, 0, period or "")


def log_ticket_event(ticket_id: str, status: str, note: str = None):
    execute(
        "INSERT INTO ticket_events (id, ticket_id, status, note, created_at) VALUES (?, ?, ?, ?, ?)",
        [uuid.uuid4().hex, ticket_id, status, note, datetime.now(timezone.utc).isoformat()],
    )


@tool
def create_ticket(title: str, description: str, priority: str = "medium", force_create: bool = False) -> dict:
    """Create a support or IT ticket. Priority must be: low, medium, or high.
    Before creating, this checks the internal knowledge base for a matching self-serve answer —
    if one is found, it returns that instead of opening a ticket. Set force_create=True to skip
    the KB check and create the ticket anyway (e.g. the suggested KB answer didn't resolve it)."""
    if not force_create:
        match = best_match(f"{title} {description}")
        if match and match["score"] >= AUTO_RESOLVE_THRESHOLD:
            record_ticket_auto_resolved()
            return {
                "resolved_by_kb": True,
                "source": match["source"],
                "suggested_answer": match["content"],
                "note": "Found a matching self-serve answer instead of opening a ticket. "
                        "If this doesn't resolve it, call create_ticket again with force_create=True.",
            }

    if priority not in ("low", "medium", "high"):
        priority = "medium"
    if priority == "medium":
        text = f"{title} {description}".lower()
        if any(k in text for k in URGENT_KEYWORDS):
            priority = "high"
        elif any(k in text for k in LOW_KEYWORDS):
            priority = "low"
    department = classify_department(f"{title} {description}")
    ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        execute(
            "INSERT INTO tickets (id, title, description, priority, status, created_at, department, resolved_by_kb) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            [ticket_id, title, description, priority, "open", created_at, department],
        )
        log_ticket_event(ticket_id, "open", "ticket created")
        record_ticket_created()
        return {
            "ticket_id": ticket_id,
            "title": title,
            "priority": priority,
            "department": department,
            "status": "open",
            "created_at": created_at,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def get_employee(identifier: str) -> dict:
    """Get employee info by employee ID (e.g. EMP001) or by name."""
    try:
        emp, err = resolve_employee(identifier)
        return {"error": err} if err else emp
    except Exception as e:
        return {"error": str(e)}


@tool
def generate_report(report_type: str, period: Optional[str] = None) -> dict:
    """Fetch a business report summary. Valid types: sales, hr, it.
    period is optional, e.g. 'Q1 2025'. Without it, the most recent period is returned.
    The response lists all available_periods so you can fetch others for comparisons."""
    try:
        rows = rows_to_dicts(execute("SELECT * FROM reports WHERE type = ?", [report_type.lower()]))
        if not rows:
            return {"error": f"No report found for type '{report_type}'. Valid: sales, hr, it"}
        rows.sort(key=lambda r: report_period_key(r.get("period")))
        available = [r["period"] for r in rows]
        if period:
            wanted = report_period_key(period)
            match = [r for r in rows if report_period_key(r.get("period")) == wanted]
            if not match:
                return {"error": f"No {report_type} report for period '{period}'", "available_periods": available}
            report = match[0]
        else:
            report = rows[-1]
        return {**report, "available_periods": available}
    except Exception as e:
        return {"error": str(e)}


@tool
def get_stats() -> dict:
    """Get company-wide stats: total employees, open ticket count, total ticket count, average leave balance."""
    try:
        employees = rows_to_dicts(execute("SELECT leave_balance FROM employees"))
        open_tickets = rows_to_dicts(execute("SELECT id FROM tickets WHERE status = 'open'"))
        all_tickets = rows_to_dicts(execute("SELECT id FROM tickets"))
        balances = [int(e["leave_balance"]) for e in employees if e["leave_balance"] is not None]
        avg_leave = round(sum(balances) / len(balances), 1) if balances else 0
        return {
            "total_employees": len(employees),
            "open_tickets": len(open_tickets),
            "total_tickets": len(all_tickets),
            "avg_leave_balance": avg_leave,
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def list_open_tickets() -> list:
    """List currently open support/IT tickets, most recent first."""
    try:
        result = execute("SELECT * FROM tickets WHERE status = 'open' ORDER BY created_at DESC LIMIT 20")
        rows = rows_to_dicts(result)
        return rows if rows else []
    except Exception as e:
        return [{"error": str(e)}]


@tool
def get_org_chart(identifier: str) -> dict:
    """Get an employee's direct reports by employee ID (e.g. EMP001) or name."""
    try:
        emp, err = resolve_employee(identifier, "id, name")
        if err:
            return {"error": err}
        manager_name = emp["name"]
        reports = rows_to_dicts(execute(
            "SELECT id, name, role, department FROM employees WHERE manager = ?",
            [manager_name],
        ))
        return {"manager": manager_name, "direct_reports": reports}
    except Exception as e:
        return {"error": str(e)}


AUTO_APPROVE_LEAVE_DAYS = int(os.getenv("AUTO_APPROVE_LEAVE_DAYS", "2"))


@tool
def apply_leave(identifier: str, days: int) -> dict:
    """Request leave for an employee. identifier is employee ID or name. Fails if insufficient balance.
    Requests of AUTO_APPROVE_LEAVE_DAYS (default 2) or fewer are auto-approved and deducted immediately;
    longer requests are created as pending and need a manager decision via /leave-requests/{id}/approve."""
    try:
        if days <= 0:
            return {"error": "days must be a positive number"}
        emp, err = resolve_employee(identifier, "id, name, leave_balance")
        if err:
            return {"error": err}
        current_balance = int(emp["leave_balance"] or 0)
        if days > current_balance:
            return {"error": f"Insufficient leave balance: {emp['name']} has {current_balance} days, requested {days}"}

        request_id = f"LR-{uuid.uuid4().hex[:6].upper()}"
        requested_at = datetime.now(timezone.utc).isoformat()

        if days <= AUTO_APPROVE_LEAVE_DAYS:
            # Conditional decrement: a concurrent request can't push the balance below zero.
            if not _deduct_leave(emp["id"], days):
                return {"error": f"Insufficient leave balance for {emp['name']} (balance changed, please retry)"}
            execute(
                "INSERT INTO leave_requests (id, employee_id, employee_name, days, status, requested_at, decided_at, decided_by) "
                "VALUES (?, ?, ?, ?, 'approved', ?, ?, 'auto')",
                [request_id, emp["id"], emp["name"], days, requested_at, requested_at],
            )
            new_balance = _leave_balance(emp["id"])
            return {
                "request_id": request_id,
                "employee": emp["name"],
                "status": "approved",
                "auto_approved": True,
                "days_deducted": days,
                "previous_balance": current_balance,
                "new_balance": new_balance,
            }

        execute(
            "INSERT INTO leave_requests (id, employee_id, employee_name, days, status, requested_at, decided_at, decided_by) "
            "VALUES (?, ?, ?, ?, 'pending', ?, NULL, NULL)",
            [request_id, emp["id"], emp["name"], days, requested_at],
        )
        return {
            "request_id": request_id,
            "employee": emp["name"],
            "status": "pending",
            "auto_approved": False,
            "days_requested": days,
            "current_balance": current_balance,
            "note": f"Requests over {AUTO_APPROVE_LEAVE_DAYS} days need manager approval.",
        }
    except Exception as e:
        return {"error": str(e)}


def _deduct_leave(employee_id: str, days: int) -> bool:
    result = execute(
        "UPDATE employees SET leave_balance = leave_balance - ? WHERE id = ? AND leave_balance >= ?",
        [days, employee_id, days],
    )
    return affected_rows(result) == 1


def _leave_balance(employee_id: str):
    rows = rows_to_dicts(execute("SELECT leave_balance FROM employees WHERE id = ?", [employee_id]))
    return rows[0]["leave_balance"] if rows else None


def decide_leave_request(request_id: str, approve: bool, decided_by: str = "manager") -> dict:
    rows = rows_to_dicts(execute("SELECT * FROM leave_requests WHERE id = ?", [request_id]))
    if not rows:
        return {"error": f"No leave request found with id '{request_id}'"}
    req = rows[0]
    if req["status"] != "pending":
        return {"error": f"Leave request '{request_id}' is already {req['status']}"}

    decided_at = datetime.now(timezone.utc).isoformat()
    new_status = "approved" if approve else "rejected"

    # Claim the request first (only one caller can move it out of 'pending'), so two
    # simultaneous approvals can't both deduct the balance.
    claimed = execute(
        "UPDATE leave_requests SET status = ?, decided_at = ?, decided_by = ? WHERE id = ? AND status = 'pending'",
        [new_status, decided_at, decided_by, request_id],
    )
    if affected_rows(claimed) != 1:
        return {"error": f"Leave request '{request_id}' was already decided"}

    if approve:
        days = int(req["days"])
        if not _deduct_leave(req["employee_id"], days):
            # Roll the claim back so a manager can retry once the balance is sorted out.
            execute(
                "UPDATE leave_requests SET status = 'pending', decided_at = NULL, decided_by = NULL WHERE id = ?",
                [request_id],
            )
            balance = _leave_balance(req["employee_id"])
            if balance is None:
                return {"error": f"Employee '{req['employee_id']}' no longer exists"}
            return {"error": f"Insufficient leave balance: has {balance} days, request is for {days}"}

    return {**req, "status": new_status, "decided_at": decided_at, "decided_by": decided_by}


@tool
def list_departments() -> list:
    """List all unique departments with their employee counts."""
    try:
        result = execute(
            "SELECT department, COUNT(*) as employee_count FROM employees GROUP BY department ORDER BY department"
        )
        return rows_to_dicts(result)
    except Exception as e:
        return [{"error": str(e)}]


@tool
def summarize_meeting(transcript: str) -> dict:
    """Summarize a meeting transcript into a short summary, decisions made, and action items with owners."""
    if not transcript or not transcript.strip():
        return {"error": "transcript is empty"}
    prompt = (
        "Summarize this meeting transcript. Respond in exactly this format:\n\n"
        "SUMMARY: <2-3 sentence summary>\n"
        "DECISIONS: <one per line, prefixed with '- ', or 'None' if no clear decisions>\n"
        "ACTION ITEMS: <one per line as '- [owner] task', or 'None' if none>\n\n"
        f"Transcript:\n{transcript}"
    )
    try:
        response = _get_util_llm().invoke([
            SystemMessage(content="You are a precise meeting-notes assistant. Do not invent owners or decisions that weren't stated."),
            HumanMessage(content=prompt),
        ])
        text = response.content

        def _section(name: str, next_names: list[str]) -> str:
            start = text.find(f"{name}:")
            if start == -1:
                return ""
            start += len(name) + 1
            end = len(text)
            for nxt in next_names:
                pos = text.find(f"{nxt}:", start)
                if pos != -1:
                    end = min(end, pos)
            return text[start:end].strip()

        summary = _section("SUMMARY", ["DECISIONS", "ACTION ITEMS"])
        decisions_raw = _section("DECISIONS", ["ACTION ITEMS"])
        actions_raw = _section("ACTION ITEMS", [])

        def _lines(raw: str) -> list:
            items = [l.strip("- ").strip() for l in raw.splitlines() if l.strip() and l.strip().lower() != "none"]
            return items

        return {
            "summary": summary,
            "decisions": _lines(decisions_raw),
            "action_items": _lines(actions_raw),
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def get_digest(identifier: str) -> dict:
    """Get a personal daily digest for an employee: leave balance, their pending leave requests,
    and company-wide open ticket counts by priority. identifier is employee ID or name."""
    try:
        emp, err = resolve_employee(identifier, "id, name, leave_balance")
        if err:
            return {"error": err}

        pending_requests = rows_to_dicts(execute(
            "SELECT id, days, requested_at FROM leave_requests WHERE employee_id = ? AND status = 'pending'",
            [emp["id"]],
        ))
        open_by_priority = rows_to_dicts(execute(
            "SELECT priority, COUNT(*) as count FROM tickets WHERE status = 'open' GROUP BY priority"
        ))

        return {
            "employee": emp["name"],
            "leave_balance": emp["leave_balance"],
            "pending_leave_requests": pending_requests,
            "company_open_tickets_by_priority": open_by_priority,
        }
    except Exception as e:
        return {"error": str(e)}


def _hours_since(iso_ts: str) -> float:
    ts = datetime.fromisoformat(iso_ts)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds() / 3600


def check_sla_breaches() -> list:
    """Find open, non-high-priority tickets older than SLA_HOURS, escalate them to high priority."""
    escalated = []
    try:
        open_tickets = rows_to_dicts(execute(
            "SELECT id, title, priority, created_at FROM tickets WHERE status = 'open' AND priority != 'high'"
        ))
        for t in open_tickets:
            if not t.get("created_at"):
                continue
            if _hours_since(t["created_at"]) >= SLA_HOURS:
                # Conditional update: if a second worker or an external cron sweeps at the same
                # time, only one of them escalates (and logs/alerts for) each ticket.
                result = execute(
                    "UPDATE tickets SET priority = 'high' WHERE id = ? AND status = 'open' AND priority != 'high'",
                    [t["id"]],
                )
                if affected_rows(result) != 1:
                    continue
                log_ticket_event(t["id"], "open", f"auto-escalated to high — open > {SLA_HOURS}h (SLA breach)")
                record_ticket_escalated()
                escalated.append({"ticket_id": t["id"], "title": t["title"], "previous_priority": t["priority"]})
    except Exception as e:
        return [{"error": str(e)}]
    return escalated
