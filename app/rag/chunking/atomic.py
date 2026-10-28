"""Atomic chunking for seeded records: one chunk per record, never split. The full record rides in metadata."""
import json

from app.rag.chunking.base import CHUNKER_VERSION, Chunk, content_hash


def role_question_chunk(rec: dict) -> Chunk:
    """Role KB question: embed the question plus its topic; keep the whole record (key points) in metadata."""
    topic = rec.get("topic", "")
    return Chunk(
        chunk_id=rec["id"],
        kb="role",
        text=rec["question"],
        header=" > ".join(x for x in ("Role KB", rec["role"], topic, rec.get("subtopic", ""), rec.get("difficulty", "")) if x),
        source_type="role_mcq" if rec.get("type") == "mcq" else "role_question",
        source_name=rec.get("source", ""),
        # measured on 49 paraphrased queries (LangSmith experiments role-rag-*): embedding the key points next to the question
        # lifts Hit@5 from 0.857 to 1.0 with no loss on exact-question search (hit@5 1.0 on all 629)
        embed_extra=" ".join(rec.get("key_points", [])),
        metadata={
            "role": rec["role"],
            "topic": topic,
            "difficulty": rec.get("difficulty"),
            "subtopic": rec.get("subtopic") or None,
            "kind": "mcq" if rec.get("type") == "mcq" else "text",
            "record": json.dumps(rec, ensure_ascii=False),
        },
        chunker_version=CHUNKER_VERSION,
        content_hash=content_hash(rec["question"] + " ".join(rec.get("key_points", []))),
    )


def company_record_chunk(rec: dict) -> Chunk:
    """Company KB record (question, experience summary, or topic pattern)."""
    header = f"Company KB > {rec['company']} > {rec['role']} > {rec.get('round', '')} > {rec.get('type', '')}"
    return Chunk(
        chunk_id=rec["id"],
        kb="company",
        text=rec["text"],
        header=header.replace(" >  >", " >").rstrip(" >"),
        source_type=f"company_{rec.get('type', 'record')}",
        source_name=rec.get("source", ""),
        metadata={
            "company": rec["company"],
            "role": rec["role"],
            "round": rec.get("round"),
            "topic": rec.get("topic"),
            "record_type": rec.get("type"),
            "difficulty": rec.get("difficulty"),
            "license": rec.get("license"),
            "confidence": rec.get("confidence"),
            "year": rec.get("year"),
            "record": json.dumps(rec, ensure_ascii=False),
        },
        chunker_version=CHUNKER_VERSION,
        content_hash=content_hash(rec["text"]),
    )
