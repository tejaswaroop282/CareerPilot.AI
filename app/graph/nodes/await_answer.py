"""Node 10: pause for the candidate's answer (mock interview only). Interrupt-only: no LLM work, no side effects."""
from langgraph.types import interrupt

from app.graph.state import State


async def await_answer(state: State) -> dict:
    item = state["current_item"]
    plan = state["prep_plan"]
    payload = {
        "type": "answer",
        "turn": state.get("turn", 0) + 1,
        "total": plan.count,
        "question": item.question,
        "kind": item.kind,
        "options": item.options,  # the correct index is never sent to the client
        "kb": item.kb,
        "citations": item.citations,
        "follow_up": bool(state.get("follow_up")),
    }
    reply = interrupt(payload)
    answer = reply.get("answer", "") if isinstance(reply, dict) else reply
    return {"answer": str(answer).strip()}
