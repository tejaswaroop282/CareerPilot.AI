"""Seed schemas for the role and company knowledge bases, plus validation (spec 8.5)."""
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.rag.chunking.base import content_hash

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class RoleQuestion(BaseModel):
    id: str
    role: str
    topic: str
    difficulty: Literal["easy", "medium", "hard"]
    type: Literal["question", "mcq"] = "question"
    question: str = Field(min_length=5)
    key_points: list[str] = Field(min_length=1)
    key_points_source: Literal["source", "generated"] = "source"  # generated = written by a model, not from the source
    options: list[str] = Field(default_factory=list)  # mcq only
    correct_index: int | None = None  # mcq only
    explanation: str = ""  # mcq only: shown after grading
    pitfall: str = ""  # common wrong answer, used to sharpen feedback
    reference: str = ""  # PRIVATE reference answer (copyrighted source): used only to grade answers, never shown or quoted
    subtopic: str = ""  # section the question belongs to, e.g. osi-model
    practice_url: str = ""  # optional plain link, never required
    source: str = Field(min_length=2)

    @model_validator(mode="after")
    def _mcq(self) -> "RoleQuestion":
        if self.type == "mcq":
            if not 2 <= len(self.options) <= 6 or any(not o.strip() for o in self.options):
                raise ValueError("mcq needs 2-6 non-empty options")
            if len({o.strip().lower() for o in self.options}) != len(self.options):
                raise ValueError("mcq options must be distinct")
            if self.correct_index is None or not 0 <= self.correct_index < len(self.options):
                raise ValueError("mcq correct_index must point at an option")
            if not self.explanation.strip():
                raise ValueError("mcq needs an explanation")
        elif self.options or self.correct_index is not None:
            raise ValueError("options/correct_index are only valid for type mcq")
        return self

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError("id must be lowercase letters, digits, - or _")
        return v


class RoleResource(BaseModel):
    id: str
    role: str
    topic: str
    type: Literal["resource"] = "resource"
    title: str = Field(min_length=2)
    url: str = ""
    summary: str = Field(min_length=20)  # short summary in your own words
    source: str = Field(min_length=2)
    license: str = Field(min_length=2)


class RoleSeed(BaseModel):
    role: str
    aliases: list[str] = Field(default_factory=list)
    questions: list[RoleQuestion] = Field(default_factory=list)
    resources: list[RoleResource] = Field(default_factory=list)


class CompanyRecord(BaseModel):
    id: str
    company: str
    role: str
    round: str = ""
    topic: str = ""
    type: Literal["question", "experience", "pattern"]
    text: str = Field(min_length=4)
    key_points: list[str] = Field(default_factory=list)  # required for questions
    key_points_source: Literal["source", "generated"] = "source"  # generated = written by a model, shown as such
    difficulty: Literal["easy", "medium", "hard"] | None = None
    practice_url: str = ""
    source: str = Field(min_length=2)
    license: str = Field(min_length=2)
    confidence: Literal["high", "medium", "low"]
    year: int = Field(ge=2000, le=2100)

    @model_validator(mode="after")
    def _question_has_key_points(self) -> "CompanyRecord":
        if self.type == "question" and not any(k.strip() for k in self.key_points):
            raise ValueError("company questions need key_points (approach, complexity, edge cases)")
        return self


class CompanySeed(BaseModel):
    company: str
    aliases: list[str] = Field(default_factory=list)
    records: list[CompanyRecord] = Field(default_factory=list)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_role_seed(data: dict) -> tuple[RoleSeed | None, list[str]]:
    return _validate(RoleSeed, data, _role_checks)


def validate_company_seed(data: dict) -> tuple[CompanySeed | None, list[str]]:
    return _validate(CompanySeed, data, _company_checks)


def _validate(model, data: dict, checks) -> tuple[object | None, list[str]]:
    try:
        seed = model.model_validate(data)
    except ValidationError as e:
        return None, [f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()]
    return seed, checks(seed)


def _dupes(items: list[tuple[str, str]]) -> list[str]:
    """items = (id, text). Report duplicate ids and duplicate/near-duplicate text."""
    errors: list[str] = []
    seen_id: dict[str, int] = defaultdict(int)
    seen_hash: dict[str, str] = {}
    for rid, text in items:
        seen_id[rid] += 1
        h = content_hash(text)
        if h in seen_hash and seen_hash[h] != rid:
            errors.append(f"duplicate content: {rid} and {seen_hash[h]}")
        seen_hash.setdefault(h, rid)
    errors += [f"duplicate id: {i}" for i, n in seen_id.items() if n > 1]
    return errors


def _role_checks(seed: RoleSeed) -> list[str]:
    errors = [f"{q.id}: role '{q.role}' != file role '{seed.role}'" for q in seed.questions if q.role != seed.role]
    errors += [f"{r.id}: role '{r.role}' != file role '{seed.role}'" for r in seed.resources if r.role != seed.role]
    errors += [f"{q.id}: empty key point" for q in seed.questions if any(not k.strip() for k in q.key_points)]
    errors += _dupes([(q.id, q.question) for q in seed.questions] + [(r.id, r.summary) for r in seed.resources])
    return errors


def _company_checks(seed: CompanySeed) -> list[str]:
    errors = [f"{r.id}: company '{r.company}' != file company '{seed.company}'" for r in seed.records if r.company != seed.company]
    errors += _dupes([(r.id, r.text) for r in seed.records])
    return errors
