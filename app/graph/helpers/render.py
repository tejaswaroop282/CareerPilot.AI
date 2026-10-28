"""Render retrieved chunks as text for prompts. Seeded records keep their key points in metadata, so the model
only sees them if we put them back into the text."""
import json

from app.graph.state import RetrievedChunk

KB_LABEL = {"personal": "Personal", "role": "Role", "company": "Company"}


def record_of(chunk: RetrievedChunk) -> dict:
    raw = chunk.metadata.get("record")
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return {}


def chunk_body(chunk: RetrievedChunk) -> str:
    """Full text the generator may use: question/record text plus its expected key points and options."""
    rec = record_of(chunk)
    if chunk.kb == "role" and rec:
        parts = [rec.get("question", chunk.text)]
        if rec.get("type") == "mcq":
            parts.append("Options: " + " | ".join(rec.get("options", [])))
            parts.append(f"Correct option index: {rec.get('correct_index')}. {rec.get('explanation', '')}")
        parts += [f"Key point: {k}" for k in rec.get("key_points", [])]
        if rec.get("pitfall"):
            parts.append(f"Common pitfall: {rec['pitfall']}")
        return "\n".join(parts)
    if chunk.kb == "company" and rec:
        parts = [rec.get("text", chunk.text)]
        parts += [f"Key point: {k}" for k in rec.get("key_points", [])]
        if rec.get("key_points_source") == "generated":
            parts.append("(key points above are model-generated standard-solution notes, not from the interview report)")
        meta = f"(record type {rec.get('type')}, round {rec.get('round') or 'n/a'}, confidence {rec.get('confidence')}, year {rec.get('year')}, source {rec.get('source')})"
        return "\n".join(parts + [meta])
    return chunk.text


def render_chunks(chunks: list[RetrievedChunk]) -> str:
    return "\n\n".join(
        f'<chunk id="{c.chunk_id}" kb="{KB_LABEL[c.kb]}">\n{c.header}\n{chunk_body(c)}\n</chunk>' for c in chunks
    )


def snippet(chunk: RetrievedChunk, n: int = 140) -> str:
    text = " ".join(chunk.text.split())
    text = text if len(text) <= n else text[: n - 1] + "…"
    meta = chunk.metadata
    tag = meta.get("topic") or meta.get("project") or meta.get("source_name") or ""
    return f"{chunk.chunk_id} [{KB_LABEL[chunk.kb]}{' / ' + str(tag) if tag else ''}] score {chunk.score:.2f}: {text}"
