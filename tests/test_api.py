import hashlib
import hmac
import json
import time

import pytest
from fastapi.testclient import TestClient

import integrations
import main

USER, ADMIN = "user-key", "admin-key"
ORIGIN = "http://localhost:5173"


@pytest.fixture
def client(fake_db, monkeypatch):
    monkeypatch.setattr(main, "API_KEY", USER)
    monkeypatch.setattr(main, "ADMIN_API_KEY", ADMIN)
    monkeypatch.setattr(main, "AUTH_DISABLED", False)
    # No `with`: skip lifespan (it would build a real Groq client and start the scheduler).
    return TestClient(main.app, raise_server_exceptions=False)


def h(key):
    return {"X-API-Key": key}


# ── auth ────────────────────────────────────────────────────────────────────

def test_startup_refuses_without_any_key(monkeypatch):
    monkeypatch.setattr(main, "API_KEY", None)
    monkeypatch.setattr(main, "ADMIN_API_KEY", None)
    monkeypatch.setattr(main, "AUTH_DISABLED", False)
    with pytest.raises(RuntimeError, match="No API key configured"):
        main._check_auth_config()


def test_health_is_public(client):
    assert client.get("/health").status_code == 200


def test_routes_need_a_key(client):
    assert client.get("/tickets").status_code == 401
    assert client.get("/tickets", headers=h("wrong")).status_code == 401
    assert client.get("/tickets", headers=h(USER)).status_code == 200


@pytest.mark.parametrize("method,path,body", [
    ("patch", "/employees/EMP001", {"leave_balance": 99}),
    ("delete", "/tickets/TKT-1", None),
    ("get", "/leave-requests", None),
    ("post", "/leave-requests/LR-1/approve", None),
    ("get", "/rag/search?q=vpn", None),
    ("get", "/rag/sources", None),
    ("post", "/rag/documents", {"text": "x", "source": "y"}),
    ("delete", "/rag/documents/y", None),
    ("post", "/reports", {"type": "it", "period": "Q1 2026", "summary": "s"}),
    ("get", "/session/abc/history", None),
    ("post", "/tickets/sla-breaches/escalate", None),
    ("post", "/ops/digest", None),
])
def test_admin_routes_reject_user_key(client, method, path, body):
    kwargs = {"headers": h(USER)}
    if body is not None:
        kwargs["json"] = body
    assert getattr(client, method)(path, **kwargs).status_code == 403


def test_admin_key_can_update_employee(client):
    r = client.patch("/employees/EMP001", json={"leave_balance": 20}, headers=h(ADMIN))
    assert r.status_code == 200 and r.json()["leave_balance"] == 20


def test_cors_preflight_passes_without_key(client):
    r = client.options("/ask", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "x-api-key,content-type",
    })
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ORIGIN


def test_401_carries_cors_headers(client):
    r = client.get("/tickets", headers={"Origin": ORIGIN})
    assert r.status_code == 401
    assert r.headers.get("access-control-allow-origin") == ORIGIN


def test_unlisted_origin_gets_no_cors_header(client):
    r = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


# ── /ask ────────────────────────────────────────────────────────────────────

def test_ask_returns_answer_and_keeps_history(client, monkeypatch):
    seen = []

    def fake_agent(question, history):
        seen.append(len(history))
        return {"answer": f"echo: {question}", "action_taken": None, "action_result": None, "sources": None}

    monkeypatch.setattr(main, "run_agent", fake_agent)
    r1 = client.post("/ask", json={"question": "  hi  "}, headers=h(USER))
    sid = r1.json()["session_id"]
    r2 = client.post("/ask", json={"question": "again", "session_id": sid}, headers=h(USER))
    assert r1.json()["answer"] == "echo: hi"  # whitespace stripped
    assert r2.status_code == 200 and seen == [0, 2]


