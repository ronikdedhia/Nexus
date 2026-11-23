from typing import Annotated, Optional, Any
from pydantic import BaseModel, Field, StringConstraints

# Upper bounds keep a single request from sending megabytes of text to the LLM or the database.
Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class AskRequest(BaseModel):
    question: Question
    session_id: Optional[str] = Field(None, max_length=128)


class AskResponse(BaseModel):
    answer: str
    action_taken: Optional[str] = None
    action_result: Optional[Any] = None
    sources: Optional[list[str]] = None
    session_id: str


class ErrorResponse(BaseModel):
    error: str
    code: str


class TicketStatusUpdate(BaseModel):
    status: str


class EmployeeUpdate(BaseModel):
    leave_balance: Optional[int] = Field(None, ge=0)
    role: Optional[str] = Field(None, max_length=200)
    department: Optional[str] = Field(None, max_length=200)


class ReportCreate(BaseModel):
    type: str = Field(..., max_length=50)
    period: str = Field(..., max_length=50)
    summary: str = Field(..., max_length=10_000)


class TicketCommentCreate(BaseModel):
    author: str = Field(..., min_length=1, max_length=200)
    comment: str = Field(..., min_length=1, max_length=5_000)


class MeetingTranscript(BaseModel):
    transcript: str = Field(..., min_length=1, max_length=60_000)


class MarketingContentRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=1_000)
    content_format: str = Field("social post", max_length=100)


class RagDocumentCreate(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)
    source: str = Field(..., min_length=1, max_length=200)
