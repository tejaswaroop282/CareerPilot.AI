"""The prep graph: plan_prep, confirm_plan, retrieve, generate, check_grounding, await_answer, evaluate_answer, report.

Routing follows spec 6.1. The MongoDB checkpointer is attached here (the parent graph) so the retrieval agent's
ask_user interrupt inside its subgraph persists too. Each run has its own thread_id.
"""
import logging
import uuid
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from app.errors import friendly_error
from app.db.client import get_db
from app.db.repos import update_session
from app.graph.checkpoint import get_checkpointer
from app.graph.nodes.await_answer import await_answer
from app.graph.nodes.check_grounding import check_grounding, route_after_guard
from app.graph.nodes.confirm_plan import confirm_plan, route_after_confirm
from app.graph.nodes.evaluate_answer import evaluate_answer, route_after_evaluate
from app.graph.nodes.generate import generate
from app.graph.nodes.plan_prep import plan_prep, route_after_plan
from app.graph.nodes.report import report
from app.graph.nodes.retrieve import retrieve
from app.graph.state import State

log = logging.getLogger(__name__)


def build_prep_graph(checkpointer=None):
    g = StateGraph(State)
    for name, fn in [
        ("plan_prep", plan_prep), ("confirm_plan", confirm_plan), ("retrieve", retrieve), ("generate", generate),
        ("check_grounding", check_grounding), ("await_answer", await_answer),
        ("evaluate_answer", evaluate_answer), ("report", report),
    ]:
        g.add_node(name, fn)
    g.add_edge(START, "plan_prep")
    g.add_conditional_edges("plan_prep", route_after_plan, {"confirm_plan": "confirm_plan", "retrieve": "retrieve", "end": END})
    g.add_conditional_edges("confirm_plan", route_after_confirm, {"plan_prep": "plan_prep", "retrieve": "retrieve", "end": END})
    g.add_edge("retrieve", "generate")
    g.add_edge("generate", "check_grounding")
    g.add_conditional_edges("check_grounding", route_after_guard, {"generate": "generate", "await_answer": "await_answer", "report": "report", "end": END})
    g.add_edge("await_answer", "evaluate_answer")
    g.add_conditional_edges("evaluate_answer", route_after_evaluate, {"generate": "generate", "retrieve": "retrieve", "report": "report"})
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)


def _config(thread_id: str, track_id: str) -> dict:
    return {
        "configurable": {"thread_id": thread_id},
        "run_name": "prep",
        "tags": ["flow:prep"],
        "metadata": {"flow": "prep", "track_id": track_id},
    }


def _dump(v: Any) -> Any:
    if hasattr(v, "model_dump"):
        return v.model_dump()
    if isinstance(v, list):
        return [_dump(x) for x in v]
    return v


async def _drive(thread_id: str, user_id: str, track_id: str, graph_input: Any, on_progress=None) -> dict:
    """Run or resume the graph until it finishes or pauses; record the outcome on the session."""
    graph = build_prep_graph(get_checkpointer())
    cfg = _config(thread_id, track_id)
    cfg["run_id"] = run_id = uuid.uuid4()  # lets POST /feedback attach a LangSmith score to this run
    await get_db().sessions.update_one({"thread_id": thread_id, "user_id": user_id}, {"$push": {"run_ids": str(run_id)}})
    try:
        async for chunk in graph.astream(graph_input, cfg, stream_mode="updates"):
            if on_progress:
                for node in chunk:
                    if not node.startswith("__"):
                        await on_progress({"node": node, "event": "done"})
        snap = await graph.aget_state(cfg)
    except Exception as e:
        log.exception("prep run failed")
        await update_session(thread_id, user_id, "failed", error=friendly_error(e))
        raise
    values = snap.values
    result = {
        "output": _dump(values.get("output")),
        "report": _dump(values.get("report")),
        "report_id": values.get("report_id"),
        "plan": _dump(values.get("prep_plan")),
        "trace": _dump(values.get("retrieval_trace") or []),
        "notes": values.get("retrieval_notes") or [],
        "scores": _dump(values.get("scores") or []),
        "chunks": [
            {"chunk_id": c.chunk_id, "kb": c.kb, "snippet": c.text[:240], "header": c.header} for c in values.get("chunks", [])
        ],
    }
    interrupts = [i for t in snap.tasks for i in t.interrupts]
    if interrupts:
        payload = interrupts[0].value
        await get_db().sessions.update_one(
            {"thread_id": thread_id, "user_id": user_id}, {"$set": {"interrupt": payload, "result": result}}
        )
        await update_session(thread_id, user_id, "awaiting_input", interrupt_type=payload.get("type"))
        return {"status": "awaiting_input", "interrupt": payload, **result}
    await get_db().sessions.update_one(
        {"thread_id": thread_id, "user_id": user_id}, {"$set": {"interrupt": None, "result": result}}
    )
    await update_session(thread_id, user_id, "done")
    return {"status": "done", "interrupt": None, **result}


async def run_prep(
    *, user_id: str, track_id: str, thread_id: str, request: str, mode: str | None, selected_job: dict | None = None,
    on_progress=None,
) -> dict:
    db = get_db()
    track = await db.tracks.find_one({"_id": track_id, "user_id": user_id})
    state: State = {
        "user_id": user_id, "track_id": track_id, "target_role": track["target_role"], "request": request,
        "clarifications": 0, "turn": 0, "retries": 0, "scores": [], "followups": 0,
    }
    if mode:
        state["mode"] = mode
    if selected_job:
        state["selected_job"] = selected_job
    return await _drive(thread_id, user_id, track_id, state, on_progress)


async def resume_prep(*, user_id: str, track_id: str, thread_id: str, payload: dict, on_progress=None) -> dict:
    return await _drive(thread_id, user_id, track_id, Command(resume=payload), on_progress)
