import os
import io
import csv
import hmac
import hashlib
import time
import uuid
import logging
import subprocess
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request, Depends, BackgroundTasks
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, AIMessage

from models import (
    AskRequest, AskResponse, ErrorResponse, TicketStatusUpdate, EmployeeUpdate,
    ReportCreate, TicketCommentCreate, MeetingTranscript, RagDocumentCreate,
)
from agent import run_agent
from memory import load_history, save_history, clear_history, ping as memory_ping
from db import execute, rows_to_dicts, ping as db_ping, migrate as db_migrate
from rag import search_with_scores, list_sources, add_document, delete_by_source, ping as rag_ping
from metrics import record_request, render_prometheus, get_roi_stats, load_persisted as load_persisted_metrics
from tools import (
    log_ticket_event, decide_leave_request,
    summarize_meeting, get_digest,
    _hours_since, SLA_HOURS, report_period_key,
)
from integrations import handle_slack_event, should_process_slack_event
from ratelimit import limit_llm_calls
from scheduler import start_scheduler, stop_scheduler, run_daily_digest, run_sla_sweep

SLACK_SIGNING_SECRET = os.getenv("SLACK_SIGNING_SECRET")

VALID_TICKET_STATUSES = {"open", "in_progress", "closed"}

logger = logging.getLogger("nexus")
logging.basicConfig(level=logging.INFO)

# ── Auth ─────────────────────────────────────────────────────────────────────
# Two keys:
#   NEXUS_API_KEY        user level: chat, reading data, ticket status/comments.
#   NEXUS_ADMIN_API_KEY  admin level: everything, including changing employees, deciding leave,
#                        deleting tickets, managing the knowledge base and reading raw search/sessions.
# The server refuses to start with neither key set, unless NEXUS_AUTH_DISABLED=true (local dev only).
API_KEY = os.getenv("NEXUS_API_KEY") or None
ADMIN_API_KEY = os.getenv("NEXUS_ADMIN_API_KEY") or None
AUTH_DISABLED = os.getenv("NEXUS_AUTH_DISABLED", "false").lower() == "true"
# /slack/events authenticates with Slack's request signature instead of an API key.
PUBLIC_PATHS = {"/health", "/docs", "/openapi.json", "/redoc", "/slack/events"}


def _check_auth_config():
    if AUTH_DISABLED:
        logger.warning("NEXUS_AUTH_DISABLED=true: every route is open. Never use this on a public deployment.")
        return
    if not API_KEY and not ADMIN_API_KEY:
        raise RuntimeError(
            "No API key configured. Set NEXUS_API_KEY (and NEXUS_ADMIN_API_KEY for admin routes), "
            "or set NEXUS_AUTH_DISABLED=true for local development."
        )


def _key_matches(supplied: str, expected: Optional[str]) -> bool:
    return bool(expected) and hmac.compare_digest(supplied.encode(), expected.encode())


def _is_admin(request: Request) -> bool:
    return AUTH_DISABLED or _key_matches(request.headers.get("X-API-Key", ""), ADMIN_API_KEY)


def _is_user(request: Request) -> bool:
    return _is_admin(request) or _key_matches(request.headers.get("X-API-Key", ""), API_KEY)


def require_admin(request: Request):
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail={"error": "This route needs the admin API key", "code": "FORBIDDEN"})


ADMIN = [Depends(require_admin)]


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


def _internal_error(request: Request, status: int, code: str, public_message: str, exc: Exception):
    """Log the real error server-side; return only a generic message plus the request ID to the caller."""
    req_id = getattr(request.state, "request_id", "-")
    logger.exception(f"[{req_id}] {code}: {exc}")
    return HTTPException(status_code=status, detail={"error": public_message, "code": code, "request_id": req_id})


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_auth_config()
    # warm up agent on startup (loads Groq client + tool schemas)
    from agent import get_agent
    get_agent()
    try:
        db_migrate()
    except Exception as e:
        logger.warning(f"Startup migration skipped: {e}")
    load_persisted_metrics()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title="Nexus — Enterprise AI Assistant",
    description="Ask anything. Act instantly.",
    version="1.1.0",
    lifespan=lifespan,
)


