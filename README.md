# Nexus — Workplace AI Assistant

[![ci](https://github.com/ronikdedhia/Nexus/actions/workflows/ci.yml/badge.svg)](https://github.com/ronikdedhia/Nexus/actions/workflows/ci.yml)

**[Live demo](https://nexus-ten-murex.vercel.app/)** · API: `https://nexus-2a14.onrender.com` (free tier: the first request after a quiet spell takes up to a minute while the server wakes)

Nexus answers employee questions and takes simple actions through one chat box. Ask about leave or IT policy, look up a colleague, raise a ticket, apply for leave, compare quarterly reports, or summarize a meeting. An LLM agent picks the right tool, reads real data from the database or the policy documents, and replies.

![stack](https://img.shields.io/badge/FastAPI-LangChain-blue) ![llm](https://img.shields.io/badge/LLM-Groq%20gpt--oss--120b-orange) ![ui](https://img.shields.io/badge/UI-React%20%2B%20Vite-61dafb)

## What it does

**Chat (`POST /ask`)**: the agent chooses from these tools:

| Tool | What it does |
|---|---|
| `rag_search` | Searches the policy documents (HR, IT, Finance, Marketing) and cites the sources it used |
| `create_ticket` | Opens an IT/support ticket. It searches the documents first and offers a self-serve answer if one matches well (`AUTO_RESOLVE_THRESHOLD`). It sets priority from words like "urgent" or "no rush" and assigns a department from keywords. Every change goes into an audit trail |
| `list_open_tickets` | Lists the 20 most recent open tickets |
| `get_employee` | Looks up an employee by ID or name. If a partial name matches more than one person, the agent asks which one you meant |
| `get_org_chart` | Lists someone's direct reports |
| `list_departments` | Lists departments with headcounts |
| `apply_leave` | Requests leave. Requests of up to `AUTO_APPROVE_LEAVE_DAYS` (default 2) are approved and deducted immediately; longer ones wait for a manager |
| `get_digest` | Shows an employee's leave balance, their pending leave requests, and company-wide open tickets by priority |
| `generate_report` | Fetches a sales, HR or IT report for a quarter (latest by default) and lists the other quarters available, for comparisons |
| `get_stats` | Shows employee count, open and total tickets, and average leave balance |
| `summarize_meeting` | Turns a transcript into a summary, decisions, and action items with owners |

**Web app** (`frontend/`): Chat (Markdown answers, suggested questions, new chat), Tickets (filter, change status, comments, CSV export), Employees (search and department filter).

**Automation**: tickets left open longer than `SLA_HOURS` are escalated to high priority, and a daily ops digest goes to Slack.

**Slack**: mention the bot in a channel or DM it, and it answers using the same agent.

## Architecture

```
React (Vercel) ──X-API-Key──▶ FastAPI (Render) ──▶ LangChain agent ──▶ Groq LLM
                                   │                    │
                                   │                    ├──▶ Turso (employees, tickets, leave, reports)
                                   │                    └──▶ Qdrant (policy documents, FastEmbed bge-small)
                                   ├──▶ Upstash Redis (chat memory, optional)
Slack ──signed webhook──▶ /slack/events        GitHub Actions cron ──▶ SLA sweep + digest
```

| File | Role |
|---|---|
| `main.py` | FastAPI app: routes, auth, CORS, request IDs, Slack webhook |
| `agent.py` | Agent setup and system prompt; extracts the answer, last tool used and sources |
| `tools.py` | Agent tools and the business logic behind them |
| `llm.py` | Groq client. The model is set here or with `GROQ_MODEL` |
| `rag.py`, `embeddings.py` | Qdrant search and document management |
| `db.py` | Turso HTTP client, schema and migrations |
| `memory.py` | Per-session chat history (Redis, or in-process) |
| `scheduler.py` | SLA sweep and daily digest jobs |
| `integrations.py` | Slack event filtering, dedupe and replies |
| `ratelimit.py` | Per-client limit on routes that call the LLM |
| `seed.py`, `ingest_docs.py` | Load sample data and documents |

## Quick start (local)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env        # fill in Groq, Turso, Qdrant and two API keys
python seed.py              # once: tables, sample employees/reports, policy docs
uvicorn main:app --reload   # http://localhost:8000/docs
```

Frontend:

```bash
cd frontend
npm install
cp .env.example .env        # VITE_API_URL, VITE_API_KEY (the *user* key)
npm run dev                 # http://localhost:5173
```

Add your own documents (`.txt`, `.md`, `.pdf`): `python ingest_docs.py path/to/folder`. Each file becomes a source, and files already loaded are skipped.

Tests need no network or credentials. Turso is emulated with SQLite and the LLM is stubbed:

```bash
pytest tests
```

## Configuration

Everything is set through environment variables; see `.env.example`.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GROQ_API_KEY` | yes | | Groq API key |
| `GROQ_MODEL` | | `openai/gpt-oss-120b` | Change this when Groq [retires a model](https://console.groq.com/docs/deprecations); no code change needed |
| `TURSO_URL`, `TURSO_TOKEN` | yes | | Database |
| `QDRANT_URL`, `QDRANT_API_KEY` | yes | | Document search |
| `NEXUS_API_KEY` | yes* | | User key |
| `NEXUS_ADMIN_API_KEY` | yes* | | Admin key |
| `NEXUS_AUTH_DISABLED` | | `false` | `true` opens every route; local development only |
| `CORS_ORIGINS` | | `http://localhost:5173` | Comma-separated browser origins allowed to call the API |
| `LLM_RATE_LIMIT_PER_MINUTE` | | `10` | Per-client limit on `/ask` and `/meetings/summarize`; `0` disables it |
| `UPSTASH_REDIS_REST_URL`, `_TOKEN` | | | Chat memory that survives restarts (30-minute TTL); otherwise kept in-process |
| `AUTO_RESOLVE_THRESHOLD` | | `0.72` | Document match score needed to offer a self-serve answer instead of a ticket |
| `SLA_HOURS` | | `24` | Hours before an open ticket is escalated |
| `AUTO_APPROVE_LEAVE_DAYS` | | `2` | Longest leave request approved without a manager |
| `NEXUS_ENABLE_SCHEDULER` | | `true` | In-process jobs; set `false` on hosts that sleep (use the cron instead) |
| `SLA_SWEEP_MINUTES`, `DIGEST_HOUR` | | `30`, `9` | Job timing (UTC) |
| `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_DIGEST_CHANNEL` | | | Slack. The webhook stays disabled until the signing secret is set |

\* The server refuses to start with neither key set, unless `NEXUS_AUTH_DISABLED=true`.

## Security model

- **Two API keys**, sent as the `X-API-Key` header. The user key covers chat, reading data, ticket status and comments, export, digests and meeting summaries. The admin key also covers the routes marked **admin** below.
- **Public routes:** `/health`, `/docs`, `/openapi.json` and `/redoc`. `/slack/events` is verified with Slack's signature instead (requests older than 5 minutes are rejected).
- **Rate limiting:** each client gets `LLM_RATE_LIMIT_PER_MINUTE` LLM calls per minute, counted per process.
- **Errors:** failures return a generic message plus an `X-Request-ID`. The details only go to the server log.
- **Input limits:** every request body has a size limit (for example, questions are capped at 2,000 characters).

Known limits, which you should fix before using this with real employees:
- **There are no user accounts.** Anyone with the user key can apply leave for, or look up, any employee. Put SSO in front of it.
- **The frontend's key is public.** Vite builds `VITE_API_KEY` into the JavaScript. Only ever give the frontend the user key.
- **Anyone with the user key can read every document** through chat. Don't load anything you wouldn't show every employee.

## API

Every route except the public ones needs a key. **admin** marks routes that need the admin key.

**Chat and AI**

| Route | Does |
|---|---|
| `POST /ask` | `{"question": "...", "session_id": "optional"}` → `{answer, action_taken, action_result, sources, session_id}` |
| `POST /meetings/summarize` | `{"transcript": "..."}` → `{summary, decisions, action_items}` |
| `GET /digest/{id-or-name}` | Personal digest |

**Employees**

| Route | Does |
|---|---|
| `GET /employees` | `?department=`, `?q=` (name/email/role), `?limit=`, `?offset=` |
| `GET /employees/{id}` · `GET /employees/{id}/reports` | One employee · their direct reports |
| `PATCH /employees/{id}` | **admin**: update `leave_balance`, `role` or `department` |
| `GET /departments` | Departments with headcounts |

**Tickets**

| Route | Does |
|---|---|
| `GET /tickets` | `?status=`, `?limit=`, `?offset=` |
| `GET /tickets/{id}` · `PATCH /tickets/{id}` | One ticket · set status (`open` / `in_progress` / `closed`) |
| `DELETE /tickets/{id}` | **admin**: delete (audit event logged) |
| `GET /tickets/{id}/history` | Audit trail |
| `GET`/`POST /tickets/{id}/comments` | List / add comments |
| `GET /tickets/export` | CSV of all tickets |
| `GET /tickets/sla-breaches` | Open tickets past `SLA_HOURS` |
| `POST /tickets/sla-breaches/escalate` | **admin**: run the SLA sweep now |

**Leave**

| Route | Does |
|---|---|
| `GET /leave-requests` | **admin**: `?status=pending/approved/rejected` |
| `POST /leave-requests/{id}/approve` · `/reject` | **admin**: decide. Approval deducts the balance, and a request can't be approved twice |

**Reports and stats**

| Route | Does |
|---|---|
| `GET /reports/{type}/trend` · `/export` | All quarters for a type, oldest first · as CSV |
| `POST /reports` | **admin**: add `{type, period, summary}` |
| `GET /stats` · `/stats/tickets-by-priority` · `/stats/roi` | Headline numbers · tickets by priority · deflection and escalation counters |

**Documents and sessions**

| Route | Does |
|---|---|
| `GET /rag/search?q=&k=` · `GET /rag/sources` | **admin**: raw search with scores · list sources |
| `POST /rag/documents` · `DELETE /rag/documents/{source}` | **admin**: add or remove documents |
| `GET /session/{id}/history` | **admin**: stored chat |
| `DELETE /session/{id}` | Clear a chat |

**Ops**

| Route | Does |
|---|---|
| `GET /health` | Liveness check (public) |
| `GET /health/deep` | Turso, Qdrant and Redis each reported as `up` / `down` / `not_configured` |
| `GET /version` · `GET /metrics` | Version and commit · Prometheus counters |
| `POST /ops/digest` | **admin**: send the daily digest now |
| `POST /slack/events` | Slack Events API webhook |

## Deployment

**Backend on Render**: a web service from this repo (or `render.yaml` as a Blueprint).
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Health check: `/health`
- Environment: the required variables above, plus `CORS_ORIGINS=https://<your-vercel-domain>` and `NEXUS_ENABLE_SCHEDULER=false`.

**Frontend on Vercel**: root directory `frontend`, framework Vite. Set `VITE_API_URL` (the Render URL) and `VITE_API_KEY` (the user key). Vercel only reads these at build time, so redeploy after changing them.

**Scheduled jobs**: Render's free tier sleeps, so the in-process scheduler can't be relied on. `.github/workflows/ops-cron.yml` runs the SLA sweep every 30 minutes and the digest daily at 09:00 UTC. Add the repository secrets `NEXUS_URL` and `NEXUS_ADMIN_API_KEY` to enable it.

**Slack**: point Event Subscriptions at `https://<backend>/slack/events`. Subscribe to `app_mention` and `message.im`, and give the bot the `chat:write` scope.

**CI**: `.github/workflows/ci.yml` runs the backend tests and the frontend lint and build on every push and pull request.

## Changelog

**1.1.0**
- **Chat:** fixed. Groq retired `llama-3.3-70b-versatile` on 16 Aug 2026; the default is now `openai/gpt-oss-120b`, configurable with `GROQ_MODEL`.
- **Security:** user and admin API keys; the server refuses to start without one; Slack requests must be signed; CORS works with auth; per-client rate limit on LLM routes; generic error messages; input size limits.
- **Fixes:**
  - `seed.py` no longer crashes, and no longer wipes added documents.
  - Leave can't go to the wrong person when a name is ambiguous, or be deducted twice.
  - Reports pick the right quarter.
  - The SLA sweep escalates each ticket once and catches up at startup; a cron covers hosts that sleep.
  - Slack sends one reply per message.
  - Numbers come back as numbers.
- **Removed:** the marketing content generator (it hardcoded a single company's persona and was outside the HR/IT scope) and the deprecated `langchain-community` dependency (stored vectors are unchanged).
- **Added:** Employees tab, Markdown answers, suggested questions, a cold-start notice, a test suite, CI, pinned dependencies and `render.yaml`.
