# Nexus — Enterprise AI Assistant

FastAPI backend that lets employees query HR policies, look up colleagues, raise IT tickets, pull business reports, get leave approved, summarize meetings, and draft on-brand marketing content — all via a single chat endpoint, plus a full REST API and a Slack integration.

## What it does

**POST `/ask`** — send a question, get an answer. The agent decides which tool(s) to call:

| Tool | What it does |
|---|---|
| `get_employee` | Look up employee info by ID or name (from Turso DB) |
| `create_ticket` | Create an IT/support ticket. Checks the knowledge base first and returns a self-serve answer instead of opening a ticket if a good match exists (`AUTO_RESOLVE_THRESHOLD`); pass `force_create=True` to open one anyway. Priority auto-escalates to `high`/`low` based on keywords ("urgent", "asap" / "whenever", "no rush") when not explicitly set. Department is auto-classified by keyword (IT/HR/Finance/Sales/Marketing/Operations). Every creation and status change is logged to a `ticket_events` audit trail |
| `generate_report` | Fetch a sales / HR / IT summary report |
| `rag_search` | Semantic search over internal HR/IT/Finance/Marketing policies (Qdrant) |
| `get_stats` | Company-wide stats: employee count, open/total tickets, avg leave balance |
| `list_open_tickets` | List currently open tickets, most recent first |
| `get_org_chart` | Direct reports for an employee (by ID or name) |
| `apply_leave` | Request leave for an employee. Requests of `AUTO_APPROVE_LEAVE_DAYS` (default 2) or fewer are auto-approved and deducted immediately; longer requests are created as `pending` in `leave_requests` and need a manager decision |
| `list_departments` | List all departments with employee counts |
| `summarize_meeting` | Turn a raw transcript into a summary, decisions made, and action items with owners |
| `generate_marketing_content` | Draft a social post / ad copy / campaign blurb for a topic, grounded in the brand-guideline and campaign-policy docs in the knowledge base |
| `get_digest` | Personal daily digest for an employee: leave balance, their pending leave requests, and company-wide open ticket counts by priority |

Conversation history is stored per-session in Upstash Redis (falls back to in-memory if Redis is not configured). When the agent uses `rag_search`, the matched document sources are surfaced in the response's `sources` field.

