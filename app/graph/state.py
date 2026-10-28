"""Shared graph state and Pydantic schemas (spec section 5)."""
import operator
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field, field_validator


class Project(BaseModel):
    name: str
    summary: str
    tech: list[str] = Field(default_factory=list)


class Profile(BaseModel):
    skills: list[str] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    level: str = ""


class Match(BaseModel):
    job_id: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    fit_note: str = ""


KBName = Literal["personal", "role", "company"]


class PrepPlan(BaseModel):
    intent: Literal["project_questions", "role_questions", "company_prep"]
    mode: Literal["question_set", "mock_interview"]
    role: str
    company: str | None = None
    project: str | None = None
    topics: list[str] = Field(default_factory=list)
    count: int = 10
    kbs: list[KBName] = Field(default_factory=list)
    filters: dict = Field(default_factory=dict)
    confidence: Literal["high", "medium", "low"] = "medium"
    clarification_question: str | None = None

    @field_validator("role", "mode", "count", "confidence", "topics", "kbs", "filters", mode="before")
    @classmethod
    def _null_to_default(cls, v, info):
        """Some models emit null for fields they consider unknown; fall back to the field's default."""
        if v is not None:
            return v
        return {"role": "", "mode": "question_set", "count": 10, "confidence": "low", "topics": [], "kbs": [], "filters": {}}[info.field_name]


class AgentStep(BaseModel):
    step: int
    rationale: str
    tool: str
    args: dict = Field(default_factory=dict)
    observation: str = ""


class RetrievedChunk(BaseModel):
    chunk_id: str
    kb: KBName
    text: str
    header: str = ""
    score: float = 0.0
    metadata: dict = Field(default_factory=dict)


class PrepItem(BaseModel):
    question: str
    key_points: list[str]
    kb: KBName
    citations: list[str]
    topic: str = ""
    difficulty: str = ""
    kind: Literal["text", "mcq"] = "text"
    options: list[str] = Field(default_factory=list)  # mcq only
    correct_index: int | None = None  # mcq only; graded in code, never by the LLM
    explanation: str = ""  # mcq only


class PrepOutput(BaseModel):
    items: list[PrepItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)  # fallbacks, missing data


class GroundingVerdict(BaseModel):
    ok: bool
    issues: list[str] = Field(default_factory=list)


class AnswerScore(BaseModel):
    correctness: int
    depth: int
    clarity: int
    covered_points: list[int] = Field(default_factory=list)  # key point indexes
    missed_points: list[int] = Field(default_factory=list)
    feedback: str
    follow_up_needed: bool = False


class Pointer(BaseModel):
    kb: KBName
    chunk_id: str
    topic: str


class Report(BaseModel):
    overall: float
    weak_areas: list[str] = Field(default_factory=list)
    study_pointers: list[Pointer] = Field(default_factory=list)


class State(TypedDict, total=False):
    user_id: str
    track_id: str
    target_role: str
    resume_id: str  # primary resume used for profile and matching
    github_username: str
    force_reindex: bool
    kb_ready: bool  # set by ingest
    ingest_report: dict  # per-source chunk counts and skips (set by ingest)
    profile: Profile  # set by build_profile
    jobs: list[dict]  # set by search_jobs (the job agent)
    search_trace: list[AgentStep]
    ranked_jobs: list[Match]  # set by rank_jobs
    scored: Annotated[list[Match], operator.add]  # per-job scores merged from the Send fan-out
    tracked_job_ids: list[str]  # jobs already in the tracker
    job_source: str  # live | fallback
    job_notes: list[str]
    selected_job_id: str
    selected_job: dict
    request: str
    clarifications: int  # max 1
    prep_plan: PrepPlan
    plan_confirmed: bool
    chunks: list[RetrievedChunk]
    retrieval_trace: list[AgentStep]
    retrieval_notes: list[str]  # the retrieval agent's notes plus code-added fallback notes
    mode: str  # requested prep mode from the API (question_set | mock_interview)
    expected_fallback: str | None  # shown on plan confirmation when company data is missing
    followups: int  # follow-up questions asked for the current interview question
    next_step: str  # evaluate_answer routing: generate | retrieve | report
    report_id: str
    regenerate: bool  # check_grounding asks generate to try again
    confirm_mode: Literal["clarify", "confirm"] | None  # set by plan_prep for confirm_plan
    clarification_answer: str
    halted: bool  # plan_prep ended the run (unsupported role); output carries the message
    issues: list[str]  # grounding issues fed back to generate on a retry
    follow_up: bool  # generate in follow-up mode (mock interview)
    output: PrepOutput
    current_item: PrepItem
    guard_ok: bool
    answer: str
    scores: list[AnswerScore]
    report: Report
    turn: int
    retries: int  # loop guard for regeneration loops
