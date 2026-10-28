"""Node 4: rank_jobs. Fan out per job with Send (score_job), then merge in rank_jobs.

Each job description is parsed into required and nice-to-have skills with structured output (LLM). The score itself is
computed in code from how many of those skills the candidate's profile and personal KB text evidence, so ranking stays
explainable and measurable. PII and bias signals are stripped from every scoring input.
"""
import hashlib
import logging
import re
from datetime import datetime, timezone

from langgraph.types import Send
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db.client import get_db
from app.graph.helpers.pii import strip_pii
from app.graph.prompts_jobs import PARSE_JOB_SYSTEM
from app.graph.state import Match, Profile, State
from app.rag import stores
from app.rag.llm import get_chat_model

log = logging.getLogger(__name__)
EVIDENCE_CHARS = 60_000
REQUIRED_WEIGHT = 0.75


class JobAnalysis(BaseModel):
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    fit_note: str = ""


_ALIASES = {"js": "javascript", "ts": "typescript", "node": "node.js", "nodejs": "node.js", "k8s": "kubernetes",
            "postgres": "postgresql", "ml": "machine learning", "dsa": "data structures", "oop": "object-oriented programming",
            "oops": "object-oriented programming"}

# Different names for one skill: postings and resumes rarely use the same words.
_SYNONYMS = {
    "llm": ["llms", "large language model", "large language models", "generative ai", "genai", "gen ai", "gpt"],
    "ai agents": ["agentic ai", "agents", "autonomous agents", "ai agent", "multi agent"],
    "nlp": ["natural language processing"],
    "ai": ["artificial intelligence"],
    "prompt engineering": ["prompting", "prompt design"],
    "vector database": ["vector databases", "vector db", "vector search"],
}
_CANON = {v: c for c, vs in _SYNONYMS.items() for v in vs}
# A skill the candidate shows also evidences these related skills named in postings (kept deliberately small, and AI-only:
# generic terms such as "API" appear in almost every posting and stopped separating good fits from bad ones in the eval).
_IMPLIES = {
    "langchain": ["llm", "ai", "ai agents", "prompt engineering"], "langgraph": ["llm", "ai", "ai agents"],
    "rag": ["llm", "ai", "nlp", "vector database"], "hybrid retrieval": ["llm", "ai", "nlp", "vector database"],
    "mcp": ["llm", "ai agents"], "chromadb": ["vector database"], "gemini": ["llm", "ai"], "openai": ["llm", "ai"],
    "groq": ["llm", "ai"],
    "machine learning": ["ai"], "deep learning": ["ai", "machine learning"], "nlp": ["ai"], "llm": ["ai"],
}
# Postings list these, but no resume proves them: counting them only punishes every candidate.
_SOFT = {"communication", "problem solving", "problem-solving", "critical thinking", "teamwork", "collaboration",
         "time management", "leadership", "documentation", "research", "adaptability", "creativity", "curiosity"}


def norm_skill(s: str) -> str:
    s = re.sub(r"[^a-z0-9+#. ]+", " ", s.lower()).strip()
    s = re.sub(r"\s+", " ", s)
    s = _ALIASES.get(s, s)
    return _CANON.get(s, s)


def _is_soft(skill: str) -> bool:
    return norm_skill(skill).replace("-", " ") in {x.replace("-", " ") for x in _SOFT}


def expand_skills(profile_skills: set[str]) -> set[str]:
    out = set(profile_skills)
    for s in profile_skills:
        out.update(_IMPLIES.get(s, []))
    return out


def has_skill(skill: str, profile_skills: set[str], evidence: str) -> bool:
    n = norm_skill(skill)
    if not n:
        return False
    if n in expand_skills(profile_skills):
        return True
    forms = [n] + _SYNONYMS.get(n, [])
    return any(re.search(rf"(?<![a-z0-9]){re.escape(f)}(?![a-z0-9])", evidence) for f in forms)


