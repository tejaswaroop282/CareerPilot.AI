"""Issue 2: detect unsupported (hallucinated) answers, and prove the detector works.

Retrieval metrics say whether the right documents were found, not whether an answer stays within them. This experiment
measures the grounding judge (the guardrail in graph/nodes/check_grounding.py) as a classifier, in LangSmith:

  supported examples    a curated record shown as an item citing itself (must NOT be flagged)
  seeded hallucinations the same kind of item, corrupted in one of three ways (must be flagged):
      foreign_claim     a key point taken from an unrelated record
      wrong_citation    a correct item citing an unrelated chunk
      invented_stat     a fabricated statistic appended to a key point

Per example the target runs the programmatic citation check and the LLM judge; evaluators score detection, and summary
evaluators report precision, recall, F1, false-positive rate and recall per corruption type. Judge prompts are compared as
separate experiments (--prompt baseline|strict). One model call per example (Groq free tier: keep the set small).

Usage: python -m evals.langsmith_groundedness [--prompt baseline|strict] [--per-kind 6] [--positives 12] [--refresh-dataset]
"""
import argparse
import asyncio
import json
import random
from collections import defaultdict
from pathlib import Path

from langsmith import Client, evaluate

from app.graph.nodes import check_grounding
from app.graph.prompts import GROUNDING_SYSTEM
from app.graph.state import GroundingVerdict, PrepItem, RetrievedChunk
from app.rag import stores
from app.rag.llm import get_chat_model

DATASET = "careerpilot-groundedness"
KINDS = ("foreign_claim", "wrong_citation", "invented_stat")
STRICT_SUFFIX = (
    "\nBe literal. Compare every key point with the cited chunk text. A claim, number, percentage, name, date, tool or "
    "relationship that is not stated in a cited chunk is unsupported, even when it is true in general. A key point about a "
    "different topic than the cited chunk is unsupported. A question that the cited chunks do not cover is unsupported."
)


def _records(collection: str, limit: int) -> list[dict]:
    rows = stores.get_chunks(collection)
    out = []
    for r in rows:
        try:
            rec = json.loads(r["metadata"].get("record", "{}"))
        except ValueError:
            continue
        text = rec.get("question") or rec.get("text")
        if rec and text and rec.get("key_points") and len(rec["key_points"]) >= 2:
            out.append({"id": r["id"], "kb": "role" if collection == stores.ROLE_COLLECTION else "company",
                        "topic": rec.get("topic") or rec.get("round") or "", "question": text, "key_points": rec["key_points"]})
    random.Random(11).shuffle(out)
    return out[:limit]


def build_examples(positives: int, per_kind: int) -> tuple[list[dict], list[dict]]:
    role = _records(stores.ROLE_COLLECTION, 400)
    company = _records(stores.COMPANY_COLLECTION, 100)
    pool = role[:300] + company[:40]
    random.Random(5).shuffle(pool)

    def other_topic(rec: dict) -> dict:
        return next(r for r in pool if r["topic"] != rec["topic"] and r["id"] != rec["id"])

    inputs, outputs = [], []

    def add(item: dict, cited: list[str], label: str, kind: str):
        inputs.append({"question": item["question"], "key_points": item["key_points"], "kb": item["kb"], "citations": cited})
        outputs.append({"label": label, "kind": kind})

    base = pool[:positives + per_kind * 3]
    for rec in base[:positives]:
        add(rec, [rec["id"]], "supported", "supported")
    k = positives
    for kind in KINDS:
        for rec in base[k:k + per_kind]:
            item = {**rec, "key_points": list(rec["key_points"])}
            cited = [rec["id"]]
            if kind == "foreign_claim":
                item["key_points"][0] = other_topic(rec)["key_points"][0]
            elif kind == "wrong_citation":
                cited = [other_topic(rec)["id"]]
            else:
                item["key_points"][0] += " This is used by 73% of production systems according to a 2023 industry survey."
            add(item, cited, "unsupported", kind)
        k += per_kind
    return inputs, outputs


def ensure_dataset(client: Client, positives: int, per_kind: int, refresh: bool) -> None:
    if client.has_dataset(dataset_name=DATASET):
        if not refresh:
            return
        client.delete_dataset(dataset_name=DATASET)
    ds = client.create_dataset(DATASET, description="Supported items and seeded hallucinations for the grounding judge")
    inputs, outputs = build_examples(positives, per_kind)
    client.create_examples(inputs=inputs, outputs=outputs, dataset_id=ds.id)


