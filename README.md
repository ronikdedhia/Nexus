# Nexus — Enterprise AI Assistant

FastAPI backend that lets employees query HR policies, look up colleagues, raise IT tickets, and pull business reports — all via a single chat endpoint.

## What it does

**POST `/ask`** — send a question, get an answer. The agent decides which tool(s) to call:

| Tool | What it does |
|---|---|
| `get_employee` | Look up employee info by ID or name (from Turso DB) |
| `create_ticket` | Create an IT/support ticket. Priority auto-escalates to `high`/`low` based on keywords ("urgent", "asap" / "whenever", "no rush") when not explicitly set. Every creation and status change is logged to a `ticket_events` audit trail |
| `generate_report` | Fetch a sales / HR / IT summary report |
| `rag_search` | Semantic search over internal HR/IT/Finance/Marketing policies (Qdrant) |
| `get_stats` | Company-wide stats: employee count, open/total tickets, avg leave balance |
| `list_open_tickets` | List currently open tickets, most recent first |
| `get_org_chart` | Direct reports for an employee (by ID or name) |
| `apply_leave` | Deduct leave days from an employee's balance, validated against their remaining balance |

Conversation history is stored per-session in Upstash Redis (falls back to in-memory if Redis is not configured). When the agent uses `rag_search`, the matched document sources are surfaced in the response's `sources` field.

Beyond the chat endpoint, plain REST routes expose the underlying data directly (see [API](#api) below) — employees, tickets (with audit history + comments), reports, departments, session history, CSV export, and raw RAG search.

A React/Vite frontend in `frontend/` gives all of this a UI — chat, employee directory, ticket board (see [Frontend](#frontend)).

## Auth & logging

Every request gets an `X-Request-ID` (generated, or echoed if the caller sends one) and is logged as `[request-id] method path status latency_ms`.

Set `NEXUS_API_KEY` in `.env` to require an `X-API-Key` header on every route except `/health`, `/docs`, `/openapi.json`, `/redoc`. Leave it unset to run without auth (local/dev default).

`CORS_ORIGINS` controls allowed origins for browser clients (comma-separated, defaults to `*`).

## Stack

- **LLM** — Groq (Llama 3.3 70B) via LangChain
- **Structured DB** — Turso (libSQL) — employees, tickets, ticket_events, ticket_comments, reports
- **Vector DB** — Qdrant — embedded HR/IT policy documents
- **Memory** — Upstash Redis (30-min TTL per session)
- **API** — FastAPI
- **Frontend** — React + Vite (`frontend/`)

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file:

```
GROQ_API_KEY=...
TURSO_URL=libsql://...
TURSO_TOKEN=...
QDRANT_URL=...
QDRANT_API_KEY=...
UPSTASH_REDIS_REST_URL=...   # optional
UPSTASH_REDIS_REST_TOKEN=... # optional
NEXUS_API_KEY=...            # optional — enables X-API-Key auth on all routes except /health
CORS_ORIGINS=*                # optional — comma-separated allowed origins
```

Seed the database and vector store (run once):

```bash
python seed.py
```

Start the server:

```bash
uvicorn main:app --reload
```

## API

### `POST /ask`

```json
{
  "question": "How many days of casual leave do I have?",
  "session_id": "optional-uuid-for-continuity"
}
```

Response:

```json
{
  "answer": "Employees get 24 days of casual leave per year...",
  "action_taken": "rag_search",
  "action_result": "...",
  "sources": ["hr_leave_policy"],
  "session_id": "abc-123"
}
```

### `GET /health`

Returns `{"status": "ok", "service": "nexus"}` — no dependency checks, just confirms the process is alive.

### `GET /health/deep`

Pings Turso, Qdrant, and Redis individually and reports each as `up` / `down` / `not_configured`, plus an overall `ok`/`degraded` status.

### `GET /version`

Returns `{"version": "...", "commit": "<git short hash>"}`.

### `GET /metrics`

Prometheus-style plaintext: total requests (overall + per path), total `/ask` agent invocations, and agent tool usage broken down by tool name.

### Employees

| Route | Does |
|---|---|
| `GET /employees` | List employees. `?department=`, `?q=` (search name/email/role), `?limit=` (default 50), `?offset=` |
| `GET /employees/{id}` | Single employee |
| `GET /employees/{id}/reports` | Direct reports for an employee |
| `PATCH /employees/{id}` | Update `leave_balance` / `role` / `department` |
| `GET /departments` | Distinct departments with employee counts |

### Tickets

| Route | Does |
|---|---|
| `GET /tickets` | List tickets. `?status=`, `?limit=`, `?offset=` |
| `GET /tickets/export` | All tickets as CSV |
| `GET /tickets/{id}` | Single ticket |
| `PATCH /tickets/{id}` | Update status (`open` / `in_progress` / `closed`) — logs to audit trail |
| `DELETE /tickets/{id}` | Delete a ticket (logs a `deleted` audit event first) |
| `GET /tickets/{id}/history` | Full status-change audit trail for a ticket |
| `POST /tickets/{id}/comments` | Add a comment (`author`, `comment`) |
| `GET /tickets/{id}/comments` | List comments on a ticket |

### Reports

| Route | Does |
|---|---|
| `GET /reports/{type}/trend` | All periods for a report type (e.g. QoQ comparison) |
| `GET /reports/{type}/export` | All periods for a report type as CSV |
| `POST /reports` | Add a new report (`type`, `period`, `summary`) |

### Sessions

| Route | Does |
|---|---|
| `GET /session/{id}/history` | Dump stored chat history for a session |
| `DELETE /session/{id}` | Clear a session's memory |

### RAG

| Route | Does |
|---|---|
| `GET /rag/search?q=...&k=` | Raw similarity search over indexed docs, with scores |
| `GET /rag/sources` | List all indexed document source categories (cached 5 min) |

### Stats

| Route | Does |
|---|---|
| `GET /stats` | Total employees, open/total tickets, avg leave balance |
| `GET /stats/tickets-by-priority` | Ticket counts grouped by priority |

## Frontend

`frontend/` is a React + Vite app: a chat panel, employee directory (search + department filter), and a ticket board (status changes, comments, CSV export).

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_URL if the backend isn't on localhost:8000
npm run dev
```

Requires the backend's CORS to allow the frontend's origin (`CORS_ORIGINS` in the backend `.env`; defaults to `*`).
