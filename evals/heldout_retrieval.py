"""Held-out check for the role-KB retrieval change (embedding each question together with its key points).

The 49 queries used to find and fix the failures cannot also be the proof that the fix works. These 30 queries were written
afterwards, for records that were not in that set, from the question text only (key points not looked at). The same hybrid
search runs over two collections: the old one (question-only embeddings) and the production one (key points added to the
embedded text). No model calls. Usage: python -m evals.heldout_retrieval
"""
import json
from pathlib import Path

from app.config import get_settings
from app.rag import bm25, retriever, stores
from app.rag.chunking.atomic import role_question_chunk

DATA = Path(__file__).parent / "datasets" / "heldout_role_queries.json"
PLAIN = "kb_role_plain"


def build_plain() -> int:
    """The previous indexing, rebuilt next to the production collection: only the question is embedded."""
    stores.delete_collection(PLAIN)
    chunks, texts = [], []
    for path in sorted((Path("data") / "role_kb").glob("*.json")):
        for q in json.loads(path.read_text(encoding="utf-8"))["questions"]:
            c = role_question_chunk(q)
            c.embed_extra = ""
            chunks.append(c)
            texts.append(c.embed_text())
    n = stores.upsert_chunks(PLAIN, chunks, embeddings=stores.embed_texts(texts))
    bm25.invalidate(PLAIN)
    return n


def score(collection: str, queries: list[dict], **cfg_over) -> dict:
    cfg = retriever.RetrievalConfig.default(rerank=False, dense_weight=get_settings().role_dense_weight, **cfg_over)
    h1 = h3 = h5 = 0
    mrr = 0.0
    for q in queries:
        role = q["filters"]["role"]
        got = retriever.search_collection(collection, q["query"], kb="role", where=stores.where_all(role=role),
                                          partition={"role": role}, post_filters={}, cfg=cfg)
        ids = [c.chunk_id for c in got]
        gold = q["expect"]["ids"][0]
        if gold in ids:
            r = ids.index(gold) + 1
            h1 += r == 1
            h3 += r <= 3
            h5 += r <= 5
            mrr += 1 / r
    n = len(queries)
    return {"hit@1": round(h1 / n, 3), "hit@3": round(h3 / n, 3), "hit@5": round(h5 / n, 3), "mrr": round(mrr / n, 3), "n": n}


def run() -> dict:
    queries = json.loads(DATA.read_text(encoding="utf-8"))
    if stores.count(PLAIN) == 0:
        print("built", PLAIN, build_plain(), "records")
    out = {
        "question only embedding (old)": score(PLAIN, queries),
        "question + key points (production)": score(stores.ROLE_COLLECTION, queries),
        "old, dense only": score(PLAIN, queries, use_bm25=False),
        "production, dense only": score(stores.ROLE_COLLECTION, queries, use_bm25=False),
    }
    print("\nHeld-out paraphrase queries (30, written after the fix, records not used before)")
    print(f"{'setup':40}{'hit@1':>8}{'hit@3':>8}{'hit@5':>8}{'mrr':>8}")
    for k, v in out.items():
        print(f"{k:40}{v['hit@1']:>8}{v['hit@3']:>8}{v['hit@5']:>8}{v['mrr']:>8}")
    Path(__file__).parent.joinpath("results", "heldout_retrieval.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    run()
