import os
from langchain_groq import ChatGroq

# Groq retired llama-3.3-70b-versatile on 2026-08-16; openai/gpt-oss-120b is its recommended replacement.
# Override with GROQ_MODEL when Groq deprecates this one too (see https://console.groq.com/docs/deprecations).
DEFAULT_MODEL = "openai/gpt-oss-120b"

_clients: dict[float, ChatGroq] = {}


def model_name() -> str:
    return os.getenv("GROQ_MODEL") or DEFAULT_MODEL


def get_llm(temperature: float = 0.0) -> ChatGroq:
    """One cached client per temperature, so callers asking for different temperatures each get theirs."""
    if temperature not in _clients:
        _clients[temperature] = ChatGroq(
            model=model_name(),
            temperature=temperature,
            api_key=os.getenv("GROQ_API_KEY"),
        )
    return _clients[temperature]
