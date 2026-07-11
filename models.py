from pydantic import BaseModel, Field
from typing import Optional, Any


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, strip_whitespace=True)
    session_id: Optional[str] = None


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
    leave_balance: Optional[int] = None
    role: Optional[str] = None
    department: Optional[str] = None


class ReportCreate(BaseModel):
    type: str
    period: str
    summary: str


class TicketCommentCreate(BaseModel):
    author: str
    comment: str
