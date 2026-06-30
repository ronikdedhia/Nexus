from pydantic import BaseModel, Field
from typing import Optional, Any


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, strip_whitespace=True)
    session_id: Optional[str] = None


class AskResponse(BaseModel):
    answer: str
    action_taken: Optional[str] = None
    action_result: Optional[Any] = None
    session_id: str


class ErrorResponse(BaseModel):
    error: str
    code: str
