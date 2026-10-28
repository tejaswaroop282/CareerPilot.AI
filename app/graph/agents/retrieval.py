"""The retrieval agent (node 7): the shared ReAct loop with three in-process KB search tools (spec 6.3, 8.4).

user_id and track_id are bound here by closure, never model-supplied. The agent chooses KBs, queries and filters;
quotas, dedupe and the fallback note are applied in code afterwards.
"""
import asyncio

from pydantic import Field

from app.config import get_settings
from app.graph.agents.react_loop import LoopResult, ToolArgs, ToolResult, ToolSpec, run_react_agent
from app.graph.helpers.render import snippet
from app.graph.prompts import RETRIEVAL_AGENT_SYSTEM, RETRIEVAL_TASK
from app.graph.state import AgentStep, PrepPlan, RetrievedChunk
from app.rag import manifest, retriever
from app.rag.seeds import slug


class SearchPersonalArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=300)
    project: str | None = Field(default=None, description="exact project name to filter to, if the plan names one")


class SearchRoleArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=300)
    topic: str | None = Field(default=None, description="a topic from the manifest")
    difficulty: str | None = Field(default=None, description="easy, medium or hard")


class SearchCompanyArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=300)
    company: str = Field(description="company key from the manifest")
    round: str | None = Field(default=None, description="e.g. oa, phone, onsite, hr")


class ManifestArgs(ToolArgs):
    pass


class AskUserArgs(ToolArgs):
    question: str = Field(min_length=3, max_length=300)


class FinishArgs(ToolArgs):
    selected_chunk_ids: list[str] = Field(description="chunk ids to keep, best first")
    notes: list[str] = Field(default_factory=list, description="short notes, e.g. no company data found")


def quotas_for(plan: PrepPlan) -> dict[str, int]:
    """Per-KB context quotas for the plan's intent (config.py), limited to the KBs in the plan and scaled to count."""
    s = get_settings()
    base = {
        "company_prep": s.quota_company_prep,
        "role_questions": s.quota_role_questions,
        "project_questions": s.quota_project_questions,
    }[plan.intent]
    quotas = {kb: q for kb, q in base.items() if kb in plan.kbs}
    total = sum(quotas.values())
    if not total:
        return {}
    want = max(plan.count, 1)
    return {kb: max(1, round(q * want / total)) for kb, q in quotas.items()}


def _observation(label: str, chunks: list[RetrievedChunk], hint: str) -> ToolResult:
    if not chunks:
        return ToolResult(observation=f"{label}: no results. {hint}")
    lines = "\n".join(snippet(c) for c in chunks[:6])
    return ToolResult(
        observation=f"{label}: {len(chunks)} results (top shown)\n{lines}",
        artifacts={c.chunk_id: c.model_dump() for c in chunks},
    )


def build_retrieval_tools(*, user_id: str, track_id: str, plan: PrepPlan) -> list[ToolSpec]:
    role = manifest.resolve_role(plan.role) or plan.role
    company = manifest.resolve_company(plan.company) if plan.company else None
    topics_ok = set(manifest.load_manifest()["roles"].get(role, {}).get("topics", []))

    def kb_allowed(kb: str):
        return None if kb in plan.kbs else f"the plan does not use the {kb} knowledge base"

    async def personal(a: SearchPersonalArgs) -> ToolResult:
        found = await retriever.search_personal_async(a.query, user_id=user_id, track_id=track_id, project=a.project)
        return _observation("personal", found, "The track may have no uploaded material; try dropping the project filter.")

    async def role_(a: SearchRoleArgs) -> ToolResult:
        found = await retriever.search_role_async(a.query, role=role, topic=slug(a.topic) if a.topic else None, difficulty=a.difficulty)
        return _observation(f"role/{role}", found, "Try a broader query or drop the topic or difficulty filter.")

    async def company_(a: SearchCompanyArgs) -> ToolResult:
        found = await retriever.search_company_async(
            a.query, company=company or slug(a.company), role=role, round=slug(a.round) if a.round else None
        )
        return _observation(f"company/{company}", found, "There may be no data for this company; say so in the finish notes.")

    def check_role(a: SearchRoleArgs) -> str | None:
        if (p := kb_allowed("role")):
            return p
        if a.topic and slug(a.topic) not in topics_ok:
            return f"unknown topic '{a.topic}' for role {role}. Valid topics: {', '.join(sorted(topics_ok))}"
        if a.difficulty and a.difficulty not in ("easy", "medium", "hard"):
            return "difficulty must be easy, medium or hard"
        return None

    def check_company(a: SearchCompanyArgs) -> str | None:
        if (p := kb_allowed("company")):
            return p
        if company is None:
            return f"there is no company knowledge base data for {plan.company or 'this company'}; finish and say so in notes"
        if manifest.resolve_company(a.company) != company:
            return f"company must be '{company}' (the plan's company)"
        return None

    async def manifest_(_: ManifestArgs) -> ToolResult:
        m = manifest.load_manifest()
        roles = {r: sorted(i.get("topics", [])) for r, i in m["roles"].items()}
        companies = {c: sorted(i.get("roles", {})) for c, i in m["companies"].items()}
        return ToolResult(observation=f"roles and topics: {roles}\ncompanies and roles: {companies}")

    return [
        ToolSpec("search_personal", "Search the user's own resume, projects and notes.", SearchPersonalArgs,
                 run=personal, validate=lambda a: kb_allowed("personal")),
        ToolSpec("search_role", "Search curated role questions with key points.", SearchRoleArgs, run=role_, validate=check_role),
        ToolSpec("search_company", "Search company-tagged interview records.", SearchCompanyArgs, run=company_, validate=check_company),
        ToolSpec("get_kb_manifest", "List supported roles, their topics, and companies.", ManifestArgs, run=manifest_),
        ToolSpec("ask_user", "Ask the user one question when a missing detail blocks you (at most once, alone in its step).",
                 AskUserArgs, kind="ask_user"),
        ToolSpec("finish", "Finish with the chunk ids you selected and short notes.", FinishArgs, kind="finish"),
    ]


