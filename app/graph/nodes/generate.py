"""Node 8: produce the question set (or the next interview question) using only the retrieved chunks."""
from pydantic import BaseModel, Field

from app.graph.helpers.render import KB_LABEL, record_of, render_chunks
from app.graph.prompts import GENERATE_SYSTEM, GENERATE_USER
from app.graph.state import KBName, PrepItem, PrepOutput, State
from app.observability import annotate, stage
from app.rag.llm import get_chat_model

LABEL_TO_KB: dict[str, KBName] = {v.lower(): k for k, v in KB_LABEL.items()}  # "role" -> "role" etc.


class GeneratedItem(BaseModel):
    question: str
    key_points: list[str] = Field(min_length=1)
    kb: str = Field(description="Personal, Role or Company")
    citations: list[str] = Field(description="chunk ids from the provided chunks")
    topic: str = ""
    difficulty: str = ""


class GeneratedSet(BaseModel):
    items: list[GeneratedItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list, description="gaps or fallbacks the user should see")


def to_item(g: GeneratedItem, chunks_by_id: dict) -> PrepItem:
    kb: KBName = LABEL_TO_KB.get(g.kb.strip().lower(), "role")
    item = PrepItem(
        question=g.question.strip(), key_points=[k.strip() for k in g.key_points if k.strip()], kb=kb,
        citations=list(dict.fromkeys(g.citations)), topic=g.topic, difficulty=g.difficulty,
    )
    # a multiple-choice record is reproduced verbatim from the seed so options and the answer never come from the LLM
    cited = [chunks_by_id[c] for c in item.citations if c in chunks_by_id]
    if len(cited) == 1 and cited[0].kb == "role":
        rec = record_of(cited[0])
        if rec.get("type") == "mcq":
            item.kind, item.question = "mcq", rec["question"]
            item.options, item.correct_index, item.explanation = rec["options"], rec["correct_index"], rec.get("explanation", "")
            item.key_points = rec.get("key_points") or item.key_points
    return item


def seed_fallback_items(chunks: list, count: int) -> list[PrepItem]:
    """Curated records (role and company knowledge bases) that were retrieved, shown as they are. Used when nothing the model
    wrote could be verified: these already carry their own question, key points and citation."""
    out: list[PrepItem] = []
    for c in chunks:
        rec = record_of(c)
        text = rec.get("question") or rec.get("text")
        if c.kb in ("role", "company") and rec and text and rec.get("key_points"):
            out.append(to_item(GeneratedItem(
                question=text, key_points=rec["key_points"], kb=KB_LABEL[c.kb], citations=[c.chunk_id],
                topic=str(c.metadata.get("topic", "")), difficulty=str(c.metadata.get("difficulty", "")),
            ), {x.chunk_id: x for x in chunks}))
        if len(out) >= count:
            break
    return out


@stage("build_generate_prompt", outputs=lambda user: {"chars": len(user)})
def build_prompt(state: State) -> str:
    """Assemble the generation prompt from the plan and the retrieved chunks (its own span: what the model really saw)."""
    plan = state["prep_plan"]
    chunks = state.get("chunks", [])
    mock = plan.mode == "mock_interview"
    prior = state.get("output")
    asked = [i.question for i in (prior.items if prior else [])] if mock else []
    if mock:
        mode_line = "mock interview: write exactly ONE next question with its key points."
        if state.get("follow_up"):
            mode_line = "mock interview follow-up: write ONE follow-up question that probes the points the candidate missed."
    else:
        mode_line = f"question set: write up to {plan.count} questions."
    issues = state.get("issues") or []
    rendered = render_chunks(chunks)
    annotate(chunk_ids=[c.chunk_id for c in chunks], chunks=len(chunks), rendered_chars=len(rendered))
    return GENERATE_USER.format(
        intent=plan.intent, role=plan.role, company=plan.company or "none", project=plan.project or "none",
        topics=", ".join(plan.topics) or "any", mode_line=mode_line,
        notes="; ".join(state.get("retrieval_notes") or []) or "none",
        issues_block=("Fix these grounding problems from the previous attempt: " + "; ".join(issues) + "\n") if issues else "",
        history_block=("Questions already asked (do not repeat): " + " | ".join(asked) + "\n") if asked else "",
        chunks=rendered,
    )


async def generate(state: State) -> dict:
    plan = state["prep_plan"]
    chunks = state.get("chunks", [])
    mock = plan.mode == "mock_interview"
    prior = state.get("output")
    user = build_prompt(state)
    llm = get_chat_model().with_structured_output(GeneratedSet)
    gen: GeneratedSet = await llm.ainvoke([("system", GENERATE_SYSTEM), ("human", user)])
    by_id = {c.chunk_id: c for c in chunks}
    limit = 1 if mock else plan.count
    items = [to_item(g, by_id) for g in gen.items[:limit]]
    notes = list(dict.fromkeys((state.get("retrieval_notes") or []) + gen.notes))
    if mock:
        # on a regeneration the previous attempt for this turn is replaced, not appended
        kept = list(prior.items) if prior else []
        if state.get("regenerate") and kept:
            kept = kept[:-1]
        return {"output": PrepOutput(items=kept + items, notes=notes), "current_item": items[-1] if items else None, "regenerate": False}
    return {"output": PrepOutput(items=items, notes=notes), "current_item": None, "regenerate": False}
