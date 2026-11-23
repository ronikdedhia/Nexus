# Nexus — Enterprise AI Assistant

🔴 **[Live demo](https://nexus-ten-murex.vercel.app/)**

FastAPI backend that lets employees query HR policies, look up colleagues, raise IT tickets, pull business reports, get leave approved, summarize meetings, and draft on-brand marketing content — all via a single chat endpoint, plus a full REST API and a Slack integration.

## What it does

**POST `/ask`** — send a question, get an answer. The agent decides which tool(s) to call:

| Tool | What it does |
|---|---|
| `get_employee` | Look up employee info by ID or name (from Turso DB). Exact ID/name wins; a partial name (3+ characters) is used only if exactly one employee matches, otherwise the agent asks which one you meant |
| `create_ticket` | Create an IT/support ticket. Checks the knowledge base first and returns a self-serve answer instead of opening a ticket if a good match exists (`AUTO_RESOLVE_THRESHOLD`); pass `force_create=True` to open one anyway. Priority auto-escalates to `high`/`low` based on keywords ("urgent", "asap" / "whenever", "no rush") when not explicitly set. Department is auto-classified by keyword (IT/HR/Finance/Sales/Marketing/Operations). Every creation and status change is logged to a `ticket_events` audit trail |
| `generate_report` | Fetch a sales / HR / IT summary report for a given period (e.g. `Q1 2025`), or the latest period by default |
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

A React/Vite frontend in `frontend/` gives the chat and ticket board a UI (see [Frontend](#frontend)).

## Auth & logging

Every request gets an `X-Request-ID` (generated, or echoed if the caller sends one) and is logged as `[request-id] method path status latency_ms`. Error responses include the request ID; the underlying error is only written to the server log.

Callers authenticate with an `X-API-Key` header. There are two keys:

| Key | Can do |
|---|---|
| `NEXUS_API_KEY` (user) | Chat, read employees/tickets/reports/stats, change ticket status, comment, export tickets, meeting summaries, marketing drafts, digests |
| `NEXUS_ADMIN_API_KEY` (admin) | Everything above, plus the routes marked **admin** below: edit employees, list/approve/reject leave, delete tickets, add reports, read raw KB search and session history, manage KB documents, trigger the SLA sweep and digest |

The server **refuses to start** if neither key is set. For local development only, `NEXUS_AUTH_DISABLED=true` opens every route.

Open without a key: `/health`, `/docs`, `/openapi.json`, `/redoc`. `/slack/events` is checked with Slack's request signature instead.

`CORS_ORIGINS` lists allowed browser origins (comma-separated, default `http://localhost:5173`). CORS wraps auth, so browser preflights work and 401 responses still reach the frontend.

Known limits:
- **There are no user accounts.** Anyone holding the user key can apply leave for any employee or look up any employee. Put real SSO in front of this before using it with real people.
- **The frontend's key is public.** Vite builds `VITE_API_KEY` into the JavaScript, so anyone who opens the site can read it. Only ever give the frontend the user key.
- There is no rate limiting. Every `/ask` costs Groq tokens, so add a limit at your proxy or host on public deployments.

## Stack

- **LLM** — Groq (`openai/gpt-oss-120b` by default, override with `GROQ_MODEL`) via LangChain
- **Structured DB** — Turso (libSQL) — employees, tickets, ticket_events, ticket_comments, reports, leave_requests
- **Vector DB** — Qdrant — embedded HR/IT/Finance/Marketing policy documents, editable at runtime via the RAG document API
- **Memory** — Upstash Redis (30-min TTL per session), in-process dict if unset
- **Scheduler** — APScheduler (in-process) — SLA sweep + daily digest
- **API** — FastAPI
- **Chat surfaces** — REST (`/ask`), Slack Events API (`/slack/events`)
- **Frontend** — React + Vite (`frontend/`)

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in. Required: `GROQ_API_KEY`, `TURSO_URL`, `TURSO_TOKEN`, `QDRANT_URL`, `QDRANT_API_KEY`, and at least one of `NEXUS_API_KEY` / `NEXUS_ADMIN_API_KEY`. Everything else is optional and documented in the file.

Groq retires models regularly (`llama-3.3-70b-versatile`, which this project used, was shut down on 16 Aug 2026). When that happens, set `GROQ_MODEL` to the replacement listed on [Groq's deprecations page](https://console.groq.com/docs/deprecations). No code change is needed.

Seed the database and vector store (run once):

```bash
python seed.py
```

Re-running `seed.py` is safe. It upserts the sample employees, reports and policy documents, and leaves any documents you added through `ingest_docs.py` or the API in place. The app's startup hook also creates missing tables and columns, so pulling schema changes only needs a restart.

Start the server:

```bash
uvicorn main:app --reload
```

Run the tests (no network or credentials needed; Turso is emulated with SQLite and the LLM is stubbed):

```bash
pip install pytest httpx
pytest tests
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

Returns `{"status": "ok", "service": "nexus"}` — no dependency checks, just confirms the process is alive. Needs no key.

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
| `PATCH /employees/{id}` | **admin** — Update `leave_balance` / `role` / `department` |
| `GET /departments` | Distinct departments with employee counts |

### Tickets

| Route | Does |
|---|---|
| `GET /tickets` | List tickets. `?status=`, `?limit=`, `?offset=` |
| `GET /tickets/export` | All tickets as CSV |
| `GET /tickets/sla-breaches` | Open, non-high-priority tickets currently past `SLA_HOURS` |
| `POST /tickets/sla-breaches/escalate` | **admin** — Run the SLA sweep now — escalates breaching tickets to `high` and logs an audit event (same job the scheduler runs every `SLA_SWEEP_MINUTES`) |
| `GET /tickets/{id}` | Single ticket (now includes `department` and `resolved_by_kb`) |
| `PATCH /tickets/{id}` | Update status (`open` / `in_progress` / `closed`) — logs to audit trail |
| `DELETE /tickets/{id}` | **admin** — Delete a ticket (logs a `deleted` audit event first) |
| `GET /tickets/{id}/history` | Full status-change audit trail for a ticket |
| `POST /tickets/{id}/comments` | Add a comment (`author`, `comment`) |
| `GET /tickets/{id}/comments` | List comments on a ticket |

### Leave requests

| Route | Does |
|---|---|
| `GET /leave-requests` | **admin** — List leave requests. `?status=` (`pending` / `approved` / `rejected`) |
| `POST /leave-requests/{id}/approve` | **admin** — Approve a pending request — deducts the balance |
| `POST /leave-requests/{id}/reject` | **admin** — Reject a pending request |

### Reports

| Route | Does |
|---|---|
| `GET /reports/{type}/trend` | All periods for a report type, oldest first (e.g. QoQ comparison) |
| `GET /reports/{type}/export` | All periods for a report type as CSV |
| `POST /reports` | **admin** — Add a new report (`type`, `period`, `summary`) |

### Sessions

| Route | Does |
|---|---|
| `GET /session/{id}/history` | **admin** — Dump stored chat history for a session |
| `DELETE /session/{id}` | Clear a session's memory |

### RAG

| Route | Does |
|---|---|
| `GET /rag/search?q=...&k=` | **admin** — Raw similarity search over indexed docs, with scores |
| `GET /rag/sources` | **admin** — List all indexed document source categories (cached 5 min) |
| `POST /rag/documents` | **admin** — Add a document to the knowledge base (`text`, `source`) |
| `DELETE /rag/documents/{source}` | **admin** — Remove all chunks for a source |

### Stats

| Route | Does |
|---|---|
| `GET /stats` | Total employees, open/total tickets, avg leave balance |
| `GET /stats/tickets-by-priority` | Ticket counts grouped by priority |
| `GET /stats/roi` | Tickets created vs. auto-resolved by KB, deflection rate, estimated minutes saved, escalations, digests sent, marketing content generated. Stored in Turso, so they survive restarts. Counting started when this feature was added, so older tickets aren't included |

### Digest, meetings, marketing

| Route | Does |
|---|---|
| `GET /digest/{id}` | On-demand personal digest for an employee (ID or name) |
| `POST /meetings/summarize` | `{"transcript": "..."}` → `{summary, decisions, action_items}` |
| `POST /marketing/generate` | `{"topic": "...", "content_format": "social post"}` → on-brand draft grounded in the KB's marketing docs |
| `POST /ops/digest` | **admin** — Send the daily ops digest now (for an external cron) |

### Slack integration

| Route | Does |
|---|---|
| `POST /slack/events` | Slack Events API webhook. Handles the `url_verification` handshake, then answers `app_mention` events in channels and plain messages in DMs via `chat.postMessage`. Requires `SLACK_SIGNING_SECRET` (the endpoint returns 503 without it) and rejects requests with a bad or stale `X-Slack-Signature`. It acknowledges Slack immediately and runs the agent in the background, and it ignores Slack retries, duplicate deliveries, bot messages and edits, so each message gets one reply |

Point your Slack app's Event Subscriptions at `https://<your-host>/slack/events`, subscribe to `app_mention` (and `message.im` for DMs), and give the bot token `chat:write` scope.

## Automation

Two jobs run in-process via APScheduler (disable with `NEXUS_ENABLE_SCHEDULER=false`):

- **SLA sweep** (`SLA_SWEEP_MINUTES`, default 30, plus once at startup) — escalates open, non-high-priority tickets past `SLA_HOURS` to `high` and logs an audit event. Posts a summary to `SLACK_DIGEST_CHANNEL` if configured. If two sweeps run at once, each ticket is still escalated only once.
- **Daily digest** (`DIGEST_HOUR`, default 9 UTC) — open ticket counts by priority plus the ROI numbers (deflection rate, minutes saved), posted to `SLACK_DIGEST_CHANNEL` or logged if Slack isn't configured.

**Hosts that sleep when idle (e.g. Render free tier) never run these jobs**, because the process isn't awake at the scheduled time. On such hosts, set `NEXUS_ENABLE_SCHEDULER=false` and use the included GitHub Actions workflow (`.github/workflows/ops-cron.yml`). It calls `POST /tickets/sla-breaches/escalate` every 30 minutes and `POST /ops/digest` daily. Add the repo secrets `NEXUS_URL` and `NEXUS_ADMIN_API_KEY` to enable it; until then it skips. The same applies if you run more than one worker process: disable the in-process scheduler and use the cron.

## Frontend

`frontend/` is a React + Vite app with two tabs: a chat panel and a ticket board (status changes, comments, CSV export).

```bash
cd frontend
npm install
cp .env.example .env   # set VITE_API_URL and VITE_API_KEY (the *user* key; it ends up public)
npm run dev
```

The backend's `CORS_ORIGINS` must include the frontend's origin (default `http://localhost:5173`).
