"""BM25 indexes built from the chunks stored in Chroma (rank_bm25, in memory, never unpickled).

One index per partition (a whole personal collection, one role, one company). BM25 has no query-time
metadata filter, so remaining filters (topic, project, difficulty, round) are applied to the scored list.
Indexes are rebuilt lazily after any write to the collection, so Chroma stays the single source of truth.
"""
import re
import threading
from dataclasses import dataclass
from typing import Any

from rank_bm25 import BM25Okapi

_TOKEN = re.compile(r"[a-z0-9]+(?:[+#.][a-z0-9]+)*")
_STOP = frozenset("a an and are as at be by for from in is it of on or that the this to with".split())

_lock = threading.Lock()
_cache: dict[tuple[str, str], "BM25Partition"] = {}


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOP]


@dataclass
class BM25Partition:
    ids: list[str]
    metadatas: list[dict]
    bm25: BM25Okapi | None

    def search(self, query: str, k: int, filters: dict | None = None) -> list[tuple[str, float]]:
        """Return up to k (chunk_id, score) pairs, scored over the whole partition then filtered."""
        tokens = tokenize(query)
        if self.bm25 is None or not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ranked = sorted(range(len(self.ids)), key=lambda i: scores[i], reverse=True)
        out: list[tuple[str, float]] = []
        for i in ranked:
            if scores[i] <= 0:
                break
            if filters and not _matches(self.metadatas[i], filters):
                continue
            out.append((self.ids[i], float(scores[i])))
            if len(out) >= k:
                break
        return out


def _matches(meta: dict, filters: dict) -> bool:
    for key, want in filters.items():
        if want is None:
            continue
        have = meta.get(key)
        if isinstance(want, (list, tuple, set)):
            if have not in want:
                return False
        elif have != want:
            return False
    return True


def _key(collection: str, partition: dict | None) -> tuple[str, str]:
    return collection, repr(sorted((partition or {}).items()))


def get_partition(collection: str, partition: dict | None = None) -> BM25Partition:
    """Fetch (or build) the BM25 index for a collection, optionally restricted to a partition such as {'role': 'sde'}."""
    from app.rag import stores  # local import: stores imports this module

    key = _key(collection, partition)
    with _lock:
        cached = _cache.get(key)
    if cached is not None:
        return cached
    where = stores.where_all(**(partition or {}))
    rows = stores.get_chunks(collection, where)
    ids = [r["id"] for r in rows]
    # Index the header with the text so contextual headers help keyword search too.
    docs = [tokenize(f"{r['metadata'].get('header', '')} {r['text']}") for r in rows]
    part = BM25Partition(ids, [r["metadata"] for r in rows], BM25Okapi(docs) if docs else None)
    with _lock:
        _cache[key] = part
    return part


def invalidate(collection: str) -> None:
    with _lock:
        for key in [k for k in _cache if k[0] == collection]:
            del _cache[key]


def search(
    collection: str, query: str, k: int, partition: dict | None = None, filters: dict[str, Any] | None = None
) -> list[tuple[str, float]]:
    return get_partition(collection, partition).search(query, k, filters)
