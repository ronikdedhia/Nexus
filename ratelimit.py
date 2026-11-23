"""Per-client rate limit for routes that call the LLM (each call costs Groq tokens).

In-memory sliding window, so limits are per process: fine for a single instance. With several
instances, enforce limits at the proxy or move this to Redis."""
import os
import time
import threading
from collections import defaultdict, deque

from fastapi import HTTPException, Request

LLM_RATE_LIMIT_PER_MINUTE = int(os.getenv("LLM_RATE_LIMIT_PER_MINUTE", "10"))
_WINDOW_SECONDS = 60

_hits: dict[str, deque] = defaultdict(deque)
_lock = threading.Lock()


def client_id(request: Request) -> str:
    # Behind Render/Vercel the real client is the first X-Forwarded-For hop.
    forwarded = request.headers.get("X-Forwarded-For", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit_llm_calls(request: Request):
    if LLM_RATE_LIMIT_PER_MINUTE <= 0:
        return
    key = client_id(request)
    now = time.monotonic()
    with _lock:
        window = _hits[key]
        while window and now - window[0] > _WINDOW_SECONDS:
            window.popleft()
        if len(window) >= LLM_RATE_LIMIT_PER_MINUTE:
            retry_after = int(_WINDOW_SECONDS - (now - window[0])) + 1
            raise HTTPException(
                status_code=429,
                detail={"error": f"Too many requests. Try again in {retry_after}s.", "code": "RATE_LIMITED"},
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)
        # Drop idle clients so the dict doesn't grow forever.
        if len(_hits) > 10_000:
            for k in [k for k, v in _hits.items() if not v or now - v[-1] > _WINDOW_SECONDS]:
                del _hits[k]


def reset():
    with _lock:
        _hits.clear()