Beyond the chat endpoint, plain REST routes expose the underlying data directly (see [API](#api) below) — employees, tickets (with audit history + comments), leave approvals, reports, departments, session history, CSV export, KB document management, and raw RAG search.

Two background jobs run on a schedule (see [Automation](#automation)): an SLA sweep that auto-escalates tickets open too long, and a daily ops digest posted to Slack.

A Slack app can talk to the same agent directly via `/slack/events` (see [Slack integration](#slack-integration)).

A React/Vite frontend in `frontend/` gives all of this a UI — chat, employee directory, ticket board (see [Frontend](#frontend)).

## Auth & logging

Every request gets an `X-Request-ID` (generated, or echoed if the caller sends one) and is logged as `[request-id] method path status latency_ms`.

Set `NEXUS_API_KEY` in `.env` to require an `X-API-Key` header on every route except `/health`, `/docs`, `/openapi.json`, `/redoc`. Leave it unset to run without auth (local/dev default).

`CORS_ORIGINS` controls allowed origins for browser clients (comma-separated, defaults to `*`).

## Stack

- **LLM** — Groq (Llama 3.3 70B) via LangChain
- **Structured DB** — Turso (libSQL) — employees, tickets, ticket_events, ticket_comments, reports, leave_requests
- **Vector DB** — Qdrant — embedded HR/IT/Finance/Marketing policy documents, editable at runtime via the RAG document API
- **Memory** — Upstash Redis (30-min TTL per session)
- **Scheduler** — APScheduler (in-process) — SLA sweep + daily digest
- **API** — FastAPI
- **Chat surfaces** — REST (`/ask`), Slack Events API (`/slack/events`)
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

# Auto-resolve / SLA / leave — all optional, sane defaults shown
AUTO_RESOLVE_THRESHOLD=0.72   # KB similarity score above which create_ticket deflects instead of opening a ticket
SLA_HOURS=24                  # open tickets below this priority breach after this many hours and get escalated
AUTO_APPROVE_LEAVE_DAYS=2     # leave requests at or under this many days skip manager approval

# Scheduler — optional
NEXUS_ENABLE_SCHEDULER=true   # set false to disable the background jobs entirely
SLA_SWEEP_MINUTES=30
DIGEST_HOUR=9                 # UTC hour the daily digest job fires

# Slack — optional, everything no-ops gracefully if unset
SLACK_BOT_TOKEN=...
SLACK_SIGNING_SECRET=...      # verifies /slack/events requests if set; unverified if left unset
SLACK_DIGEST_CHANNEL=...      # channel ID the daily digest and SLA-breach alerts post to
```

Seed the database and vector store (run once):

```bash
python seed.py
```

Already seeded before and just pulling schema changes (new `department`/`resolved_by_kb` ticket columns, the `leave_requests` table)? `seed.py` re-running is safe, or just start the app — `main.py`'s startup hook runs the same idempotent migration automatically.

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
| `GET /tickets/sla-breaches` | Open, non-high-priority tickets currently past `SLA_HOURS` |
| `POST /tickets/sla-breaches/escalate` | Run the SLA sweep now — escalates breaching tickets to `high` and logs an audit event (same job the scheduler runs every `SLA_SWEEP_MINUTES`) |
| `GET /tickets/{id}` | Single ticket (now includes `department` and `resolved_by_kb`) |
| `PATCH /tickets/{id}` | Update status (`open` / `in_progress` / `closed`) — logs to audit trail |
| `DELETE /tickets/{id}` | Delete a ticket (logs a `deleted` audit event first) |
| `GET /tickets/{id}/history` | Full status-change audit trail for a ticket |
| `POST /tickets/{id}/comments` | Add a comment (`author`, `comment`) |
| `GET /tickets/{id}/comments` | List comments on a ticket |

### Leave requests

| Route | Does |
|---|---|
| `GET /leave-requests` | List leave requests. `?status=` (`pending` / `approved` / `rejected`) |
| `POST /leave-requests/{id}/approve` | Approve a pending request — deducts the balance |
| `POST /leave-requests/{id}/reject` | Reject a pending request |

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
| `POST /rag/documents` | Add a document to the knowledge base (`text`, `source`) |
| `DELETE /rag/documents/{source}` | Remove all chunks for a source |

### Stats

| Route | Does |
|---|---|
| `GET /stats` | Total employees, open/total tickets, avg leave balance |
| `GET /stats/tickets-by-priority` | Ticket counts grouped by priority |
| `GET /stats/roi` | Tickets created vs. auto-resolved by KB, deflection rate, estimated minutes saved, escalations, digests sent, marketing content generated — all counters reset on process restart |

### Digest, meetings, marketing

| Route | Does |
|---|---|
| `GET /digest/{id}` | On-demand personal digest for an employee (ID or name) |
| `POST /meetings/summarize` | `{"transcript": "..."}` → `{summary, decisions, action_items}` |
| `POST /marketing/generate` | `{"topic": "...", "content_format": "social post"}` → on-brand draft grounded in the KB's marketing docs |

### Slack integration

| Route | Does |
|---|---|
| `POST /slack/events` | Slack Events API webhook — handles the `url_verification` handshake, then routes `app_mention` / DM events to the agent and replies in-channel via `chat.postMessage`. Verifies `X-Slack-Signature` if `SLACK_SIGNING_SECRET` is set; unverified otherwise |

Point your Slack app's Event Subscriptions at `https://<your-host>/slack/events`, subscribe to `app_mention`, and give the bot token `chat:write` scope.

## Automation

Two jobs run in-process via APScheduler (disable with `NEXUS_ENABLE_SCHEDULER=false`):

- **SLA sweep** (`SLA_SWEEP_MINUTES`, default 30) — escalates open, non-high-priority tickets past `SLA_HOURS` to `high` and logs an audit event. Posts a summary to `SLACK_DIGEST_CHANNEL` if configured.
- **Daily digest** (`DIGEST_HOUR`, default 9 UTC) — open ticket counts by priority plus the ROI numbers (deflection rate, minutes saved), posted to `SLACK_DIGEST_CHANNEL` or logged if Slack isn't configured.

## Frontend

`frontend/` is a React + Vite app: a chat panel, employee directory (search + department filter), and a ticket board (status changes, comments, CSV export).

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_URL if the backend isn't on localhost:8000
npm run dev
```

Requires the backend's CORS to allow the frontend's origin (`CORS_ORIGINS` in the backend `.env`; defaults to `*`).
