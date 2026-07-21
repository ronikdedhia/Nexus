import os
import uuid
from datetime import datetime, timezone
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from db import execute, rows_to_dicts
from rag import best_match, search_with_scores
from metrics import (
    record_ticket_created,
    record_ticket_auto_resolved,
    record_ticket_escalated,
    record_marketing_content_generated,
)

_util_llm = None


def _get_util_llm(temperature: float = 0.2) -> ChatGroq:
    global _util_llm
    if _util_llm is None:
        _util_llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=temperature, api_key=os.getenv("GROQ_API_KEY"))
    return _util_llm


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
        result = execute(
            "SELECT * FROM employees WHERE id = ? OR name LIKE ? LIMIT 1",
            [identifier, f"%{identifier}%"],
        )
        rows = rows_to_dicts(result)
        if not rows:
            return {"error": f"No employee found matching '{identifier}'"}
        return rows[0]
    except Exception as e:
        return {"error": str(e)}


@tool
def generate_report(report_type: str) -> dict:
    """Fetch a business report summary. Valid types: sales, hr, it."""
    try:
        result = execute(
            "SELECT * FROM reports WHERE type = ? LIMIT 1",
            [report_type.lower()],
        )
        rows = rows_to_dicts(result)
        if not rows:
            return {"error": f"No report found for type '{report_type}'. Valid: sales, hr, it"}
        return rows[0]
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
        emp_result = execute(
            "SELECT id, name FROM employees WHERE id = ? OR name LIKE ? LIMIT 1",
            [identifier, f"%{identifier}%"],
        )
        emp_rows = rows_to_dicts(emp_result)
        if not emp_rows:
            return {"error": f"No employee found matching '{identifier}'"}
        manager_name = emp_rows[0]["name"]
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
        emp_result = execute(
            "SELECT id, name, leave_balance FROM employees WHERE id = ? OR name LIKE ? LIMIT 1",
            [identifier, f"%{identifier}%"],
        )
        emp_rows = rows_to_dicts(emp_result)
        if not emp_rows:
            return {"error": f"No employee found matching '{identifier}'"}
        emp = emp_rows[0]
        current_balance = int(emp["leave_balance"])
        if days <= 0:
            return {"error": "days must be a positive number"}
        if days > current_balance:
            return {"error": f"Insufficient leave balance: {emp['name']} has {current_balance} days, requested {days}"}

        request_id = f"LR-{uuid.uuid4().hex[:6].upper()}"
        requested_at = datetime.now(timezone.utc).isoformat()

        if days <= AUTO_APPROVE_LEAVE_DAYS:
            new_balance = current_balance - days
            execute("UPDATE employees SET leave_balance = ? WHERE id = ?", [new_balance, emp["id"]])
            execute(
                "INSERT INTO leave_requests (id, employee_id, employee_name, days, status, requested_at, decided_at, decided_by) "
                "VALUES (?, ?, ?, ?, 'approved', ?, ?, 'auto')",
                [request_id, emp["id"], emp["name"], days, requested_at, requested_at],
            )
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


def decide_leave_request(request_id: str, approve: bool, decided_by: str = "manager") -> dict:
    rows = rows_to_dicts(execute("SELECT * FROM leave_requests WHERE id = ?", [request_id]))
    if not rows:
        return {"error": f"No leave request found with id '{request_id}'"}
    req = rows[0]
    if req["status"] != "pending":
        return {"error": f"Leave request '{request_id}' is already {req['status']}"}

    decided_at = datetime.now(timezone.utc).isoformat()
    new_status = "approved" if approve else "rejected"

    if approve:
        emp_rows = rows_to_dicts(execute("SELECT leave_balance FROM employees WHERE id = ?", [req["employee_id"]]))
        if not emp_rows:
            return {"error": f"Employee '{req['employee_id']}' no longer exists"}
        current_balance = int(emp_rows[0]["leave_balance"])
        days = int(req["days"])
        if days > current_balance:
            return {"error": f"Insufficient leave balance: has {current_balance} days, request is for {days}"}
        execute("UPDATE employees SET leave_balance = ? WHERE id = ?", [current_balance - days, req["employee_id"]])

    execute(
        "UPDATE leave_requests SET status = ?, decided_at = ?, decided_by = ? WHERE id = ?",
        [new_status, decided_at, decided_by, request_id],
    )
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
def generate_marketing_content(topic: str, content_format: str = "social post") -> dict:
    """Draft marketing content (social post, ad copy, email, or campaign blurb) for a topic,
    grounded in the company's brand guidelines and campaign policy from the knowledge base."""
    try:
        grounding = search_with_scores(f"marketing brand guidelines {topic}", k=3)
        context = "\n".join(f"[{g['source']}] {g['content']}" for g in grounding) or "No brand guidelines found."
        prompt = (
            f"Draft a {content_format} about: {topic}\n\n"
            f"Follow these brand guidelines strictly (tone, colors, approval rules mentioned):\n{context}\n\n"
            "Keep it concise, on-brand, and ready to use. Do not invent statistics or claims."
        )
        response = _get_util_llm(temperature=0.5).invoke([
            SystemMessage(content="You are FlexiLoans' marketing copywriter. Stay on-brand and factual."),
            HumanMessage(content=prompt),
        ])
        record_marketing_content_generated()
        return {
            "content": response.content,
            "format": content_format,
            "grounded_on": sorted({g["source"] for g in grounding}),
        }
    except Exception as e:
        return {"error": str(e)}


@tool
def get_digest(identifier: str) -> dict:
    """Get a personal daily digest for an employee: leave balance, their pending leave requests,
    and company-wide open ticket counts by priority. identifier is employee ID or name."""
    try:
        emp_rows = rows_to_dicts(execute(
            "SELECT id, name, leave_balance FROM employees WHERE id = ? OR name LIKE ? LIMIT 1",
            [identifier, f"%{identifier}%"],
        ))
        if not emp_rows:
            return {"error": f"No employee found matching '{identifier}'"}
        emp = emp_rows[0]

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
                execute("UPDATE tickets SET priority = 'high' WHERE id = ?", [t["id"]])
                log_ticket_event(t["id"], "open", f"auto-escalated to high — open > {SLA_HOURS}h (SLA breach)")
                record_ticket_escalated()
                escalated.append({"ticket_id": t["id"], "title": t["title"], "previous_priority": t["priority"]})
    except Exception as e:
        return [{"error": str(e)}]
    return escalated