def default_query(plan: PrepPlan, request: str) -> str:
    parts = [plan.project or "", " ".join(plan.topics), plan.company or "", plan.role if not plan.topics else ""]
    q = " ".join(p for p in parts if p).strip()
    if not plan.topics and not plan.project and request.strip():
        q = request.strip()[:200]  # no structured focus: the user's own words say more than "<company> <role>"
    return q or plan.role


async def fixed_pipeline(plan: PrepPlan, *, user_id: str, track_id: str, request: str) -> dict[str, list[RetrievedChunk]]:
    """The agent-free baseline and the fallback: one fixed query per KB named in the plan, searched in parallel."""
    q = default_query(plan, request)
    role = manifest.resolve_role(plan.role) or plan.role
    company = manifest.resolve_company(plan.company) if plan.company else None
    jobs: dict[str, asyncio.Future] = {}
    if "personal" in plan.kbs:
        jobs["personal"] = retriever.search_personal_async(q, user_id=user_id, track_id=track_id, project=plan.project)
    if "role" in plan.kbs:
        topic = slug(plan.topics[0]) if len(plan.topics) == 1 else None
        jobs["role"] = retriever.search_role_async(q, role=role, topic=topic)
    if "company" in plan.kbs and company:
        jobs["company"] = retriever.search_company_async(q, company=company, role=role)
    results = await asyncio.gather(*jobs.values())
    return dict(zip(jobs, results, strict=True))


def _finalize(
    plan: PrepPlan, per_kb: dict[str, list[RetrievedChunk]], notes: list[str], user_id: str, track_id: str
) -> tuple[list[RetrievedChunk], list[str]]:
    # defence in depth: personal chunks must belong to this user and track whatever the tools returned
    per_kb["personal"] = [
        c for c in per_kb.get("personal", [])
        if c.metadata.get("user_id") == user_id and c.metadata.get("track_id") == track_id
    ]
    chunks = retriever.select_context(per_kb, quotas_for(plan))
    notes = list(notes)
    if plan.company and not any(c.kb == "company" for c in chunks):  # includes companies dropped from plan.kbs
        notes.append(f"No company-specific data found for {plan.company}; showing role-level preparation instead.")
    for kb in plan.kbs:
        if kb != "company" and not any(c.kb == kb for c in chunks):
            notes.append(f"No {kb} knowledge base content was found for this request.")
    return chunks, notes


async def run_retrieval(
    *, plan: PrepPlan, request: str, user_id: str, track_id: str, config: dict | None = None, llm=None
) -> tuple[list[RetrievedChunk], list[AgentStep], list[str]]:
    """Run the agent; on cap, error or a missing finish run the fixed pipeline and record that in the trace."""
    steps = get_settings().agent_max_steps_retrieval
    task = RETRIEVAL_TASK.format(
        intent=plan.intent, mode=plan.mode, role=plan.role, company=plan.company or "none", project=plan.project or "none",
        topics=", ".join(plan.topics) or "any", kbs=", ".join(plan.kbs), count=plan.count, request=request[:500],
        want=max(plan.count, 6),
    )
    result: LoopResult = await run_react_agent(
        system_prompt=RETRIEVAL_AGENT_SYSTEM.format(max_steps=steps), task=task,
        tools=build_retrieval_tools(user_id=user_id, track_id=track_id, plan=plan),
        max_steps=steps, config=config, llm=llm,
    )
    trace = list(result.trace)
    pool = {cid: RetrievedChunk(**c) for cid, c in result.artifacts.items()}

    if result.status == "finished" or (result.status in ("no_progress", "cap") and pool):
        # no_progress and cap keep what was gathered; finished uses the model's selection (valid ids only)
        picked = [i for i in result.finish_args.get("selected_chunk_ids", []) if i in pool] if result.status == "finished" else []
        ordered = [pool[i] for i in picked] or sorted(pool.values(), key=lambda c: -c.score)
        per_kb: dict[str, list[RetrievedChunk]] = {}
        for c in ordered:
            per_kb.setdefault(c.kb, []).append(c)
        stopped = {"no_progress": "Agent stopped repeating itself", "cap": "Agent reached its step limit"}
        notes = [str(n) for n in result.finish_args.get("notes", [])] if result.status == "finished" else [f"{stopped[result.status]}; used what it had gathered."]
        chunks, notes = _finalize(plan, per_kb, notes, user_id, track_id)
        return chunks, trace, notes

    reason = f"agent ended with status '{result.status}'"
    per_kb = await fixed_pipeline(plan, user_id=user_id, track_id=track_id, request=request)
    trace.append(AgentStep(step=len(trace) + 1, rationale=f"Fallback: {reason}", tool="fixed_pipeline", args={"kbs": plan.kbs},
                           observation=", ".join(f"{k}: {len(v)}" for k, v in per_kb.items())))
    chunks, notes = _finalize(plan, per_kb, ["Used the fixed retrieval pipeline because the retrieval agent did not finish."], user_id, track_id)
    return chunks, trace, notes
