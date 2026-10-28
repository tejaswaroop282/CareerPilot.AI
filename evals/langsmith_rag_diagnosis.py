"""Issue 1: why does the role KB give wrong answers? Attribute every failure to retrieval, prompt or generation.

Runs the real pipeline stages for each role query (retrieve -> build prompt -> generate -> judge) as a LangSmith experiment,
so each stage is a span in the trace and each example gets feedback scores:

  gold_rank, hit@5, hit@context (the 8 chunks the generator receives), prompt_contains_gold, answer_covers_gold,
  faithful_to_context and failure_stage = ok | ranking_near_miss | retrieval_miss | prompt_drop | generation_error

Retrieval and prompt stages cost no model calls; generation and the judge run only for failures and a small sample of
hits (token budget: Groq free tier). Variants (--variant) are compared as separate experiments in the LangSmith UI:
  baseline   the production role collection (question text only is embedded)
  keypoints  question plus its key points are embedded and indexed for both dense and BM25 (collection kb_role_v2)
  kp_dense   only the embedding sees the key points; the stored text and BM25 keep the plain question (kb_role_v3)

Usage: python -m evals.langsmith_rag_diagnosis [--variant baseline|keypoints|kp_dense] [--generate none|misses|sample|all] [--limit N]
"""
import argparse
import asyncio
import json
import re
import time
from collections import Counter
from pathlib import Path

from langsmith import Client, evaluate, traceable

from app.config import get_settings
from app.graph.nodes import check_grounding, generate as gen_node
from app.graph.prompts import GROUNDING_SYSTEM
from app.graph.state import GroundingVerdict, PrepPlan, RetrievedChunk
from app.rag import bm25, retriever, stores
from app.rag.chunking.atomic import role_question_chunk
from app.rag.llm import get_chat_model

DATASETS = Path(__file__).parent / "datasets"
DATASET = "careerpilot-role-rag-diagnosis"
V2_COLLECTION = "kb_role_v2"
V3_COLLECTION = "kb_role_v3"
MISS_AT = 5  # the headline metric: gold must be in the top 5
STAGES = ("ok", "ranking_near_miss", "retrieval_miss", "prompt_drop", "generation_error", "generation_unavailable")


def call_llm(make_coro, tries: int = 8):
    """Run an async LLM call, waiting out free-tier rate limits (per-minute token caps clear within a minute)."""
    for attempt in range(tries):
        try:
            return asyncio.run(make_coro())
        except Exception as e:  # noqa: BLE001
            if attempt < tries - 1 and ("429" in str(e) or "rate limit" in str(e).lower() or "RateLimit" in type(e).__name__):
                time.sleep(20)
                continue
            raise


# ---- dataset -----------------------------------------------------------------------------

def gold_for(ids: list[str]) -> dict[str, dict]:
    rows = stores.get_by_ids(stores.ROLE_COLLECTION, ids)
    out = {}
    for r in rows:
        rec = json.loads(r["metadata"]["record"])
        out[r["id"]] = {"question": rec["question"], "key_points": rec.get("key_points", [])}
    return out


def ensure_dataset(client: Client, refresh: bool = False) -> None:
    queries = [q for q in json.loads((DATASETS / "retrieval_queries.json").read_text(encoding="utf-8")) if q["kb"] == "role"]
    if client.has_dataset(dataset_name=DATASET):
        if not refresh:
            return
        client.delete_dataset(dataset_name=DATASET)
    ds = client.create_dataset(DATASET, description="Role-KB queries with the gold record, for retrieval/prompt/generation error attribution")
    gold = gold_for([q["expect"]["ids"][0] for q in queries])
    inputs, outputs = [], []
    for q in queries:
        gid = q["expect"]["ids"][0]
        inputs.append({"id": q["id"], "query": q["query"], "role": q["filters"]["role"]})
        outputs.append({"gold_id": gid, "gold_question": gold[gid]["question"], "gold_key_points": gold[gid]["key_points"]})
    client.create_examples(inputs=inputs, outputs=outputs, dataset_id=ds.id)


# ---- variants ----------------------------------------------------------------------------

def build_collection(variant: str) -> int:
    """Non-destructive: a second collection per variant, built from the seed files."""
    name = V2_COLLECTION if variant == "keypoints" else V3_COLLECTION
    stores.delete_collection(name)
    chunks, vectors_text = [], []
    for path in sorted((Path("data") / "role_kb").glob("*.json")):
        for q in json.loads(path.read_text(encoding="utf-8"))["questions"]:
            c = role_question_chunk(q)
            kp = " ".join(q.get("key_points", []))
            if variant == "keypoints":
                c.text = (q["question"] + " " + kp).strip()
                vectors_text.append(c.embed_text())
            else:  # kp_dense: stored text unchanged, only the embedded text is extended
                vectors_text.append(f"{c.header}\n{q['question']} {kp}".strip())
            chunks.append(c)
    n = stores.upsert_chunks(name, chunks, embeddings=stores.embed_texts(vectors_text))
    bm25.invalidate(name)
    return n


def collection_of(variant: str) -> str | None:
    return {"baseline": None, "keypoints": V2_COLLECTION, "kp_dense": V3_COLLECTION}[variant]


