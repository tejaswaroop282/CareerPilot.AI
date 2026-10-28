"""Node 9: guardrail. Deterministic checks first, then an LLM judge that each question and key point is supported
by its cited chunks. A failure sends the graph back to generate (max 2 retries)."""
from app.graph.helpers.render import KB_LABEL, chunk_body, record_of
from app.graph.nodes.generate import seed_fallback_items
from app.graph.prompts import GROUNDING_SYSTEM
from app.graph.state import GroundingVerdict, PrepItem, PrepOutput, State
from app.observability import annotate, feedback
from app.rag.llm import get_chat_model

MAX_RETRIES = 2


def deterministic_issues(items: list[PrepItem], chunks_by_id: dict) -> dict[int, list[str]]:
    """Item number (1-based) -> problems: citations present, cited ids exist, KB label matches a cited chunk."""
    bad: dict[int, list[str]] = {}
    for n, it in enumerate(items, 1):
        probs: list[str] = []
        if not it.citations:
            probs.append(f"item {n} has no citations")
        missing = [c for c in it.citations if c not in chunks_by_id]
        if missing:
            probs.append(f"item {n} cites ids that were not retrieved: {', '.join(missing)}")
        elif it.citations and not any(chunks_by_id[c].kb == it.kb for c in it.citations):
            probs.append(f"item {n} is labelled {KB_LABEL[it.kb]} but cites none of that knowledge base")
        if probs:
            bad[n] = probs
    return bad


def is_verbatim_seed(item: PrepItem, chunks_by_id: dict) -> bool:
    """A curated record reproduced word for word (one citation, same question and key points): nothing the model wrote,
    so there is nothing to hallucinate and no judge call is needed."""
    if len(item.citations) != 1 or item.citations[0] not in chunks_by_id:
        return False
    chunk = chunks_by_id[item.citations[0]]
    rec = record_of(chunk)
    text = rec.get("question") or rec.get("text")
    if chunk.kb not in ("role", "company") or not rec or not text or not rec.get("key_points"):
        return False
    same = lambda a, b: [str(x).strip() for x in a] == [str(x).strip() for x in b]  # noqa: E731
    return item.question.strip() == str(text).strip() and same(item.key_points, rec["key_points"])


def _judge_input(items: list[PrepItem], chunks_by_id: dict, numbers: list[int] | None = None) -> str:
    blocks = []
    for n, it in zip(numbers or range(1, len(items) + 1), items, strict=True):
        cited = "\n".join(f"[{c}] {chunk_body(chunks_by_id[c])}" for c in it.citations if c in chunks_by_id)
        points = "\n".join(f"- {k}" for k in it.key_points)
        blocks.append(f"Item {n}: {it.question}\nKey points:\n{points}\nCited chunks:\n{cited}")
    return "\n\n".join(blocks)


async def check_grounding(state: State) -> dict:
    plan = state["prep_plan"]
    out: PrepOutput = state["output"]
    mock = plan.mode == "mock_interview"
    cur = state.get("current_item")
    items = [cur] if mock and cur else list(out.items)
    chunks_by_id = {c.chunk_id: c for c in state.get("chunks", [])}
    retries = state.get("retries", 0)

    issues: list[str] = []
    bad = deterministic_issues(items, chunks_by_id)
    for probs in bad.values():
        issues.extend(probs)
    if not items:
        issues.append("no items were generated")
    if not issues:  # the LLM judge only runs once the cheap checks pass, and only on text the model wrote
        pending = [(n, it) for n, it in enumerate(items, 1) if not is_verbatim_seed(it, chunks_by_id)]
        annotate(judge_items=len(pending), judge_skipped_verbatim=len(items) - len(pending))
        if pending:
            verdict: GroundingVerdict = await get_chat_model(0).with_structured_output(GroundingVerdict).ainvoke(
                [("system", GROUNDING_SYSTEM), ("human", _judge_input([it for _, it in pending], chunks_by_id, [n for n, _ in pending]))]
            )
            if not verdict.ok:
                issues.extend(verdict.issues or ["an item is not supported by its citations"])

    valid = 1.0 - len(bad) / len(items) if items else 0.0  # share of items whose citations check out
    await feedback("citation_valid", valid)  # LangSmith feedback on the live trace (no-op when tracing is off)
    await feedback("grounded", 0.0 if issues else 1.0, "; ".join(issues)[:300] or None)
    await feedback("grounding_retries", float(retries))
    if not issues:
        return {"guard_ok": True, "issues": [], "regenerate": False}
    if retries < MAX_RETRIES:
        return {"guard_ok": False, "issues": issues, "retries": retries + 1, "regenerate": True}

    # retries exhausted: keep only items that pass the deterministic checks and say the rest was not verified
    keep = [it for n, it in enumerate(items, 1) if n not in bad]
    removed = len(keep) < len(items)
    notes = list(out.notes) + ["Some content could not be verified against its sources" + (" and was removed." if removed else ".")]
    if not keep:  # never hand back an empty set when curated records were retrieved
        keep = seed_fallback_items(state.get("chunks", []), 1 if mock else plan.count)
        if keep:
            notes.append("The questions written for you could not be verified, so these are curated records from the knowledge base, shown as they are.")
        else:
            found = sorted({c.kb for c in state.get("chunks", [])}) or ["nothing"]
            notes.append(
                f"No questions could be produced: only {', '.join(found)} content was found for this request"
                + (f" (topics: {', '.join(plan.topics)})" if plan.topics else "")
                + ". Make sure the role knowledge base is selected, or add material to your resume."
            )
    if mock:
        kept_all = [i for i in out.items if i not in items] + keep
        return {"guard_ok": False, "issues": issues, "regenerate": False, "output": PrepOutput(items=kept_all, notes=notes),
                "current_item": keep[0] if keep else None}
    return {"guard_ok": False, "issues": issues, "regenerate": False, "output": PrepOutput(items=keep, notes=notes)}


def route_after_guard(state: State) -> str:
    if state.get("regenerate"):
        return "generate"
    if state["prep_plan"].mode != "mock_interview":
        return "end"
    if state.get("current_item") is None:  # nothing verifiable to ask: finish the interview (or end if nothing was asked)
        return "report" if state.get("scores") else "end"
    return "await_answer"
