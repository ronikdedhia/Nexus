import os
import io
import csv
import time
import uuid
import logging
import subprocess
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, AIMessage

from models import AskRequest, AskResponse, ErrorResponse, TicketStatusUpdate, EmployeeUpdate, ReportCreate, TicketCommentCreate
from agent import run_agent
from memory import load_history, save_history, clear_history, ping as memory_ping
from db import execute, rows_to_dicts, ping as db_ping
from rag import search_with_scores, list_sources, ping as rag_ping
from metrics import record_request, render_prometheus
from tools import log_ticket_event

VALID_TICKET_STATUSES = {"open", "in_progress", "closed"}

logger = logging.getLogger("nexus")
logging.basicConfig(level=logging.INFO)

API_KEY = os.getenv("NEXUS_API_KEY")
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL
        ).decode().strip()
    except Exception:
        return "unknown"


GIT_COMMIT = _git_commit()


def _to_csv(rows: list) -> str:
    if not rows:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return buf.getvalue()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # warm up agent on startup (loads Groq client + tool schemas)
    from agent import get_agent
    get_agent()
    yield


app = FastAPI(
    title="Nexus — Enterprise AI Assistant",
    description="Ask anything. Act instantly.",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_id(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


@app.middleware("http")
async def request_logging(request: Request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000, 1)
    req_id = getattr(request.state, "request_id", "-")
    logger.info(f"[{req_id}] {request.method} {request.url.path} {response.status_code} {duration_ms}ms")
    record_request(request.url.path)
    return response


@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    if API_KEY and request.url.path not in PUBLIC_PATHS:
        if request.headers.get("X-API-Key") != API_KEY:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=401, content={"error": "Missing or invalid X-API-Key header", "code": "UNAUTHORIZED"})
    return await call_next(request)


@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest):
    session_id = req.session_id or str(uuid.uuid4())

    try:
        history = await run_in_threadpool(load_history, session_id)
    except Exception:
        history = []

    try:
        result = await run_in_threadpool(run_agent, req.question, history)
    except Exception as e:
        err = str(e).lower()
        if "rate limit" in err or "429" in err:
            raise HTTPException(status_code=429, detail={"error": "Groq rate limit hit. Retry in a moment.", "code": "RATE_LIMITED"})
        raise HTTPException(status_code=500, detail={"error": str(e), "code": "AGENT_ERROR"})

    updated_history = history + [
        HumanMessage(content=req.question),
        AIMessage(content=result["answer"]),
    ]
    try:
        await run_in_threadpool(save_history, session_id, updated_history)
    except Exception:
        pass  # memory failure is non-fatal

    return AskResponse(
        answer=result["answer"],
        action_taken=result.get("action_taken"),
        action_result=result.get("action_result"),
        sources=result.get("sources"),
        session_id=session_id,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nexus"}


@app.get("/version")
async def version():
    return {"version": app.version, "commit": GIT_COMMIT}


@app.get("/metrics")
async def metrics():
    return Response(content=render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/health/deep")
async def health_deep():
    turso, qdrant, redis = await run_in_threadpool(
        lambda: (db_ping(), rag_ping(), memory_ping())
    )
    services = {"turso": turso, "qdrant": qdrant, "redis": redis}
    overall = "ok" if all(s["status"] in ("up", "not_configured") for s in services.values()) else "degraded"
    return {"status": overall, "services": services}


@app.get("/employees")
async def list_employees(
    department: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    where = []
    params = []
    if department:
        where.append("department = ?")
        params.append(department)
    if q:
        where.append("(name LIKE ? OR email LIKE ? OR role LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])

    sql = "SELECT * FROM employees"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    result = await run_in_threadpool(execute, sql, params)
    return rows_to_dicts(result)


@app.get("/departments")
async def list_departments():
    result = await run_in_threadpool(
        execute,
        "SELECT department, COUNT(*) as employee_count FROM employees GROUP BY department ORDER BY department",
    )
    return rows_to_dicts(result)


@app.patch("/employees/{employee_id}")
async def update_employee(employee_id: str, body: EmployeeUpdate):
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM employees WHERE id = ?", [employee_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No employee found with id '{employee_id}'", "code": "NOT_FOUND"})

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(status_code=400, detail={"error": "No fields provided to update", "code": "NO_UPDATE_FIELDS"})

    set_clause = ", ".join(f"{field} = ?" for field in updates)
    await run_in_threadpool(
        execute, f"UPDATE employees SET {set_clause} WHERE id = ?", [*updates.values(), employee_id]
    )
    updated = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM employees WHERE id = ?", [employee_id]
    ))
    return updated[0]


@app.get("/employees/{employee_id}")
async def get_employee_endpoint(employee_id: str):
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM employees WHERE id = ?", [employee_id]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No employee found with id '{employee_id}'", "code": "NOT_FOUND"})
    return rows[0]


@app.get("/employees/{employee_id}/reports")
async def employee_reports(employee_id: str):
    emp = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT name FROM employees WHERE id = ?", [employee_id]
    ))
    if not emp:
        raise HTTPException(status_code=404, detail={"error": f"No employee found with id '{employee_id}'", "code": "NOT_FOUND"})
    result = await run_in_threadpool(
        execute, "SELECT * FROM employees WHERE manager = ?", [emp[0]["name"]]
    )
    return rows_to_dicts(result)


