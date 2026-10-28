"""Hybrid retrieval behind one interface: metadata filter, dense + BM25, RRF fusion, cross-encoder rerank.

Steps 1-4 of the pipeline (spec 8.4) run per KB inside `search_collection`. Quotas and dedupe (steps 5-6)
are applied afterwards in code by `select_context`. All defaults come from config.py so evals can vary them.
"""
import asyncio
import json
from dataclasses import dataclass, replace

from app.config import get_settings
from app.graph.state import KBName, RetrievedChunk
from app.rag import bm25, reranker, stores


@dataclass(frozen=True)
class RetrievalConfig:
    dense_k: int
    bm25_k: int
    rrf_k: int
    candidates: int  # merged candidates passed to the reranker
    final_k: int  # results returned
    rerank: bool = True
    dense_weight: float = 1.0
    blend: bool = False
    use_dense: bool = True
    use_bm25: bool = True

    @classmethod
    def default(cls, **overrides) -> "RetrievalConfig":
        s = get_settings()
        base = cls(
            dense_k=s.dense_top_k,
            bm25_k=s.bm25_top_k,
            rrf_k=s.rrf_k,
            candidates=s.rerank_candidates,
            final_k=s.retrieval_final_k,
            dense_weight=s.rrf_dense_weight,
            blend=s.rerank_blend,
        )
        return replace(base, **overrides) if overrides else base


def rrf_fuse(ranked_lists: list[list[str]], k: int = 60, weights: list[float] | None = None) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: score(d) = sum over lists of weight / (k + rank). Returns ids best first."""
    scores: dict[str, float] = {}
    for n, ids in enumerate(ranked_lists):
        w = weights[n] if weights else 1.0
        for rank, cid in enumerate(ids, start=1):
            scores[cid] = scores.get(cid, 0.0) + w / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def search_collection(
    collection: str,
    query: str,
    *,
    kb: KBName,
    where: dict | None,
    partition: dict | None,
    post_filters: dict | None = None,
    cfg: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    """Run the fixed pipeline over one collection. `where` filters dense search in Chroma; `partition` picks the
    BM25 index and `post_filters` narrow the BM25 hits after scoring (BM25 has no query-time filter)."""
    cfg = cfg or RetrievalConfig.default()
    ranked: list[list[str]] = []
    weights: list[float] = []
    rows: dict[str, dict] = {}

    if cfg.use_dense:
        for r in stores.query_dense(collection, stores.embed_query(query), where, cfg.dense_k):
            rows[r["id"]] = r
        ranked.append(sorted(rows, key=lambda i: rows[i]["score"], reverse=True))
        weights.append(cfg.dense_weight)
    if cfg.use_bm25:
        hits = bm25.search(collection, query, cfg.bm25_k, partition, post_filters)
        ranked.append([cid for cid, _ in hits])
        weights.append(1.0)

    fused = rrf_fuse(ranked, cfg.rrf_k, weights)[: cfg.candidates]
    missing = [cid for cid, _ in fused if cid not in rows]
    if missing:
        for r in stores.get_by_ids(collection, missing):
            rows[r["id"]] = r
    cands = [(cid, score) for cid, score in fused if cid in rows]
    if not cands:
        return []

    if cfg.rerank:
        scores = reranker.score_pairs(query, [_rerank_text(rows[cid]) for cid, _ in cands])
        order = sorted(zip(cands, scores, strict=True), key=lambda x: -x[1])
        final = [(cid, sc) for (cid, _), sc in order]
        if cfg.blend:  # reranker as one more signal next to the first-stage order, not a replacement for it
            blended = rrf_fuse([[cid for cid, _ in cands], [cid for cid, _ in final]], cfg.rrf_k)
            rr = dict(final)
            final = [(cid, rr[cid]) for cid, _ in blended]
    else:
        final = cands
    return [_to_chunk(kb, rows[cid], sc) for cid, sc in final[: cfg.final_k]]


def _rerank_text(row: dict) -> str:
    header = row["metadata"].get("header", "")
    return f"{header}\n{row['text']}" if header else row["text"]


def _to_chunk(kb: KBName, row: dict, score: float) -> RetrievedChunk:
    meta = row["metadata"]
    return RetrievedChunk(
        chunk_id=row["id"], kb=kb, text=row["text"], header=meta.get("header", ""), score=score, metadata=meta
    )


# ---- per-KB search functions (what the retrieval agent's tools call) ------

def search_personal(
    query: str, *, user_id: str, track_id: str, project: str | None = None, cfg: RetrievalConfig | None = None
) -> list[RetrievedChunk]:
    """Personal KB. user_id and track_id are mandatory and always filter (isolation)."""
    if not user_id or not track_id:
        raise ValueError("personal search requires user_id and track_id")
    where = stores.where_all(user_id=user_id, track_id=track_id, project=project)
    return search_collection(
        stores.personal_collection_name(track_id),
        query,
        kb="personal",
        where=where,
        partition={"user_id": user_id, "track_id": track_id},
        post_filters={"project": project},
        cfg=cfg,
    )


def search_role(
    query: str, *, role: str, topic: str | None = None, difficulty: str | None = None,
    cfg: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    where = stores.where_all(role=role, topic=topic, difficulty=difficulty)
    if cfg is None and not get_settings().rerank_role:
        cfg = RetrievalConfig.default(rerank=False, dense_weight=get_settings().role_dense_weight)
    return search_collection(
        stores.ROLE_COLLECTION,
        query,
        kb="role",
        where=where,
        partition={"role": role},
        post_filters={"topic": topic, "difficulty": difficulty},
        cfg=cfg,
    )


def search_company(
    query: str, *, company: str, role: str | None = None, round: str | None = None, topic: str | None = None,
    cfg: RetrievalConfig | None = None,
) -> list[RetrievedChunk]:
    where = stores.where_all(company=company, role=role, round=round, topic=topic)
    return search_collection(
        stores.COMPANY_COLLECTION,
        query,
        kb="company",
        where=where,
        partition={"company": company},
        post_filters={"role": role, "round": round, "topic": topic},
        cfg=cfg,
    )


async def search_personal_async(*a, **kw) -> list[RetrievedChunk]:
    return await asyncio.to_thread(lambda: search_personal(*a, **kw))


async def search_role_async(*a, **kw) -> list[RetrievedChunk]:
    return await asyncio.to_thread(lambda: search_role(*a, **kw))


async def search_company_async(*a, **kw) -> list[RetrievedChunk]:
    return await asyncio.to_thread(lambda: search_company(*a, **kw))


# ---- steps 5-6: quotas and dedupe (code, after the agent finishes) --------

def select_context(
    per_kb: dict[str, list[RetrievedChunk]], quotas: dict[str, int], fill_shortfall: bool = True
) -> list[RetrievedChunk]:
    """Apply per-KB quotas, drop near-duplicates (content hash), and hand any unused quota to the other KBs so a
    missing KB (for example no company data) is replaced by role or personal chunks instead of leaving holes."""
    seen: set[str] = set()
    chosen: list[RetrievedChunk] = []
    leftovers: list[RetrievedChunk] = []
    for kb, quota in quotas.items():
        taken = 0
        for ch in per_kb.get(kb, []):
            h = ch.metadata.get("content_hash") or ch.text
            if h in seen:
                continue
            seen.add(h)
            if taken < quota:
                chosen.append(ch)
                taken += 1
            else:
                leftovers.append(ch)
    if fill_shortfall:
        room = sum(quotas.values()) - len(chosen)
        chosen.extend(leftovers[: max(room, 0)])
    return chosen
