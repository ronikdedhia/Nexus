# Nexus — Enterprise AI Assistant

FastAPI backend that lets employees query HR policies, look up colleagues, raise IT tickets, and pull business reports — all via a single chat endpoint.

## What it does

**POST `/ask`** — send a question, get an answer. The agent decides which tool(s) to call:

| Tool | What it does |
|---|---|
| `get_employee` | Look up employee info by ID or name (from Turso DB) |
| `create_ticket` | Create an IT/support ticket (stored in Turso DB) |
| `generate_report` | Fetch a sales / HR / IT summary report |
| `rag_search` | Semantic search over internal HR policies and IT guides (Qdrant) |

Conversation history is stored per-session in Upstash Redis (falls back to in-memory if Redis is not configured).

## Stack

- **LLM** — Groq (Llama 3.3 70B) via LangChain
- **Structured DB** — Turso (libSQL) — employees, tickets, reports
- **Vector DB** — Qdrant — embedded HR/IT policy documents
- **Memory** — Upstash Redis (30-min TTL per session)
- **API** — FastAPI

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
  "session_id": "abc-123"
}
```

### `GET /health`

Returns `{"status": "ok", "service": "nexus"}`.
