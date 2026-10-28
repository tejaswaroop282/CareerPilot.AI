"""Node 5: turn the request (or the selected job) into a PrepPlan, then validate it against the KB manifest in code."""
from app.db.client import get_db
from app.graph.prompts import PLAN_PREP_SYSTEM, PLAN_PREP_USER
from app.graph.state import PrepOutput, PrepPlan, State
from app.mcp_client import McpUnavailable, call_tool
from app.rag import manifest
from app.rag.llm import get_chat_model
from app.rag.seeds import slug

REQUIRED_KBS = {
    "project_questions": ["personal", "role"],
    "role_questions": ["role"],
    "company_prep": ["company", "role", "personal"],
}


def normalize_plan(plan: PrepPlan, *, mode: str | None, target_role: str, projects: list[str]) -> tuple[PrepPlan, dict]:
    """Code-side validation and normalisation. Returns the plan and facts used for routing:
    role_valid, company_missing (named but not in the manifest for this role)."""
    m = manifest.load_manifest()
    role_key = manifest.resolve_role(plan.role) or manifest.resolve_role(target_role if not plan.role else None)
    role_valid = role_key is not None
    if role_key:
        plan.role = role_key
    company_key = manifest.resolve_company(plan.company) if plan.company else None
    company_missing = bool(plan.company) and (
        company_key is None or (role_key is not None and role_key not in m["companies"][company_key].get("roles", {}))
    )
    if company_key and not company_missing:
        plan.company = company_key
    if mode:
        plan.mode = mode  # an explicit API choice wins over the model's guess
    plan.count = min(max(plan.count, 1), 20)
    plan.topics = [t.strip() for t in plan.topics if t.strip()][:8]
    if plan.project:  # match the exact project name from the profile when it is a loose mention
        low = plan.project.lower()
        plan.project = next((p for p in projects if p.lower() == low or low in p.lower() or p.lower() in low), plan.project)
    # kbs follow the intent (spec 8.2); company is dropped when there is no company data to search
    kbs = list(REQUIRED_KBS[plan.intent])
    if "company" in kbs and (plan.company is None or company_missing):
        kbs.remove("company")
    plan.kbs = kbs
    return plan, {"role_valid": role_valid, "company_missing": company_missing}


def expected_fallback(plan: PrepPlan, facts: dict) -> str | None:
    if facts["company_missing"]:
        return f"There is no company-specific data for {plan.company}; you will get role-level preparation, labelled as such."
    return None


async def make_plan(
    *, request: str, target_role: str, profile: dict, mode: str | None, job: dict | None, clarifications: int
) -> dict:
    """LLM planning plus code validation and routing facts. Shared by the plan_prep node and the plan-accuracy eval."""
    projects = [p.get("name", "") for p in profile.get("projects", [])]
    m = manifest.load_manifest()
    try:  # spec 5: roles and companies are validated against the MCP list tools (same manifest the server reads)
        mcp_roles = await call_tool("list_supported_roles", {})
        mcp_companies = await call_tool("list_supported_companies", {})
        m = {"roles": {r: m["roles"].get(r, {}) for r in mcp_roles},
             "companies": {c: m["companies"].get(c, {}) for c in mcp_companies}}
    except McpUnavailable:
        pass  # the local manifest is the same data; plan validation below never depends on the server being up
    job_block = ""
    if job:
        job_block = (
            '<selected_job untrusted="true">\n'
            f"title: {job.get('title', '')}\ncompany: {job.get('company', '')}\n"
            f"description: {str(job.get('description', ''))[:1500]}\n</selected_job>\n"
        )
    user = PLAN_PREP_USER.format(
        roles={r: i.get("topics", []) for r, i in m["roles"].items()},
        companies={c: sorted(i.get("roles", {})) for c, i in m["companies"].items()},
        target_role=target_role,
        skills=", ".join(profile.get("skills", [])[:25]) or "unknown",
        projects=", ".join(projects) or "none",
        level=profile.get("level") or "unknown",
        job_block=job_block,
        mode=mode or "any",
        request=request,
    )
    llm = get_chat_model().with_structured_output(PrepPlan)
    plan: PrepPlan = await llm.ainvoke([("system", PLAN_PREP_SYSTEM), ("human", user)])
    plan, facts = normalize_plan(plan, mode=mode, target_role=target_role, projects=projects)

    update: dict = {"prep_plan": plan, "plan_confirmed": False, "halted": False, "confirm_mode": None}
    if plan.clarification_question and clarifications == 0:
        update["confirm_mode"] = "clarify"
    elif not facts["role_valid"] and clarifications >= 1:
        roles = ", ".join(manifest.supported_roles()) or "none yet"
        update["halted"] = True
        update["output"] = PrepOutput(notes=[f"This role is not supported yet. Supported roles: {roles}. Please choose one."])
    elif not facts["role_valid"] or facts["company_missing"] or plan.confidence == "low":
        update["confirm_mode"] = "confirm"
    update["expected_fallback"] = expected_fallback(plan, facts)
    return update


async def plan_prep(state: State) -> dict:
    track = await get_db().tracks.find_one({"_id": state["track_id"], "user_id": state["user_id"]}) or {}
    return await make_plan(
        request=state.get("request", ""),
        target_role=state.get("target_role") or track.get("target_role", ""),
        profile=track.get("profile") or {},
        mode=state.get("mode"),
        job=state.get("selected_job"),
        clarifications=state.get("clarifications", 0),
    )


def route_after_plan(state: State) -> str:
    if state.get("halted"):
        return "end"
    return "confirm_plan" if state.get("confirm_mode") else "retrieve"
