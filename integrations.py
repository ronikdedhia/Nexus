import os
import re
import time
import logging
import threading
import requests
from agent import run_agent
from memory import load_history, save_history
from langchain_core.messages import HumanMessage, AIMessage

logger = logging.getLogger("nexus.slack")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN")
SLACK_DIGEST_CHANNEL = os.getenv("SLACK_DIGEST_CHANNEL")

# event_id -> time first seen. Slack can deliver the same event more than once; remember recent IDs.
_seen_events: dict[str, float] = {}
_seen_lock = threading.Lock()
_SEEN_TTL_SECONDS = 600


def post_to_slack(channel: str, text: str) -> dict:
    if not SLACK_BOT_TOKEN:
        return {"status": "skipped", "reason": "SLACK_BOT_TOKEN not configured"}
    resp = requests.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json={"channel": channel, "text": text},
        timeout=10,
    )
    return resp.json()


def _first_time_seen(event_id: str) -> bool:
    now = time.monotonic()
    with _seen_lock:
        for eid, ts in list(_seen_events.items()):
            if now - ts > _SEEN_TTL_SECONDS:
                del _seen_events[eid]
        if event_id in _seen_events:
            return False
        _seen_events[event_id] = now
        return True


def should_process_slack_event(payload: dict) -> bool:
    """True only for events the bot should answer, and only the first time each event arrives.

    Answers @mentions in channels and plain messages in DMs. Ignores bot messages (including its own),
    edits/deletes/joins (any message subtype), and regular channel messages. Without that last rule a
    channel @mention arrives as both app_mention and message and gets two replies."""
    if payload.get("type") != "event_callback":
        return False
    event = payload.get("event") or {}
    if event.get("bot_id") or event.get("subtype"):
        return False
    etype = event.get("type")
    if etype == "message" and event.get("channel_type") != "im":
        return False
    if etype not in ("app_mention", "message"):
        return False
    event_id = payload.get("event_id") or f"{event.get('channel')}:{event.get('ts')}"
    return _first_time_seen(event_id)


def handle_slack_event(payload: dict) -> dict:
    """Run the agent for an accepted Slack event and reply in the same channel.
    Called in the background after main.py has verified the signature and acknowledged Slack."""
    event = payload.get("event") or {}
    text = re.sub(r"<@[A-Z0-9]+>", "", event.get("text", "")).strip()
    channel = event.get("channel")
    user = event.get("user", "unknown")
    if not text or not channel:
        return {"ok": True}
    session_id = f"slack:{channel}:{user}"
    try:
        history = load_history(session_id)
        result = run_agent(text, history)
        save_history(session_id, history + [HumanMessage(content=text), AIMessage(content=result["answer"])])
        post_to_slack(channel, result["answer"])
    except Exception:
        logger.exception("Slack event handling failed")
        post_to_slack(channel, "Sorry, I couldn't answer that just now. Please try again.")
    return {"ok": True}
