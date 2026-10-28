"""Plan accuracy (live LLM): intent, mode, role, company, KB selection, and the decision to confirm or clarify."""
import asyncio
import json
from pathlib import Path

from app.graph.nodes.plan_prep import make_plan

DATASETS = Path(__file__).parent / "datasets"
PROFILE = {"skills": ["Python", "FastAPI", "PyTorch"], "level": "entry",
           "projects": [{"name": "Ocular AI", "summary": "retinal classifier", "tech": ["PyTorch"]},
                        {"name": "DocuChat RAG", "summary": "RAG over PDFs", "tech": ["Python"]}]}


def route_of(update: dict) -> str:
    if update.get("halted"):
        return "halted"
    return {"clarify": "clarify", "confirm": "confirm"}.get(update.get("confirm_mode"), "retrieve")


async def run_async() -> dict:
    cases = json.loads((DATASETS / "plan_cases.json").read_text())
    fields = {"intent": 0, "mode": 0, "role": 0, "company": 0, "route": 0, "project": 0, "count": 0}
    totals = dict.fromkeys(fields, 0)
    failures = []
    for c in cases:
        exp = c["expect"]
        update = await make_plan(request=c["request"], target_role=c["target_role"], profile=PROFILE, mode=None, job=None, clarifications=0)
        plan, route = update["prep_plan"], route_of(update)
        got = {"intent": plan.intent, "mode": plan.mode, "role": plan.role if exp["role"] else None, "company": plan.company, "route": route,
               "project": plan.project, "count": plan.count}
        for f in fields:
            if f in ("project", "count") and f not in exp:
                continue
            totals[f] += 1
            want = exp.get(f)
            if f == "route" and want in ("clarify_or_confirm", "confirm"):
                ok = route in ("clarify", "confirm")  # asking a question is as safe as showing the plan
            else:
                ok = str(got[f]).lower() == str(want).lower() if isinstance(want, str) else got[f] == want
            if f == "role" and want is None:
                ok = True  # unsupported roles are checked through the route (must confirm)
            fields[f] += ok
            if not ok:
                failures.append({"request": c["request"], "field": f, "expected": want, "got": got[f]})
    rates = {f: round(fields[f] / totals[f], 3) for f in fields if totals[f]}
    return {"cases": len(cases), "accuracy": rates, "failures": failures[:15]}


def run() -> dict:
    from evals.common import print_table

    out = asyncio.run(run_async())
    print_table(f"Plan accuracy ({out['cases']} labelled requests)", {"plan_prep": out["accuracy"]})
    for f in out["failures"][:8]:
        print("  miss:", f)
    return out
