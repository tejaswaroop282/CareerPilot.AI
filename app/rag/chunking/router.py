"""Pick a chunking strategy from the source type and build stable-id Chunk objects."""
from pathlib import PurePath

from app.rag.chunking.base import CHUNKER_VERSION, Chunk, chunk_id, content_hash
from app.rag.chunking.headings import chunk_headings, count_headings
from app.rag.chunking.recursive import split_recursive
from app.rag.chunking.resume import chunk_resume


def pick_source_type(file_type: str, text: str, is_resume: bool = False) -> str:
    """resume -> resume sections; markdown or text with headings -> project_doc; everything else -> notes."""
    if is_resume:
        return "resume"
    if file_type == "md" or count_headings(text) >= 2:
        return "project_doc"
    return "notes"


def _doc_title(text: str, source_name: str) -> str:
    for line in text.splitlines():
        s = line.strip().lstrip("#").strip()
        if s:
            return s[:60]
    return PurePath(source_name).stem


def chunk_document(
    *, doc_id: str, source_type: str, source_name: str, text: str, base_meta: dict
) -> list[Chunk]:
    """Chunk one personal-KB document. base_meta carries user_id, track_id, role."""
    meta_common = {"doc_id": doc_id, **base_meta}
    raw: list[tuple[str, str, dict]] = []  # (header, text, extra metadata)

    if source_type == "resume":
        raw = chunk_resume(text)
    elif source_type == "project_doc":
        project = _doc_title(text, source_name)
        for path, body in chunk_headings(text):
            header = f"{source_name} > {path}" if path else source_name
            raw.append((header, body, {"project": project, "section": path}))
    else:  # notes and learning material
        for piece in split_recursive(text, max_tokens=650, overlap=100):
            raw.append((source_name, piece, {}))

    chunks: list[Chunk] = []
    for i, (header, body, extra) in enumerate(raw):
        chunks.append(
            Chunk(
                chunk_id=chunk_id(doc_id, i),
                kb="personal",
                text=body,
                header=header,
                source_type=source_type,
                source_name=source_name,
                metadata={**meta_common, **extra},
                chunker_version=CHUNKER_VERSION,
                content_hash=content_hash(body),
            )
        )
    return chunks
