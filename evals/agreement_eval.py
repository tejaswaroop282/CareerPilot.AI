"""Evaluator agreement (live LLM): does evaluate_answer score like a person?

evals/datasets/evaluator_answers.json holds 30 answers (10 questions x good / partial / poor). `human_score` (0-10) is
left empty on purpose: fill it with your own scores to get the model-vs-human correlation. Until then the eval reports
the correlation with the intended quality order (reference_rank), which checks that the evaluator ranks answers sensibly.
"""
import asyncio
import json
from pathlib import Path

from app.graph.nodes.evaluate_answer import evaluate_answer
from app.graph.nodes.report import item_score
from app.graph.state import PrepItem, PrepPlan
from evals.common import print_table, spearman

DATASETS = Path(__file__).parent / "datasets"


async def run_async() -> dict:
    cases = json.loads((DATASETS / "evaluator_answers.json").read_text())
    plan = PrepPlan(intent="role_questions", mode="mock_interview", role="sde", count=1)
    model, human, ref = [], [], []
    for c in cases:
        item = PrepItem(question=c["question"], key_points=c["key_points"], kb="role", citations=["x"])
        upd = await evaluate_answer({"current_item": item, "prep_plan": plan, "answer": c["answer"]})
        s = upd["scores"][-1]
        model.append(item_score(s))
        ref.append(float(c["reference_rank"]))
        human.append(c["human_score"])
    labelled = [(m, h) for m, h in zip(model, human) if h is not None]
    out = {
        "answers": len(cases),
        "spearman_vs_reference_order": spearman(model, ref),
        "human_labelled": len(labelled),
        "spearman_vs_human": spearman([m for m, _ in labelled], [h for _, h in labelled]) if len(labelled) >= 10 else "add human_score values",
    }
    return out


def run() -> dict:
    out = asyncio.run(run_async())
    print_table("Evaluator agreement", {"evaluate_answer": out})
    return out
