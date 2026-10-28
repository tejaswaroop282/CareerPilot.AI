"""Node 6: interrupt-only. Clarify mode asks the one clarification question; confirm mode shows the plan for approval.

No LLM work here: on resume LangGraph restarts the node from its beginning, so anything before `interrupt` must be
cheap and deterministic. User edits are applied and validated against the manifest in code.
"""
from langgraph.types import interrupt

from app.graph.nodes.plan_prep import REQUIRED_KBS, expected_fallback, normalize_plan
from app.graph.state import PrepOutput, PrepPlan, State
from app.rag import manifest

EDITABLE = {"role", "company", "project", "topics", "count", "mode", "kbs"}


def apply_edits(plan: PrepPlan, edits: dict) -> tuple[PrepPlan, list[str]]:
    """Apply user edits; anything invalid is ignored and reported back, never trusted."""
    data = plan.model_dump()
    problems: list[str] = []
    for key, value in (edits or {}).items():
        if key not in EDITABLE:
            problems.append(f"'{key}' cannot be edited")
            continue
        if key == "role":
            resolved = manifest.resolve_role(str(value))
            if resolved:
                data["role"] = resolved
            else:
                problems.append(f"role '{value}' is not supported (supported: {', '.join(manifest.supported_roles())})")
        elif key == "company":
            if not value:
                data["company"] = None
            elif manifest.resolve_company(str(value)):
                data["company"] = manifest.resolve_company(str(value))
            else:
                problems.append(f"company '{value}' has no data (available: {', '.join(manifest.supported_companies()) or 'none'})")
        elif key == "count":
            try:
                data["count"] = min(max(int(value), 1), 20)
            except (TypeError, ValueError):
                problems.append("count must be a number")
        elif key == "mode":
            if value in ("question_set", "mock_interview"):
                data["mode"] = value
            else:
                problems.append("mode must be question_set or mock_interview")
        elif key == "kbs":
            allowed = {"personal", "role", "company"}
            kbs = [k for k in (value or []) if k in allowed]
            if kbs:
                data["kbs"] = kbs
            else:
                problems.append("kbs must include at least one of personal, role, company")
        elif key == "topics":
            data["topics"] = [str(t).strip() for t in (value or []) if str(t).strip()][:8]
        elif key == "project":
            data["project"] = str(value).strip() or None
    new = PrepPlan(**data)
    if "kbs" not in (edits or {}):  # re-derive kbs when the role or company changed
        new, _ = normalize_plan(new, mode=None, target_role=new.role, projects=[new.project] if new.project else [])
    elif "company" in new.kbs and not manifest.resolve_company(new.company):
        new.kbs = [k for k in new.kbs if k != "company"]
        problems.append("company knowledge base removed: no company data for this plan")
    if "kbs" in (edits or {}):  # the request needs these (company aside): a plan without them has little to ground questions on
        for kb in REQUIRED_KBS[new.intent]:
            if kb != "company" and kb not in new.kbs:
                new.kbs.append(kb)
                problems.append(f"{kb} knowledge base kept: this kind of request needs it")
    if not new.kbs:
        new.kbs = list(REQUIRED_KBS[new.intent])
    return new, problems


async def confirm_plan(state: State) -> dict:
    plan: PrepPlan = state["prep_plan"]
    if state.get("confirm_mode") == "clarify":
        answer = interrupt({"type": "clarification", "source": "plan", "question": plan.clarification_question})
        text = (answer.get("text", "") if isinstance(answer, dict) else str(answer)).strip()
        return {
            "request": f"{state.get('request', '')}\nClarification: {text}".strip(),
            "clarification_answer": text,
            "clarifications": state.get("clarifications", 0) + 1,
            "confirm_mode": None,
        }

    facts = {"company_missing": bool(state.get("expected_fallback"))}
    payload = {
        "type": "confirm_plan",
        "plan": plan.model_dump(),
        "expected_fallback": expected_fallback(plan, facts) or state.get("expected_fallback"),
        "supported_roles": manifest.supported_roles(),
        "supported_companies": manifest.supported_companies(plan.role),
    }
    answer = interrupt(payload)
    approved = bool(answer.get("approved")) if isinstance(answer, dict) else False
    if not approved:
        return {"plan_confirmed": False, "halted": True, "confirm_mode": None,
                "output": PrepOutput(notes=["Plan cancelled."])}
    new, problems = apply_edits(plan, (answer or {}).get("edits") or {})
    if manifest.resolve_role(new.role) is None:
        roles = ", ".join(manifest.supported_roles()) or "none yet"
        return {"plan_confirmed": False, "halted": True, "confirm_mode": None, "prep_plan": new,
                "output": PrepOutput(notes=[f"This role is not supported yet. Supported roles: {roles}. Please choose one."])}
    update: dict = {"prep_plan": new, "plan_confirmed": True, "confirm_mode": None, "expected_fallback": None}
    if problems:
        update["retrieval_notes"] = [f"Ignored edit: {p}" for p in problems]
    return update


def route_after_confirm(state: State) -> str:
    if state.get("halted"):
        return "end"
    if state.get("confirm_mode") is None and state.get("plan_confirmed"):
        return "retrieve"
    return "plan_prep"  # clarify mode: the answer goes back to plan_prep
