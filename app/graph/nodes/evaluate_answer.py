"""Node 11: score the answer against the current item's key points; feedback stays tied to key point indexes.

Multiple-choice items are graded in code (the LLM never decides correctness of an option). Free-text answers go to the
LLM with structured output, then the indexes are validated and made consistent in code.
"""
import asyncio
import json

from app.graph.prompts import EVALUATE_SYSTEM, EVALUATE_USER
from app.graph.state import AnswerScore, PrepItem, State
from app.rag import stores
from app.rag.llm import get_chat_model

MAX_FOLLOWUPS_PER_QUESTION = 1


def grade_mcq(item: PrepItem, answer: str) -> AnswerScore:
    """Deterministic: the answer is an option index (0-based) or the option's text."""
    chosen: int | None = None
    a = answer.strip()
    if a.isdigit() and int(a) < len(item.options):
        chosen = int(a)
    else:
        low = [o.strip().lower() for o in item.options]
        if a.lower() in low:
            chosen = low.index(a.lower())
    ok = chosen is not None and chosen == item.correct_index
    all_points = list(range(len(item.key_points)))
    right = item.options[item.correct_index] if item.correct_index is not None else ""
    feedback = ("Correct. " if ok else f"Not quite. The correct answer is: {right}. ") + item.explanation
    score = 10 if ok else 0
    return AnswerScore(
        correctness=score, depth=score, clarity=score, covered_points=all_points if ok else [],
        missed_points=[] if ok else all_points, feedback=feedback.strip(), follow_up_needed=False,
    )


def clean_score(score: AnswerScore, n_points: int) -> AnswerScore:
    """Make the model's output consistent: clamp scores, keep valid unique indexes, missed = everything not covered."""
    clamp = lambda v: max(0, min(10, int(v)))  # noqa: E731
    covered = sorted({i for i in score.covered_points if 0 <= i < n_points})
    missed = [i for i in range(n_points) if i not in covered]
    return AnswerScore(
        correctness=clamp(score.correctness), depth=clamp(score.depth), clarity=clamp(score.clarity),
        covered_points=covered, missed_points=missed, feedback=score.feedback.strip(),
        follow_up_needed=score.follow_up_needed and bool(missed),
    )


def _load_reference(item: PrepItem) -> str:
    """Private reference answer of the role question this item was built from (never sent to the client)."""
    if item.kb != "role" or len(item.citations) != 1:
        return ""
    for row in stores.get_by_ids(stores.ROLE_COLLECTION, item.citations):
        try:
            return json.loads(row["metadata"].get("record", "{}")).get("reference", "") or ""
        except ValueError:
            return ""
    return ""


def topic_of_turn(plan, turn: int) -> str | None:
    return plan.topics[turn % len(plan.topics)] if plan.topics else None


async def evaluate_answer(state: State) -> dict:
    item: PrepItem = state["current_item"]
    plan = state["prep_plan"]
    answer = state.get("answer", "")
    if item.kind == "mcq":
        score = grade_mcq(item, answer)
    else:
        points = "\n".join(f"{i}. {k}" for i, k in enumerate(item.key_points))
        reference = await asyncio.to_thread(_load_reference, item)
        reference_block = (
            f"\n<private_reference_answer>\n{reference[:3500]}\n</private_reference_answer>\n" if reference else "\n"
        )
        llm = get_chat_model(0).with_structured_output(AnswerScore)
        raw: AnswerScore = await llm.ainvoke(
            [("system", EVALUATE_SYSTEM), ("human", EVALUATE_USER.format(
                question=item.question, points=points, reference_block=reference_block, answer=answer[:4000]))]
        )
        score = clean_score(raw, len(item.key_points))

    scores = list(state.get("scores") or []) + [score]  # scores[i] belongs to output.items[i]
    turn = state.get("turn", 0)
    followups = state.get("followups", 0)
    if score.follow_up_needed and followups < MAX_FOLLOWUPS_PER_QUESTION:
        return {"scores": scores, "followups": followups + 1, "follow_up": True, "next_step": "generate", "answer": ""}
    turn += 1
    if turn >= plan.count:
        return {"scores": scores, "turn": turn, "followups": 0, "follow_up": False, "next_step": "report", "answer": ""}
    changed = len(plan.topics) > 1 and topic_of_turn(plan, turn) != topic_of_turn(plan, turn - 1)
    return {"scores": scores, "turn": turn, "followups": 0, "follow_up": False,
            "next_step": "retrieve" if changed else "generate", "answer": ""}


def route_after_evaluate(state: State) -> str:
    return state["next_step"]