# Middleware order: Starlette runs the LAST-added middleware first. CORS is added last so it wraps
# everything, which means browser preflights are answered before auth and 401s still carry CORS headers.

@app.middleware("http")
async def api_key_auth(request: Request, call_next):
    if request.method != "OPTIONS" and request.url.path not in PUBLIC_PATHS and not _is_user(request):
        return JSONResponse(status_code=401, content={"error": "Missing or invalid X-API-Key header", "code": "UNAUTHORIZED"})
    return await call_next(request)


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
async def request_id(request: Request, call_next):
    req_id = (request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12])[:64]
    request.state.request_id = req_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = req_id
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.post("/ask", response_model=AskResponse, dependencies=[Depends(limit_llm_calls)])
async def ask(req: AskRequest, request: Request):
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
        raise _internal_error(request, 500, "AGENT_ERROR", "The assistant failed to answer. Please try again.", e)

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


@app.patch("/employees/{employee_id}", dependencies=ADMIN)
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


@app.get("/tickets/sla-breaches")
async def get_sla_breaches():
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT id, title, priority, created_at FROM tickets WHERE status = 'open' AND priority != 'high'"
    ))
    breaching = [r for r in rows if r.get("created_at") and _hours_since(r["created_at"]) >= SLA_HOURS]
    return {"sla_hours": SLA_HOURS, "breaching": breaching}


@app.post("/tickets/sla-breaches/escalate", dependencies=ADMIN)
async def run_sla_escalation():
    escalated = await run_in_threadpool(run_sla_sweep)
    return {"escalated": escalated}


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


@app.delete("/tickets/{ticket_id}", dependencies=ADMIN)
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


@app.post("/reports", dependencies=ADMIN)
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
        execute, "SELECT * FROM reports WHERE type = ?", [report_type.lower()]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No reports found for type '{report_type}'", "code": "NOT_FOUND"})
    return sorted(rows, key=lambda r: report_period_key(r.get("period")))


@app.get("/reports/{report_type}/export")
async def export_reports(report_type: str):
    rows = rows_to_dicts(await run_in_threadpool(
        execute, "SELECT * FROM reports WHERE type = ?", [report_type.lower()]
    ))
    if not rows:
        raise HTTPException(status_code=404, detail={"error": f"No reports found for type '{report_type}'", "code": "NOT_FOUND"})
    rows.sort(key=lambda r: report_period_key(r.get("period")))
    csv_data = _to_csv(rows)
    return Response(content=csv_data, media_type="text/csv", headers={"Content-Disposition": f"attachment; filename={report_type.lower()}_reports.csv"})


@app.get("/stats")
async def stats():
    employees = rows_to_dicts(await run_in_threadpool(execute, "SELECT id FROM employees"))
    open_tickets = rows_to_dicts(await run_in_threadpool(execute, "SELECT id FROM tickets WHERE status = 'open'"))
    all_tickets = rows_to_dicts(await run_in_threadpool(execute, "SELECT id FROM tickets"))
    return {
        "total_employees": len(employees),
        "open_tickets": len(open_tickets),
        "total_tickets": len(all_tickets),
    }


@app.get("/stats/tickets-by-priority")
async def stats_tickets_by_priority():
    result = await run_in_threadpool(
        execute, "SELECT priority, COUNT(*) as count FROM tickets GROUP BY priority"
    )
    return rows_to_dicts(result)


@app.get("/session/{session_id}/history", dependencies=ADMIN)
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


@app.get("/rag/search", dependencies=ADMIN)
async def rag_search_endpoint(request: Request, q: str = Query(..., min_length=1), k: int = Query(3, ge=1, le=10)):
    try:
        results = await run_in_threadpool(search_with_scores, q, k)
    except Exception as e:
        raise _internal_error(request, 503, "RAG_UNAVAILABLE", "Document search is unavailable", e)
    return {"query": q, "results": results}


@app.get("/rag/sources", dependencies=ADMIN)
async def rag_sources_endpoint(request: Request):
    try:
        sources = await run_in_threadpool(list_sources)
    except Exception as e:
        raise _internal_error(request, 503, "RAG_UNAVAILABLE", "Document search is unavailable", e)
    return {"sources": sources}


