"""Node 12: scorecard, weak areas and study pointers; saved to the reports collection. Aggregation is code, no LLM."""
from collections import defaultdict

from langchain_core.runnables import RunnableConfig

from app.db.client import get_db
from app.db.models import utcnow
from app.db.repos import new_id
from app.graph.state import AnswerScore, Pointer, PrepItem, Report, State
from app.rag import stores

WEAK_BELOW = 6.0  # a topic whose mean item score (0-10) is under this is a weak area


def item_score(s: AnswerScore) -> float:
    return (s.correctness + s.depth + s.clarity) / 3


def build_report(items: list[PrepItem], scores: list[AnswerScore], role: str) -> Report:
    pairs = list(zip(items, scores, strict=False))
    if not pairs:
        return Report(overall=0.0)
    overall = round(sum(item_score(s) for _, s in pairs) / len(pairs), 2)
    by_topic: dict[str, list[tuple[PrepItem, AnswerScore]]] = defaultdict(list)
    for it, s in pairs:
        by_topic[it.topic or "general"].append((it, s))
    weak = sorted(
        (t for t, ps in by_topic.items() if sum(item_score(s) for _, s in ps) / len(ps) < WEAK_BELOW),
        key=lambda t: sum(item_score(s) for _, s in by_topic[t]) / len(by_topic[t]),
    )
    pointers: list[Pointer] = []
    seen: set[str] = set()
    for t in weak:
        for it, s in by_topic[t]:
            if item_score(s) >= WEAK_BELOW:
                continue
            for cid in it.citations:  # the user's own chunks and role questions the weak answers were built from
                if cid not in seen:
                    seen.add(cid)
                    pointers.append(Pointer(kb=it.kb, chunk_id=cid, topic=t))
        for row in stores.get_chunks(stores.ROLE_COLLECTION, stores.where_all(role=role, topic=t, source_type="role_resource"))[:2]:
            if row["id"] not in seen:  # curated learning resources for the topic, when the role KB has any
                seen.add(row["id"])
                pointers.append(Pointer(kb="role", chunk_id=row["id"], topic=t))
    return Report(overall=overall, weak_areas=weak, study_pointers=pointers)


async def report(state: State, config: RunnableConfig) -> dict:
    out = state["output"]
    scores = state.get("scores") or []
    answered = out.items[: len(scores)]  # a question that was never answered has no score
    plan = state["prep_plan"]
    rep = build_report(answered, scores, plan.role)
    doc = {
        "_id": new_id(),
        "user_id": state["user_id"],
        "track_id": state["track_id"],
        "thread_id": (config.get("configurable") or {}).get("thread_id"),
        "plan": plan.model_dump(),
        "report": rep.model_dump(),
        "items": [i.model_dump() for i in answered],
        "scores": [s.model_dump() for s in scores],
        "created_at": utcnow(),
    }
    await get_db().reports.insert_one(doc)
    return {"report": rep, "report_id": doc["_id"]}
