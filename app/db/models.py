"""Pydantic shapes for API payloads and MongoDB documents."""
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    email: EmailStr


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class TrackIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    target_role: str = Field(min_length=1, max_length=40)
    github_username: str | None = Field(default=None, max_length=39, pattern=r"^[A-Za-z0-9-]*$")


class TrackPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    target_role: str | None = Field(default=None, min_length=1, max_length=40)
    github_username: str | None = Field(default=None, max_length=39, pattern=r"^[A-Za-z0-9-]*$")


class TrackOut(BaseModel):
    id: str
    name: str
    target_role: str
    github_username: str | None = None
    created_at: datetime


class ResumeOut(BaseModel):
    id: str
    track_id: str
    filename: str
    version: int
    is_primary: bool
    created_at: datetime


class DocumentOut(BaseModel):
    id: str
    track_id: str
    filename: str
    type: Literal["pdf", "md", "txt"]
    size: int
    chunk_count: int
    chunker_version: str | None
    indexed: bool
    created_at: datetime


SessionStatus = Literal["running", "awaiting_input", "done", "expired", "failed"]


class SessionOut(BaseModel):
    thread_id: str
    track_id: str
    flow: Literal["ingest", "discover", "prep"]
    job_id: str | None = None
    status: SessionStatus
    interrupt_type: str | None = None
    error: str | None = None
    interrupt: dict | None = None  # the pending interrupt payload while awaiting_input
    result: dict | None = None  # latest output, trace, notes and report of a prep run
    updated_at: datetime


class PrepIn(BaseModel):
    request: str = Field(default="", max_length=2000)
    job_id: str | None = None
    mode: Literal["question_set", "mock_interview"] | None = None


class ResumeIn(BaseModel):
    """One body for every interrupt type; the required fields depend on the session's pending interrupt."""

    approved: bool | None = None  # confirm_plan
    edits: dict | None = None  # confirm_plan
    text: str | None = Field(default=None, max_length=2000)  # clarification
    answer: str | None = Field(default=None, max_length=8000)  # interview answer


class FeedbackIn(BaseModel):
    thread_id: str
    item_index: int = Field(ge=0)
    score: Literal[0, 1]
    comment: str = Field(default="", max_length=1000)