@app.post("/rag/documents", dependencies=ADMIN)
async def add_rag_document(body: RagDocumentCreate, request: Request):
    try:
        result = await run_in_threadpool(add_document, body.text, body.source)
    except Exception as e:
        raise _internal_error(request, 503, "RAG_UNAVAILABLE", "Document search is unavailable", e)
    return result


@app.delete("/rag/documents/{source}", dependencies=ADMIN)
async def delete_rag_document(source: str, request: Request):
    try:
        result = await run_in_threadpool(delete_by_source, source)
    except Exception as e:
        raise _internal_error(request, 503, "RAG_UNAVAILABLE", "Document search is unavailable", e)
    return result


# ── Leave approval workflow ─────────────────────────────────────────────────

@app.get("/leave-requests", dependencies=ADMIN)
async def list_leave_requests(status: Optional[str] = Query(None)):
    if status:
        result = await run_in_threadpool(
            execute, "SELECT * FROM leave_requests WHERE status = ? ORDER BY requested_at DESC", [status]
        )
    else:
        result = await run_in_threadpool(execute, "SELECT * FROM leave_requests ORDER BY requested_at DESC")
    return rows_to_dicts(result)


@app.post("/leave-requests/{request_id}/approve", dependencies=ADMIN)
async def approve_leave_request(request_id: str):
    result = await run_in_threadpool(decide_leave_request, request_id, True, "manager")
    if "error" in result:
        raise HTTPException(status_code=400, detail={"error": result["error"], "code": "LEAVE_REQUEST_ERROR"})
    return result


@app.post("/leave-requests/{request_id}/reject", dependencies=ADMIN)
async def reject_leave_request(request_id: str):
    result = await run_in_threadpool(decide_leave_request, request_id, False, "manager")
    if "error" in result:
        raise HTTPException(status_code=400, detail={"error": result["error"], "code": "LEAVE_REQUEST_ERROR"})
    return result


# ── ROI / digest ─────────────────────────────────────────────────────────────

@app.get("/stats/roi")
async def stats_roi():
    return get_roi_stats()


@app.get("/digest/{identifier}")
async def digest_endpoint(identifier: str):
    result = await run_in_threadpool(get_digest.invoke, {"identifier": identifier})
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=404, detail={"error": result["error"], "code": "NOT_FOUND"})
    return result


# ── Meeting summarizer ────────────────────────────────────────────────

@app.post("/meetings/summarize", dependencies=[Depends(limit_llm_calls)])
async def summarize_meeting_endpoint(body: MeetingTranscript, request: Request):
    result = await run_in_threadpool(summarize_meeting.invoke, {"transcript": body.transcript})
    if isinstance(result, dict) and "error" in result:
        raise _internal_error(request, 502, "SUMMARIZE_ERROR", "Meeting summary failed. Please try again.", RuntimeError(result["error"]))
    return result


# ── Ops triggers (for an external cron when the host sleeps) ────────────────

@app.post("/ops/digest", dependencies=ADMIN)
async def trigger_daily_digest():
    return await run_in_threadpool(run_daily_digest)


# ── Slack integration ────────────────────────────────────────────────────────

def _verify_slack_signature(request: Request, raw_body: bytes) -> bool:
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")
    if not SLACK_SIGNING_SECRET or not timestamp or not signature:
        return False
    try:
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    basestring = b"v0:" + timestamp.encode() + b":" + raw_body
    computed = "v0=" + hmac.new(SLACK_SIGNING_SECRET.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


@app.post("/slack/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    if not SLACK_SIGNING_SECRET:
        # Fail closed: without the signing secret anyone could make the bot act on their behalf.
        return JSONResponse(status_code=503, content={"error": "Slack integration not configured (SLACK_SIGNING_SECRET unset)"})
    raw_body = await request.body()
    if not _verify_slack_signature(request, raw_body):
        return JSONResponse(status_code=401, content={"error": "invalid Slack signature"})
    payload = await request.json()
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge")}
    # Slack retries any event it doesn't get a 200 for within 3s. Acknowledge right away, run the agent
    # in the background, and skip retries/duplicates so each message gets exactly one reply.
    if request.headers.get("X-Slack-Retry-Num") is None and should_process_slack_event(payload):
        background_tasks.add_task(handle_slack_event, payload)  # sync fn: Starlette runs it in a thread
    return {"ok": True}