def test_ask_error_does_not_leak_internals(client, monkeypatch):
    def boom(question, history):
        raise RuntimeError("Error code: 404 - model llama-3.3 secret-internal-detail")

    monkeypatch.setattr(main, "run_agent", boom)
    r = client.post("/ask", json={"question": "hi"}, headers=h(USER))
    assert r.status_code == 500
    assert "secret-internal-detail" not in r.text
    assert r.json()["detail"]["code"] == "AGENT_ERROR"
    assert r.json()["detail"]["request_id"] == r.headers["X-Request-ID"]


def test_ask_rejects_oversized_question(client):
    assert client.post("/ask", json={"question": "x" * 2001}, headers=h(USER)).status_code == 422


def test_report_trend_sorted_chronologically(client):
    for rid, period in [("R1", "Q4 2025"), ("R2", "Q1 2026"), ("R3", "Q2 2025")]:
        client.post("/reports", json={"type": "it", "period": period, "summary": rid}, headers=h(ADMIN))
    periods = [r["period"] for r in client.get("/reports/it/trend", headers=h(USER)).json()]
    assert periods == ["Q2 2025", "Q4 2025", "Q1 2026"]


# ── Slack ───────────────────────────────────────────────────────────────────

SECRET = "slack-secret"


def _signed(body: dict, retry=None):
    raw = json.dumps(body).encode()
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SECRET.encode(), b"v0:" + ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
    headers = {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig, "Content-Type": "application/json"}
    if retry:
        headers["X-Slack-Retry-Num"] = retry
    return raw, headers


@pytest.fixture
def slack(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, "SLACK_SIGNING_SECRET", SECRET)
    monkeypatch.setattr(main, "handle_slack_event", lambda payload: calls.append(payload["event_id"]))
    monkeypatch.setattr(integrations, "_seen_events", {})
    return calls


def _event(event_id, **event):
    return {"type": "event_callback", "event_id": event_id,
            "event": {"type": "app_mention", "text": "<@U1> hi", "channel": "C1", "user": "U2", **event}}


def test_slack_disabled_without_signing_secret(client, monkeypatch):
    monkeypatch.setattr(main, "SLACK_SIGNING_SECRET", None)
    assert client.post("/slack/events", json={"type": "url_verification"}).status_code == 503


def test_slack_rejects_bad_signature(client, slack):
    raw, headers = _signed({"type": "url_verification", "challenge": "c"})
    headers["X-Slack-Signature"] = "v0=bad"
    assert client.post("/slack/events", content=raw, headers=headers).status_code == 401


def test_slack_rejects_non_numeric_timestamp(client, slack):
    raw, headers = _signed({"type": "url_verification"})
    headers["X-Slack-Request-Timestamp"] = "abc"
    assert client.post("/slack/events", content=raw, headers=headers).status_code == 401


def test_slack_url_verification(client, slack):
    raw, headers = _signed({"type": "url_verification", "challenge": "c123"})
    assert client.post("/slack/events", content=raw, headers=headers).json() == {"challenge": "c123"}


def test_slack_event_handled_once_and_retries_skipped(client, slack):
    raw, headers = _signed(_event("Ev1"))
    assert client.post("/slack/events", content=raw, headers=headers).json() == {"ok": True}
    client.post("/slack/events", content=raw, headers=headers)  # duplicate delivery
    raw_r, headers_r = _signed(_event("Ev1"), retry="1")
    client.post("/slack/events", content=raw_r, headers=headers_r)  # Slack retry
    assert slack == ["Ev1"]


def test_slack_ignores_channel_messages_bots_and_edits(client, slack):
    for i, extra in enumerate([
        {"type": "message", "channel_type": "channel"},
        {"bot_id": "B1"},
        {"type": "message", "channel_type": "im", "subtype": "message_changed"},
    ]):
        raw, headers = _signed(_event(f"Skip{i}", **extra))
        client.post("/slack/events", content=raw, headers=headers)
    raw, headers = _signed(_event("Dm1", type="message", channel_type="im"))
    client.post("/slack/events", content=raw, headers=headers)
    assert slack == ["Dm1"]
