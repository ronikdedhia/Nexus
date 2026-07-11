import uuid
from datetime import datetime, timezone
from langchain_core.tools import tool
from db import execute, rows_to_dicts


URGENT_KEYWORDS = ("urgent", "asap", "critical", "down", "outage", "blocked", "immediately")
LOW_KEYWORDS = ("whenever", "no rush", "not urgent", "low priority")


def log_ticket_event(ticket_id: str, status: str, note: str = None):
    execute(
        "INSERT INTO ticket_events (id, ticket_id, status, note, created_at) VALUES (?, ?, ?, ?, ?)",
        [uuid.uuid4().hex, ticket_id, status, note, datetime.now(timezone.utc).isoformat()],
    )


@tool
def create_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Create a support or IT ticket. Priority must be: low, medium, or high."""
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    if priority == "medium":
        text = f"{title} {description}".lower()
        if any(k in text for k in URGENT_KEYWORDS):
            priority = "high"
        elif any(k in text for k in LOW_KEYWORDS):
            priority = "low"
    ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        execute(
            "INSERT INTO tickets (id, title, description, priority, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [ticket_id, title, description, priority, "open", created_at],
        )
        log_ticket_event(ticket_id, "open", "ticket created")
        return {
            "ticket_id": ticket_id,
            "title": title,
            "priority": priority,
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


@tool
def apply_leave(identifier: str, days: int) -> dict:
    """Deduct leave days from an employee's balance. identifier is employee ID or name. Fails if insufficient balance."""
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
        new_balance = current_balance - days
        execute(
            "UPDATE employees SET leave_balance = ? WHERE id = ?",
            [new_balance, emp["id"]],
        )
        return {
            "employee": emp["name"],
            "days_deducted": days,
            "previous_balance": current_balance,
            "new_balance": new_balance,
        }
    except Exception as e:
        return {"error": str(e)}


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
