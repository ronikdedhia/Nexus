import uuid
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.concurrency import run_in_threadpool
from langchain_core.messages import HumanMessage, AIMessage

from models import AskRequest, AskResponse, ErrorResponse
from agent import run_agent
from memory import load_history, save_history


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
        session_id=session_id,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "service": "nexus"}