def chunks_for(ids: list[str]) -> dict[str, RetrievedChunk]:
    out: dict[str, RetrievedChunk] = {}
    for collection, kb in ((stores.ROLE_COLLECTION, "role"), (stores.COMPANY_COLLECTION, "company")):
        for r in stores.get_by_ids(collection, ids):
            out[r["id"]] = RetrievedChunk(chunk_id=r["id"], kb=kb, text=r["text"], header=r["metadata"].get("header", ""), metadata=r["metadata"])
    return out


def make_target(prompt: str):
    system = GROUNDING_SYSTEM + (STRICT_SUFFIX if prompt == "strict" else "")

    def target(inputs: dict) -> dict:
        chunks = chunks_for(inputs["citations"])
        item = PrepItem(question=inputs["question"], key_points=inputs["key_points"], kb=inputs["kb"], citations=inputs["citations"])
        det = check_grounding.deterministic_issues([item], chunks)
        verdict = asyncio.run(
            get_chat_model(0).with_structured_output(GroundingVerdict).ainvoke(
                [("system", system), ("human", check_grounding._judge_input([item], chunks))]
            )
        )
        return {"citation_issues": [p for v in det.values() for p in v], "judge_ok": verdict.ok, "judge_issues": verdict.issues,
                "flagged": bool(det) or not verdict.ok}

    return target


def citation_eval(run, example):
    return {"key": "citation_validity", "score": float(not (run.outputs or {}).get("citation_issues"))}


def detection_eval(run, example):
    o, ref = run.outputs or {}, example.outputs or {}
    flagged = bool(o.get("flagged"))
    correct = flagged == (ref["label"] == "unsupported")
    return {"key": "detection_correct", "score": float(correct), "comment": f"{ref['kind']}: " + "; ".join(o.get("judge_issues", []))[:200]}


def summary_eval(runs, examples):
    tp = fp = fn = tn = 0
    by_kind: dict[str, list[int]] = defaultdict(list)
    for run, ex in zip(runs, examples, strict=True):
        flagged, ref = bool((run.outputs or {}).get("flagged")), ex.outputs
        unsupported = ref["label"] == "unsupported"
        tp += flagged and unsupported
        fp += flagged and not unsupported
        fn += (not flagged) and unsupported
        tn += (not flagged) and not unsupported
        if unsupported:
            by_kind[ref["kind"]].append(int(flagged))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    results = [{"key": "precision", "score": prec}, {"key": "recall", "score": rec}, {"key": "f1", "score": f1},
               {"key": "false_positive_rate", "score": fp / (fp + tn) if fp + tn else 0.0}]
    results += [{"key": f"recall_{k}", "score": sum(v) / len(v)} for k, v in by_kind.items()]
    return {"results": results}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", choices=["baseline", "strict"], default="baseline")
    ap.add_argument("--positives", type=int, default=12)
    ap.add_argument("--per-kind", type=int, default=6)
    ap.add_argument("--refresh-dataset", action="store_true")
    a = ap.parse_args()
    client = Client()
    ensure_dataset(client, a.positives, a.per_kind, a.refresh_dataset)
    res = evaluate(
        make_target(a.prompt), data=DATASET, evaluators=[citation_eval, detection_eval], summary_evaluators=[summary_eval],
        experiment_prefix=f"groundedness-{a.prompt}", max_concurrency=1, metadata={"judge_prompt": a.prompt},
    )
    rows = list(res)
    print(f"\nexperiment: {res.experiment_name}  examples: {len(rows)}")
    tp = fp = fn = tn = 0
    by_kind: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        flagged = bool(r["run"].outputs.get("flagged"))
        ref = r["example"].outputs
        unsupported = ref["label"] == "unsupported"
        tp += flagged and unsupported
        fp += flagged and not unsupported
        fn += (not flagged) and unsupported
        tn += (not flagged) and not unsupported
        if unsupported:
            by_kind[ref["kind"]].append(int(flagged))
    print(f"  caught {tp} of {tp + fn} seeded hallucinations; flagged {fp} of {fp + tn} supported items")
    for k, v in by_kind.items():
        print(f"    {k:16} recall {sum(v)}/{len(v)}")


if __name__ == "__main__":
    main()
