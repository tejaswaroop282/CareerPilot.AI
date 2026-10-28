"""Node 7: the retrieval ReAct agent (see graph/agents/retrieval.py). The loop is a subgraph; the parent's config is
passed through so an ask_user interrupt inside it persists on the parent's checkpointer."""
from langchain_core.runnables import RunnableConfig

from app.graph.agents.retrieval import run_retrieval
from app.graph.nodes.evaluate_answer import topic_of_turn
from app.graph.state import State
from app.observability import annotate


async def retrieve(state: State, config: RunnableConfig) -> dict:
    prior = [n for n in state.get("retrieval_notes") or [] if n.startswith("Ignored edit")]  # from confirm_plan
    plan = state["prep_plan"]
    if plan.mode == "mock_interview" and len(plan.topics) > 1:  # each interview turn retrieves for its own topic
        plan = plan.model_copy(update={"topics": [topic_of_turn(plan, state.get("turn", 0))]})
    chunks, trace, notes = await run_retrieval(
        plan=plan, request=state.get("request", ""), user_id=state["user_id"],
        track_id=state["track_id"], config=config,
    )
    merged = list(dict.fromkeys(prior + notes))
    annotate(
        retrieved=[{"id": c.chunk_id, "kb": c.kb, "score": round(float(c.score), 3)} for c in chunks],
        agent_steps=len(trace), plan_kbs=plan.kbs, plan_topics=plan.topics,
    )
    return {
        "chunks": chunks, "retrieval_trace": trace, "retrieval_notes": merged,
        "retries": 0, "issues": [], "follow_up": False, "regenerate": False,
    }
