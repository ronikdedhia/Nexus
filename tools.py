import uuid
from datetime import datetime, timezone
from langchain_core.tools import tool
from db import execute, rows_to_dicts


@tool
def create_ticket(title: str, description: str, priority: str = "medium") -> dict:
    """Create a support or IT ticket. Priority must be: low, medium, or high."""
    if priority not in ("low", "medium", "high"):
        priority = "medium"
    ticket_id = f"TKT-{uuid.uuid4().hex[:6].upper()}"
    created_at = datetime.now(timezone.utc).isoformat()
    try:
        execute(
            "INSERT INTO tickets (id, title, description, priority, status, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            [ticket_id, title, description, priority, "open", created_at],
        )
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
