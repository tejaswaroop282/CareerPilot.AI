"""Shared chunk shape, token counting, and id/hash helpers."""
import hashlib
import re
from datetime import datetime, timezone
from functools import lru_cache

from pydantic import BaseModel, Field

CHUNKER_VERSION = "v1"


class Chunk(BaseModel):
    chunk_id: str
    kb: str
    text: str
    header: str = ""
    source_type: str = ""  # resume | project_doc | notes | role_question | ...
    source_name: str = ""
    embed_extra: str = ""  # extra text only the embedding sees (role questions: their key points); never stored or shown
    metadata: dict = Field(default_factory=dict)  # role, company, topic, difficulty, round, project, user_id, ...
    chunker_version: str = CHUNKER_VERSION
    content_hash: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def embed_text(self) -> str:
        """Contextual header is prepended before embedding but stored separately."""
        body = f"{self.text} {self.embed_extra}".strip() if self.embed_extra else self.text
        return f"{self.header}\n{body}" if self.header else body

    def flat_metadata(self) -> dict:
        """Flat, None-free metadata dict as stored in Chroma (all KBs share these keys)."""
        flat = {
            "chunk_id": self.chunk_id,
            "kb": self.kb,
            "header": self.header,
            "source_type": self.source_type,
            "source_name": self.source_name,
            "chunker_version": self.chunker_version,
            "content_hash": self.content_hash,
            "created_at": self.created_at,
            **self.metadata,
        }
        return {k: v for k, v in flat.items() if v is not None}


@lru_cache
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")  # proxy for the embedding tokenizer
    except Exception:  # offline or missing vocab: fall back to a word-based estimate
        return None


def count_tokens(text: str) -> int:
    enc = _encoder()
    if enc is not None:
        return len(enc.encode(text, disallowed_special=()))
    return int(len(text.split()) * 1.3) + 1


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode()).hexdigest()[:16]


def chunk_id(doc_id: str, index: int, version: str = CHUNKER_VERSION) -> str:
    """Stable id for user documents: {doc_id}:{chunker_version}:{index}."""
    return f"{doc_id}:{version}:{index}"
