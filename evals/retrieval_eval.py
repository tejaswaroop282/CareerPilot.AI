"""Retrieval evals: hit@k, MRR, recall@k per KB, plus the ablation table (spec 11.2).

Personal KB (chunking, header, parent-child, retrieval-mode and reranker ablations) runs on the sample corpus in
data/sample_docs + the sample resume, with labels given as answer phrases so different chunkers stay comparable.
Role and company KBs run on the seeded collections with record-id labels (evals/datasets/retrieval_queries.json).
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.graph.helpers.loaders import parse_file
from app.rag import stores
from app.rag.chunking.base import Chunk, chunk_id, content_hash, count_tokens, _encoder
from app.rag.chunking.headings import chunk_headings
from app.rag.chunking.recursive import split_recursive
from app.rag.chunking.router import chunk_document, pick_source_type
from app.rag.retriever import RetrievalConfig, search_collection

ROOT = Path(__file__).resolve().parent.parent
DATASETS = Path(__file__).parent / "datasets"
KS = (1, 3, 5)


@dataclass
class Metrics:
    n: int = 0
    hit: dict[int, float] = field(default_factory=lambda: {k: 0.0 for k in KS})
    recall: dict[int, float] = field(default_factory=lambda: {k: 0.0 for k in KS})
    mrr: float = 0.0
    latency_ms: float = 0.0

    def row(self) -> dict:
        n = max(self.n, 1)
        return {
            **{f"hit@{k}": round(self.hit[k] / n, 3) for k in KS},
            "mrr": round(self.mrr / n, 3),
            **{f"recall@{k}": round(self.recall[k] / n, 3) for k in KS},
            "ms/query": round(self.latency_ms / n),
            "n": self.n,
        }


def judge(chunks: list, expect: dict) -> list[int]:
    """Return the 1-based ranks of relevant results. Labels are record ids or answer phrases."""
    ids = set(expect.get("ids", []))
    phrases = [p.lower() for p in expect.get("phrases", [])]
    ranks = []
    for rank, ch in enumerate(chunks, start=1):
        text = (ch.metadata.get("parent_text") or ch.text).lower()
        if ch.chunk_id in ids or any(p in text for p in phrases):
            ranks.append(rank)
    return ranks


def n_expected(expect: dict) -> int:
    return max(len(expect.get("ids", [])) + len(expect.get("phrases", [])), 1)


def evaluate(queries: list[dict], search) -> Metrics:
    """search(query_dict) -> ranked RetrievedChunk list."""
    m = Metrics()
    for q in queries:
        t0 = time.perf_counter()
        results = search(q)
        m.latency_ms += (time.perf_counter() - t0) * 1000
        ranks = judge(results, q["expect"])
        m.n += 1
        if ranks:
            m.mrr += 1.0 / ranks[0]
        for k in KS:
            in_k = [r for r in ranks if r <= k]
            m.hit[k] += 1.0 if in_k else 0.0
            m.recall[k] += min(len(in_k) / n_expected(q["expect"]), 1.0)
    return m


# ---- personal corpus variants --------------------------------------------

def load_corpus() -> list[tuple[str, str, str, bool]]:
    """(doc_id, filename, text, is_resume) for the sample docs and resume."""
    corpus = []
    for p in sorted((ROOT / "data" / "sample_docs").glob("*")):
        corpus.append((p.stem, p.name, parse_file(p.read_bytes(), p.suffix.lstrip(".").lower()), False))
    for p in sorted((ROOT / "data" / "sample_resumes").glob("*.pdf")):
        corpus.append((p.stem, p.name, parse_file(p.read_bytes(), "pdf"), True))
    return corpus


def _chunk(doc_id: str, kind: str, name: str, text: str, meta: dict | None = None, header: str = "") -> Chunk:
    return Chunk(chunk_id=kind, kb="personal", text=text, header=header, source_type="eval", source_name=name,
                 metadata=meta or {"doc_id": doc_id}, content_hash=content_hash(text))


def chunks_fixed(corpus, size: int = 400) -> list[Chunk]:
    """Naive baseline: fixed-size token windows, no overlap, no structure, no headers."""
    enc, out = _encoder(), []
    for doc_id, name, text, _ in corpus:
        toks = enc.encode(text, disallowed_special=()) if enc else text.split()
        for i in range(0, len(toks), size):
            piece = enc.decode(toks[i : i + size]) if enc else " ".join(toks[i : i + size])
            out.append(_chunk(doc_id, chunk_id(doc_id, len(out), "fixed"), name, piece))
    return out


def chunks_structured(corpus, with_header: bool = True) -> list[Chunk]:
    out = []
    for doc_id, name, text, is_resume in corpus:
        st = pick_source_type(name.rsplit(".", 1)[-1], text, is_resume)
        for c in chunk_document(doc_id=doc_id, source_type=st, source_name=name, text=text, base_meta={}):
            if not with_header:
                c = c.model_copy(update={"header": ""})
            out.append(c)
    return out


def chunks_parent_child(corpus, child_tokens: int = 250) -> list[Chunk]:
    """Small child chunks are embedded; the parent section is carried in metadata and used for judging/answering."""
    out = []
    for doc_id, name, text, is_resume in corpus:
        if is_resume:
            parents = [(h, t) for h, t, _ in __import__("app.rag.chunking.resume", fromlist=["x"]).chunk_resume(text)]
        else:
            parents = [(f"{name} > {p}", t) for p, t in chunk_headings(text, max_tokens=900)] or [(name, text)]
        for header, parent in parents:
            for piece in split_recursive(parent, max_tokens=child_tokens, overlap=30):
                out.append(_chunk(doc_id, chunk_id(doc_id, len(out), "pc"), name, piece,
                                  {"doc_id": doc_id, "parent_text": parent[:3500]}, header))
    return out


def chunks_semantic(corpus, percentile: float = 20.0, max_tokens: int = 500) -> list[Chunk]:
    """Semantic chunking experiment: split where the embedding similarity between neighbouring sentences drops
    below the given percentile of all similarities in the document (or when a chunk reaches max_tokens)."""
    import re

    import numpy as np

    out = []
    for doc_id, name, text, _ in corpus:
        sents = [x.strip() for x in re.split(r"(?<=[.!?])\s+|\n{2,}", text) if x.strip()]
        if len(sents) < 3:
            out.append(_chunk(doc_id, chunk_id(doc_id, len(out), "sem"), name, text))
            continue
        vecs = np.array(stores.embed_texts(sents))
        sims = (vecs[:-1] * vecs[1:]).sum(axis=1)  # embeddings are normalised, so the dot product is cosine
        cut = np.percentile(sims, percentile)
        cur, size = [sents[0]], count_tokens(sents[0])
        for i, sent in enumerate(sents[1:]):
            n = count_tokens(sent)
            if sims[i] < cut or size + n > max_tokens:
                out.append(_chunk(doc_id, chunk_id(doc_id, len(out), "sem"), name, " ".join(cur)))
                cur, size = [], 0
            cur.append(sent)
            size += n
        out.append(_chunk(doc_id, chunk_id(doc_id, len(out), "sem"), name, " ".join(cur)))
    return out


_embed_cache: dict[str, list[float]] = {}


def build_collection(name: str, chunks: list[Chunk]) -> str:
    """Embed (with a per-run cache) and store chunks in a throwaway eval collection."""
    stores.delete_collection(name)
    texts = [c.embed_text() for c in chunks]
    missing = [t for t in dict.fromkeys(texts) if t not in _embed_cache]
    for t, e in zip(missing, stores.embed_texts(missing), strict=True):
        _embed_cache[t] = e
    stores.upsert_chunks(name, chunks, [_embed_cache[t] for t in texts])
    return name


def personal_ablation(queries: list[dict]) -> dict[str, dict]:
    corpus = load_corpus()
    default_cfg = RetrievalConfig.default()
    variants = {
        # chunking and header ablations (hybrid + rerank retrieval)
        "chunk: fixed-size 400, no header": (chunks_fixed(corpus), default_cfg),
        "chunk: structure-aware, no header": (chunks_structured(corpus, with_header=False), default_cfg),
        "chunk: structure-aware + header (default)": (chunks_structured(corpus), default_cfg),
        "chunk: parent-child (250 child)": (chunks_parent_child(corpus), default_cfg),
        "chunk: semantic (embedding breakpoints)": (chunks_semantic(corpus), default_cfg),
    }
    structured = variants["chunk: structure-aware + header (default)"][0]
    # retrieval-mode and reranker ablations on the default chunks
    modes = {
        "mode: vector only": RetrievalConfig.default(use_bm25=False, rerank=False),
        "mode: BM25 only": RetrievalConfig.default(use_dense=False, rerank=False),
        "mode: hybrid (RRF), no rerank": RetrievalConfig.default(rerank=False),
        "mode: hybrid + rerank (default)": default_cfg,
        "mode: vector + rerank": RetrievalConfig.default(use_bm25=False),
    }
    results: dict[str, dict] = {}
    for label, (chunks, cfg) in variants.items():
        col = build_collection("eval_" + str(abs(hash(label)) % 10**8), chunks)
        results[label] = evaluate(queries, lambda q, c=col, g=cfg: _search(c, q["query"], g)).row()
        stores.delete_collection(col)
    col = build_collection("eval_default_chunks", structured)
    for label, cfg in modes.items():
        results[label] = evaluate(queries, lambda q, g=cfg: _search(col, q["query"], g)).row()
    stores.delete_collection(col)
    return results


def _search(collection: str, query: str, cfg: RetrievalConfig):
    return search_collection(collection, query, kb="personal", where=None, partition=None, cfg=cfg)


# ---- seeded KBs ----------------------------------------------------------

def seeded_kb_eval(queries: list[dict]) -> dict[str, dict]:
    """queries: {"kb": "role"|"company", "query", "filters": {...}, "expect": {"ids": [...]}}"""
    from app.rag import retriever

    out: dict[str, dict] = {}
    for kb in ("role", "company"):
        qs = [q for q in queries if q.get("kb") == kb]
        if not qs:
            continue
        fn = retriever.search_role if kb == "role" else retriever.search_company
        for label, cfg in (("hybrid + rerank (default)", RetrievalConfig.default()),
                           ("vector only", RetrievalConfig.default(use_bm25=False, rerank=False)),
                           ("BM25 only", RetrievalConfig.default(use_dense=False, rerank=False)),
                           ("hybrid, no rerank", RetrievalConfig.default(rerank=False))):
            out[f"{kb}: {label}"] = evaluate(qs, lambda q, f=fn, c=cfg: f(q["query"], cfg=c, **q.get("filters", {}))).row()
        # what the app really runs (role: its own dense weight, no reranker; company: defaults)
        out[f"{kb}: PRODUCTION config"] = evaluate(qs, lambda q, f=fn: f(q["query"], **q.get("filters", {}))).row()
    return out


def load_queries(name: str) -> list[dict]:
    path = DATASETS / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def print_table(title: str, rows: dict[str, dict]) -> None:
    if not rows:
        return
    cols = list(next(iter(rows.values())))
    w = max(len(k) for k in rows) + 2
    print(f"\n{title}")
    print("".ljust(w) + "".join(c.rjust(10) for c in cols))
    for label, r in rows.items():
        print(label.ljust(w) + "".join(str(r[c]).rjust(10) for c in cols))


def run() -> dict:
    personal = personal_ablation(load_queries("personal_queries.json"))
    print_table("Personal KB: chunking and retrieval ablations", personal)
    seeded = seeded_kb_eval(load_queries("retrieval_queries.json"))
    print_table("Role / company KB retrieval", seeded)
    return {"personal": personal, "seeded": seeded}


if __name__ == "__main__":
    run()