def score_from_skills(analysis: JobAnalysis, profile_skills: set[str], evidence: str) -> Match:
    req = [s for s in dict.fromkeys(analysis.required_skills) if norm_skill(s) and not _is_soft(s)]
    nice = [s for s in dict.fromkeys(analysis.nice_to_have) if norm_skill(s) and not _is_soft(s)]
    req_hit = [s for s in req if has_skill(s, profile_skills, evidence)]
    nice_hit = [s for s in nice if has_skill(s, profile_skills, evidence)]
    if not req and not nice:
        score = 0.0
    elif not nice:
        score = len(req_hit) / len(req)
    elif not req:
        score = len(nice_hit) / len(nice)
    else:
        score = REQUIRED_WEIGHT * len(req_hit) / len(req) + (1 - REQUIRED_WEIGHT) * len(nice_hit) / len(nice)
    reasons = [f"You show {s}" for s in req_hit[:4]] + [f"Bonus: {s}" for s in nice_hit[:2]]
    return Match(job_id="", score=round(score, 3), reasons=reasons, missing=[s for s in req if s not in req_hit],
                 fit_note=analysis.fit_note)


async def personal_evidence(user_id: str, track_id: str) -> str:
    rows = await stores.get_chunks_async(stores.personal_collection_name(track_id), stores.where_all(user_id=user_id, track_id=track_id))
    return strip_pii("\n".join(r["text"] for r in rows))[:EVIDENCE_CHARS].lower()


async def fan_out(state: State):
    """Conditional edge after search_jobs: one score_job task per job (LangGraph Send), or straight to the merge."""
    jobs = (state.get("jobs") or [])[: get_settings().jobs_score_limit]  # the agent lists the best candidates first
    if not jobs:
        return "rank_jobs"
    profile: Profile | None = state.get("profile")
    skills = sorted({norm_skill(s) for s in (profile.skills if profile else [])})
    evidence = await personal_evidence(state["user_id"], state["track_id"])
    return [Send("score_job", {"job": j, "profile_skills": skills, "evidence": evidence}) for j in jobs]


async def _cached_analysis(key: str) -> JobAnalysis | None:
    doc = await get_db().job_analyses.find_one({"_id": key})
    return JobAnalysis(**doc["analysis"]) if doc else None


async def _store_analysis(key: str, analysis: JobAnalysis) -> None:
    await get_db().job_analyses.replace_one(
        {"_id": key}, {"_id": key, "analysis": analysis.model_dump(), "created_at": datetime.now(timezone.utc)}, upsert=True
    )


async def score_job(payload: dict) -> dict:
    job = payload["job"]
    s = get_settings()
    # no PII or college signals reach the model. The cap is a safety limit only: the job feeds already cut descriptions to
    # 3,000 characters (measured on 499 live postings, none was longer), so it does not change token use today.
    text = strip_pii(f"{job.get('title', '')}\n{job.get('description', '')}")[:3500]
    key = f"{job.get('id', '')}:{hashlib.sha1(text.encode()).hexdigest()[:16]}"  # a changed posting gets a new key
    analysis: JobAnalysis | None = None
    if s.job_analysis_cache:
        try:
            analysis = await _cached_analysis(key)
        except Exception:  # noqa: BLE001 - a cache problem must not stop ranking
            log.warning("job analysis cache read failed", exc_info=True)
    if analysis is None:
        llm = get_chat_model(0, effort=s.llm_effort_extraction or None).with_structured_output(JobAnalysis)
        try:
            analysis = await llm.ainvoke([("system", PARSE_JOB_SYSTEM), ("human", f"<job_posting>\n{text}\n</job_posting>")])
        except Exception:  # noqa: BLE001 - one unparseable job must not sink the whole ranking
            log.warning("could not parse job %s", job.get("id"), exc_info=True)
            return {"scored": []}
        if s.job_analysis_cache:
            try:
                await _store_analysis(key, analysis)
            except Exception:  # noqa: BLE001
                log.warning("job analysis cache write failed", exc_info=True)
    match = score_from_skills(analysis, set(payload["profile_skills"]), payload["evidence"])
    match.job_id = job["id"]
    return {"scored": [match]}


async def rank_jobs(state: State) -> dict:
    s = get_settings()
    ordered = sorted(state.get("scored") or [], key=lambda m: (-m.score, m.job_id))
    ranked = [m for m in ordered if m.score >= s.jobs_min_score][: s.jobs_top_n]
    if len(ranked) < s.jobs_min_shown:  # a student's resume seldom clears the bar on every posting: show the best partial fits
        extra = [m for m in ordered if s.jobs_floor_score <= m.score < s.jobs_min_score]
        ranked += extra[: s.jobs_min_shown - len(ranked)]
    return {"ranked_jobs": ranked}
