"""Chroma collections: kb_personal_{track_id} (private), kb_role and kb_company (shared, seeded).

Chroma is synchronous; async callers use the *_async wrappers. Every write invalidates the BM25 cache
for that collection so keyword search always reflects the stored chunks.
"""
import asyncio
import re
import time
from functools import lru_cache
from typing import Any

import chromadb

from app.config import get_settings
from app.rag import bm25
from app.rag.chunking.base import Chunk
from app.rag.llm import get_embeddings

ROLE_COLLECTION = "kb_role"
COMPANY_COLLECTION = "kb_company"
EMBED_BATCH = 50


def personal_collection_name(track_id: str) -> str:
    return f"kb_personal_{track_id}"


@lru_cache
def _client() -> chromadb.api.ClientAPI:
    return chromadb.PersistentClient(path=get_settings().chroma_dir)


def get_collection(name: str):
    return _client().get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def where_all(**conds: Any) -> dict | None:
    """Build a Chroma `where` filter from equality conditions (None values are skipped)."""
    items = [{k: v} for k, v in conds.items() if v is not None]
    if not items:
        return None
    return items[0] if len(items) == 1 else {"$and": items}


def _retry(fn, attempts: int = 8):
    """Retry transient API errors; honour the server's suggested delay on rate limits (free tiers are per minute)."""
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if i == attempts - 1:
                raise
            m = re.search(r"retry in (\d+(?:\.\d+)?)s", str(e))
            time.sleep(min(70.0, float(m.group(1)) + 2) if m else min(30, 2**i))


def embed_texts(texts: list[str]) -> list[list[float]]:
    emb = get_embeddings()
    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        out.extend(_retry(lambda b=batch: emb.embed_documents(b)))
    return out


@lru_cache(maxsize=2048)
def _embed_query_cached(text: str) -> tuple[float, ...]:
    return tuple(_retry(lambda: get_embeddings().embed_query(text)))


def embed_query(text: str) -> list[float]:
    return list(_embed_query_cached(text))


def upsert_chunks(collection: str, chunks: list[Chunk], embeddings: list[list[float]] | None = None) -> int:
    if not chunks:
        return 0
    if embeddings is None:
        embeddings = embed_texts([c.embed_text() for c in chunks])
    get_collection(collection).upsert(
        ids=[c.chunk_id for c in chunks],
        documents=[c.text for c in chunks],
        metadatas=[c.flat_metadata() for c in chunks],
        embeddings=embeddings,
    )
    bm25.invalidate(collection)
    return len(chunks)


def delete_where(collection: str, where: dict) -> None:
    get_collection(collection).delete(where=where)
    bm25.invalidate(collection)


def get_chunks(collection: str, where: dict | None = None) -> list[dict]:
    res = get_collection(collection).get(where=where, include=["documents", "metadatas"])
    return [
        {"id": i, "text": d, "metadata": m}
        for i, d, m in zip(res["ids"], res["documents"], res["metadatas"], strict=True)
    ]


def get_by_ids(collection: str, ids: list[str]) -> list[dict]:
    res = get_collection(collection).get(ids=ids, include=["documents", "metadatas"])
    return [
        {"id": i, "text": d, "metadata": m, "score": 0.0}
        for i, d, m in zip(res["ids"], res["documents"], res["metadatas"], strict=True)
    ]


def count(collection: str, where: dict | None = None) -> int:
    if where is None:
        return get_collection(collection).count()
    return len(get_collection(collection).get(where=where, include=[])["ids"])


def query_dense(collection: str, embedding: list[float], where: dict | None, k: int) -> list[dict]:
    col = get_collection(collection)
    n = col.count()
    if n == 0:
        return []
    res = col.query(
        query_embeddings=[embedding],
        n_results=min(k, n),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    return [
        {"id": i, "text": d, "metadata": m, "score": 1.0 - dist}
        for i, d, m, dist in zip(
            res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0], strict=True
        )
    ]


def delete_collection(name: str) -> None:
    try:
        _client().delete_collection(name)
    except Exception:  # collection never existed
        pass
    bm25.invalidate(name)


# ---- async wrappers -------------------------------------------------------

async def upsert_chunks_async(collection: str, chunks: list[Chunk]) -> int:
    return await asyncio.to_thread(upsert_chunks, collection, chunks)


async def delete_where_async(collection: str, where: dict) -> None:
    await asyncio.to_thread(delete_where, collection, where)


async def get_chunks_async(collection: str, where: dict | None = None) -> list[dict]:
    return await asyncio.to_thread(get_chunks, collection, where)


async def delete_personal_collection(user_id: str, track_id: str) -> None:
    """Remove the track's personal vectors and BM25 entries."""
    await asyncio.to_thread(delete_collection, personal_collection_name(track_id))