@app.get("/tickets")
async def list_tickets(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    if status:
        result = await run_in_threadpool(
            execute,
            "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [status, limit, offset],
        )
    else:
        result = await run_in_threadpool(
            execute, "SELECT * FROM tickets ORDER BY created_at DESC LIMIT ? OFFSET ?", [limit, offset]
        )
    return rows_to_dicts(result)


@app.get("/tickets/export")
async def export_tickets():
    rows = rows_to_dicts(await run_in_threadpool(execute, "SELECT * FROM tickets ORDER BY created_at DESC"))
    csv_data = _to_csv(rows)
    return Response(content=csv_data, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=tickets.csv"})


@app.get("/tickets/{ticket_id}")
async def get_ticket(ticket_id: str):
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    return rows[0]


@app.patch("/tickets/{ticket_id}")
async def update_ticket(ticket_id: str, body: TicketStatusUpdate):
    if body.status not in VALID_TICKET_STATUSES:
        raise HTTPException(status_code=400, detail={"error": f"status must be one of {sorted(VALID_TICKET_STATUSES)}", "code": "INVALID_STATUS"})
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    await run_in_threadpool(
        execute, "UPDATE tickets SET status = ? WHERE id = ?", [body.status, ticket_id]
    )
    await run_in_threadpool(log_ticket_event, ticket_id, body.status, "status updated via API")
    updated = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM tickets WHERE id = ?", [ticket_id]
    ))
    return updated[0]


@app.delete("/tickets/{ticket_id}")
async def delete_ticket(ticket_id: str):
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT id FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    await run_in_threadpool(log_ticket_event, ticket_id, "deleted", "ticket deleted via API")
    await run_in_threadpool(execute, "DELETE FROM tickets WHERE id = ?", [ticket_id])
    return {"status": "deleted", "ticket_id": ticket_id}


@app.get("/tickets/{ticket_id}/history")
async def ticket_history(ticket_id: str):
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT id FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    events = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM ticket_events WHERE ticket_id = ? ORDER BY created_at ASC", [ticket_id]
    ))
    return events


@app.post("/tickets/{ticket_id}/comments")
async def add_ticket_comment(ticket_id: str, body: TicketCommentCreate):
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT id FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    comment_id = uuid.uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    await run_in_threadpool(
        execute,
        "INSERT INTO ticket_comments (id, ticket_id, author, comment, created_at) VALUES (?, ?, ?, ?, ?)",
        [comment_id, ticket_id, body.author, body.comment, created_at],
    )
    return {"id": comment_id, "ticket_id": ticket_id, "author": body.author, "comment": body.comment, "created_at": created_at}


@app.get("/tickets/{ticket_id}/comments")
async def list_ticket_comments(ticket_id: str):
    existing = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT id FROM tickets WHERE id = ?", [ticket_id]
    ))
    if not existing:
        raise HTTPException(status_code=404, detail={"error": f"No ticket found with id '{ticket_id}'", "code": "NOT_FOUND"})
    comments = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM ticket_comments WHERE ticket_id = ? ORDER BY created_at ASC", [ticket_id]
    ))
    return comments


@app.post("/reports")
async def create_report(body: ReportCreate):
    report_id = f"RPT-{uuid.uuid4().hex[:6].upper()}"
    await run_in_threadpool(
        execute,
        "INSERT INTO reports (id, type, period, summary) VALUES (?, ?, ?, ?)",
        [report_id, body.type.lower(), body.period, body.summary],
    )
    return {"id": report_id, "type": body.type.lower(), "period": body.period, "summary": body.summary}


@app.get("/reports/{report_type}/trend")
async def report_trend(report_type: str):
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM reports WHERE type = ? ORDER BY period", [report_type.lower()]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No reports found for type '{report_type}'", "code": "NOT_FOUND"})
    return rows


@app.get("/reports/{report_type}/export")
async def export_reports(report_type: str):
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM reports WHERE type = ? ORDER BY period", [report_type.lower()]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No reports found for type '{report_type}'", "code": "NOT_FOUND"})
    csv_data = _to_csv(rows)
    return Response(content=csv_data, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_type.lower()}_reports.csv"})


@app.get("/stats")
async def stats():
    employees = rows_to_dicts(await run_in_threadpool(execute, "SELECT leave_balance FROM employees"))
    open_tickets = rows_to_dicts(await run_in_threadpool(execute, "SELECT id FROM tickets WHERE status = 'open'"))
    all_tickets = rows_to_dicts(await run_in_threadpool(execute, "SELECT id FROM tickets"))
    balances = [int(e["leave_balance"]) for e in employees if e["leave_balance"] is not None]
    avg_leave = round(sum(balances) / len(balances), 1) if balances else 0
    return {
        "total_employees": len(employees),
        "open_tickets": len(open_tickets),
        "total_tickets": len(all_tickets),
        "avg_leave_balance": avg_leave,
    }


@app.get("/stats/tickets-by-priority")
async def stats_tickets_by_priority():
    result = await run_in_threadpool(
        execute, "SELECT priority, COUNT(*) as count FROM tickets GROUP BY priority"
    )
    return rows_to_dicts(result)


@app.get("/session/{session_id}/history")
async def session_history(session_id: str):
    history = await run_in_threadpool(load_history, session_id)
    return [
        {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in history
    ]


@app.delete("/session/{session_id}")
async def delete_session(session_id: str):
    await run_in_threadpool(clear_history, session_id)
    return {"status": "cleared", "session_id": session_id}


@app.get("/rag/search")
async def rag_search_endpoint(q: str = Query(..., min_length=1), k: int = Query(3, ge=1, le=10)):
    try:
        results = await run_in_threadpool(search_with_scores, q, k)
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e), "code": "RAG_UNAVAILABLE"})
    return {"query": q, "results": results}


@app.get("/rag/sources")
async def rag_sources_endpoint():
    try:
        sources = await run_in_threadpool(list_sources)
    except Exception as e:
        raise HTTPException(status_code=503, detail={"error": str(e), "code": "RAG_UNAVAILABLE"})
    return {"sources": sources}