def search(variant: str, query: str, role: str) -> list[RetrievedChunk]:
    if variant == "baseline":
        return retriever.search_role(query, role=role)
    cfg = retriever.RetrievalConfig.default(rerank=False, dense_weight=get_settings().role_dense_weight)
    return retriever.search_collection(
        collection_of(variant), query, kb="role", where=stores.where_all(role=role), partition={"role": role},
        post_filters={}, cfg=cfg,
    )


# ---- the traced pipeline -----------------------------------------------------------------

def _tokens(text: str) -> set[str]:
    return set(bm25.tokenize(text))


def coverage(items: list[dict], gold_points: list[str]) -> float:
    """Share of gold key points that some generated key point overlaps with (token Jaccard >= 0.25)."""
    produced = [_tokens(k) for it in items for k in it["key_points"]]
    if not gold_points:
        return 0.0
    hit = 0
    for g in gold_points:
        gt = _tokens(g)
        if gt and any(len(gt & p) / len(gt | p) >= 0.25 for p in produced if p):
            hit += 1
    return hit / len(gold_points)


def rank_of(gold_id: str, chunks: list[RetrievedChunk]) -> int | None:
    ids = [c.chunk_id for c in chunks]
    return ids.index(gold_id) + 1 if gold_id in ids else None


def make_target(variant: str, generate: str):
    @traceable(name="retrieve", run_type="retriever")
    def retrieve(query: str, role: str, k: int) -> list[RetrievedChunk]:
        cfg_k = retriever.RetrievalConfig.default().final_k
        return search(variant, query, role)[: max(k, cfg_k)]

    def target(inputs: dict) -> dict:
        chunks = retrieve(inputs["query"], inputs["role"], 20)
        gold_id = inputs.get("gold_id") or ""
        ctx_k = get_settings().retrieval_final_k
        context = chunks[:ctx_k]
        rank = rank_of(gold_id, chunks) if gold_id else None
        out: dict = {
            "retrieved": [{"id": c.chunk_id, "score": round(c.score, 3)} for c in chunks[:10]],
            "rank": rank, "context_k": ctx_k,
        }
        plan = PrepPlan(intent="role_questions", mode="question_set", role=inputs["role"], count=3,
                        topics=[inputs["query"][:100]], kbs=["role"])
        state = {"prep_plan": plan, "chunks": context, "retrieval_notes": []}
        prompt = gen_node.build_prompt(state)
        out["prompt_chars"] = len(prompt)
        out["prompt_has_gold_id"] = gold_id in prompt
        missed = rank is None or rank > MISS_AT
        every_sixth = int(re.sub(r"\D", "", inputs["id"]) or 0) % 6 == 0  # a small, fixed sample of hits
        want = generate == "all" or (generate in ("misses", "sample") and missed) or (generate == "sample" and every_sixth)
        if want:
            try:
                result = call_llm(lambda: gen_node.generate(state))
                items = [i.model_dump() for i in result["output"].items]
                out["items"] = items
                if items:
                    verdict = call_llm(
                        lambda: get_chat_model(0).with_structured_output(GroundingVerdict).ainvoke(
                            [("system", GROUNDING_SYSTEM), ("human", check_grounding._judge_input(result["output"].items, {c.chunk_id: c for c in context}))]
                        )
                    )
                    out["judge_ok"], out["judge_issues"] = verdict.ok, verdict.issues
            except Exception as e:  # noqa: BLE001 - keep the retrieval and prompt results; mark the generation stage unavailable
                out["gen_error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return out

    return target


# ---- evaluators --------------------------------------------------------------------------

def stage_of(rank: int | None, ctx_k: int, prompt_has_gold: bool, generated: bool, covered: float | None, cited_gold: bool,
             gen_failed: bool = False) -> str:
    if rank is None or rank > ctx_k:
        return "retrieval_miss"
    if not prompt_has_gold:
        return "prompt_drop"
    if gen_failed:
        return "generation_unavailable"  # the model call itself failed (rate limit, API error): not evidence about the answer
    if generated and (covered or 0) < 0.5 and not cited_gold:
        return "generation_error"
    return "ranking_near_miss" if rank > MISS_AT else "ok"


def evaluators_for():
    def with_gold(run, example):
        o, ref = run.outputs or {}, example.outputs or {}
        rank = o.get("rank")
        return o, ref, rank

    def rank_eval(run, example):
        o, ref, _ = with_gold(run, example)
        gold = ref["gold_id"]
        ids = [r["id"] for r in o.get("retrieved", [])]
        rank = ids.index(gold) + 1 if gold in ids else None
        return {"results": [
            {"key": "gold_rank", "score": float(rank) if rank else 21.0},
            {"key": "hit@5", "score": float(bool(rank and rank <= MISS_AT))},
            {"key": "hit@context", "score": float(bool(rank and rank <= o.get("context_k", 8)))},
        ]}

    def prompt_eval(run, example):
        return {"key": "prompt_contains_gold", "score": float(bool((run.outputs or {}).get("prompt_has_gold_id")))}

    def answer_eval(run, example):
        o, ref, _ = with_gold(run, example)
        if "items" not in o:
            return {"results": []}
        cov = coverage(o["items"], ref["gold_key_points"])
        cited = any(ref["gold_id"] in it["citations"] for it in o["items"])
        return {"results": [{"key": "answer_covers_gold", "score": cov}, {"key": "answer_cites_gold", "score": float(cited)}]}

    def faithful_eval(run, example):
        o = run.outputs or {}
        if "judge_ok" not in o:
            return {"results": []}
        return {"key": "faithful_to_context", "score": float(o["judge_ok"]), "comment": "; ".join(o.get("judge_issues", []))[:300]}

    def stage_eval(run, example):
        o, ref, _ = with_gold(run, example)
        ids = [r["id"] for r in o.get("retrieved", [])]
        rank = ids.index(ref["gold_id"]) + 1 if ref["gold_id"] in ids else None
        generated = "items" in o
        cov = coverage(o["items"], ref["gold_key_points"]) if generated else None
        cited = generated and any(ref["gold_id"] in it["citations"] for it in o["items"])
        stage = stage_of(rank, o.get("context_k", 8), bool(o.get("prompt_has_gold_id")), generated, cov, bool(cited), "gen_error" in o)
        return {"key": "failure_stage", "value": stage, "score": float(stage == "ok"), "comment": f"gold rank {rank}"}

    return [rank_eval, prompt_eval, answer_eval, faithful_eval, stage_eval]


def summary_evaluator(runs, examples):
    stages: Counter = Counter()
    hit5 = ctx = 0
    for run, ex in zip(runs, examples, strict=True):
        o, ref = run.outputs or {}, ex.outputs or {}
        ids = [r["id"] for r in o.get("retrieved", [])]
        rank = ids.index(ref["gold_id"]) + 1 if ref["gold_id"] in ids else None
        hit5 += bool(rank and rank <= MISS_AT)
        ctx += bool(rank and rank <= o.get("context_k", 8))
        generated = "items" in o
        cov = coverage(o["items"], ref["gold_key_points"]) if generated else None
        cited = generated and any(ref["gold_id"] in it["citations"] for it in o["items"])
        stages[stage_of(rank, o.get("context_k", 8), bool(o.get("prompt_has_gold_id")), generated, cov, bool(cited), "gen_error" in o)] += 1
    n = max(len(runs), 1)
    results = [{"key": "hit@5_rate", "score": hit5 / n}, {"key": "hit@context_rate", "score": ctx / n}]
    results += [{"key": f"stage_{s}", "score": float(stages.get(s, 0))} for s in STAGES]
    return {"results": results}


# ---- run ---------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", choices=["baseline", "keypoints", "kp_dense"], default="baseline")
    ap.add_argument("--generate", choices=["none", "misses", "sample", "all"], default="sample")
    ap.add_argument("--limit", type=int, default=0, help="only the first N examples (0 = all 49)")
    ap.add_argument("--ids", default="", help="comma separated example ids to run (e.g. r08,r11)")
    ap.add_argument("--tag", default="", help="suffix for the experiment name")
    ap.add_argument("--refresh-dataset", action="store_true")
    ap.add_argument("--rebuild-index", action="store_true")
    a = ap.parse_args()
    client = Client()
    ensure_dataset(client, a.refresh_dataset)
    if a.variant != "baseline" and (a.rebuild_index or stores.count(collection_of(a.variant)) == 0):
        print("built", collection_of(a.variant), "with", build_collection(a.variant), "records")

    def data_with_gold():
        for ex in client.list_examples(dataset_name=DATASET, limit=a.limit or None):
            yield ex

    examples = list(data_with_gold())
    if a.ids:
        wanted = {i.strip() for i in a.ids.split(",") if i.strip()}
        examples = [e for e in examples if e.inputs["id"] in wanted]

    def target_with_gold(inputs: dict) -> dict:
        ex = next(e for e in examples if e.inputs["id"] == inputs["id"])
        return make_target(a.variant, a.generate)({**inputs, "gold_id": ex.outputs["gold_id"]})

    res = evaluate(
        target_with_gold, data=examples, evaluators=evaluators_for(), summary_evaluators=[summary_evaluator],
        experiment_prefix="role-rag-" + a.variant + ("-" + a.tag if a.tag else ""), max_concurrency=1,
        metadata={"variant": a.variant, "generate": a.generate, "dense_weight": get_settings().role_dense_weight},
    )
    rows = list(res)
    stages: Counter = Counter()
    for r in rows:
        for fb in r["evaluation_results"]["results"]:
            if fb.key == "failure_stage":
                stages[fb.value] += 1
    print(f"\nexperiment: {res.experiment_name}  examples: {len(rows)}")
    for s in STAGES:
        print(f"  {s:20}{stages.get(s, 0):>4}")
    for r in rows:
        stage = next((fb.value for fb in r["evaluation_results"]["results"] if fb.key == "failure_stage"), "?")
        if stage != "ok":
            print(f"   {stage:18} {r['example'].inputs['id']}: {r['example'].inputs['query'][:70]}")


if __name__ == "__main__":
    main()
