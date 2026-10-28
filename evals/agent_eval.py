"""Retrieval agent vs the fixed pipeline, and agent trace quality (live LLM; each agent eval runs several times).

Metrics: hit@k on labelled role queries, tool calls per run, latency, how often the agent stops by finishing vs a step
cap or no-progress, fallback rate, redundant calls, and (no-data cases) how often the run says there is no data.
"""
import asyncio
import json
import os
import time
from pathlib import Path

from app.graph.agents.retrieval import fixed_pipeline, quotas_for, run_retrieval
from app.graph.state import PrepPlan
from app.rag import retriever
from evals.common import mean_std, new_run_ids, print_table

DATASETS = Path(__file__).parent / "datasets"
NO_DATA = [("Prepare me for a Globex SDE interview", "globex"), ("Prep me for a Initech software role", "initech"),
           ("Help me get ready for Hooli", "hooli")]


def role_plan(q: dict, count: int = 5) -> PrepPlan:
    return PrepPlan(intent="role_questions", mode="question_set", role=q["filters"]["role"], count=count, kbs=["role"], topics=[])


def hit(chunks, expect_ids: list[str], k: int) -> bool:
    return any(c.chunk_id in expect_ids for c in chunks[:k])


async def run_async(runs: int = 3) -> dict:
    queries = json.loads((DATASETS / "retrieval_queries.json").read_text())
    # The full set is about 600 model calls, far beyond a free tier: AGENT_EVAL_LIMIT=N takes every k-th query (spread over
    # all knowledge bases) and AGENT_EVAL_RUNS the repetitions.
    limit = int(os.environ.get("AGENT_EVAL_LIMIT", "0") or 0)
    if limit and limit < len(queries):
        step = len(queries) / limit
        queries = [queries[int(i * step)] for i in range(limit)]
    runs = int(os.environ.get("AGENT_EVAL_RUNS", "0") or runs)
    uid, tid = new_run_ids()
    agent = {"hit@3": [], "calls": [], "latency_s": [], "finished": [], "fallback": [], "redundant": []}
    fixed = {"hit@3": [], "latency_s": []}
    stops: dict[str, int] = {}
    for q in queries:
        plan = role_plan(q)
        ids = q["expect"]["ids"]
        t0 = time.perf_counter()
        per_kb = await fixed_pipeline(plan, user_id=uid, track_id=tid, request=q["query"])
        chunks = retriever.select_context(per_kb, quotas_for(plan))
        fixed["latency_s"].append(time.perf_counter() - t0)
        fixed["hit@3"].append(float(hit(chunks, ids, 3)))
        for _ in range(runs):
            t0 = time.perf_counter()
            chunks, trace, _ = await run_retrieval(plan=plan, request=q["query"], user_id=uid, track_id=tid)
            agent["latency_s"].append(time.perf_counter() - t0)
            agent["hit@3"].append(float(hit(chunks, ids, 3)))
            calls = [s for s in trace if s.tool not in ("finish", "fixed_pipeline")]
            agent["calls"].append(len(calls))
            sigs = [(s.tool, json.dumps(s.args, sort_keys=True)) for s in calls]
            agent["redundant"].append(len(sigs) - len(set(sigs)))
            fell_back = bool(trace) and trace[-1].tool == "fixed_pipeline"
            agent["fallback"].append(float(fell_back))
            agent["finished"].append(float(not fell_back))
            stops["fallback" if fell_back else "finish"] = stops.get("fallback" if fell_back else "finish", 0) + 1
    # no-data cases: the company is not in the manifest, so the agent must say so and invent nothing
    said_no_data, invented = [], []
    for request, company in NO_DATA:
        plan = PrepPlan(intent="company_prep", mode="question_set", role="sde", company=company, count=5, kbs=["role"])
        for _ in range(runs):
            chunks, trace, notes = await run_retrieval(plan=plan, request=request, user_id=uid, track_id=tid)
            said_no_data.append(float(any("no company" in n.lower() for n in notes)))
            invented.append(float(any(c.kb == "company" for c in chunks)))
    rows = {k: str(mean_std(v)) for k, v in agent.items()}
    return {
        "agent": rows,
        "fixed": {k: str(mean_std(v)) for k, v in fixed.items()},
        "stop_reasons": stops,
        "no_data": {"says_no_data": mean_std(said_no_data)[0], "invents_company_chunks": mean_std(invented)[0]},
    }


def run(runs: int = 3) -> dict:
    out = asyncio.run(run_async(runs))
    print_table("Retrieval: agent vs fixed pipeline (mean ±spread)", {"agent": out["agent"], "fixed pipeline": out["fixed"]})
    print("trace quality, stop reasons:", out["stop_reasons"], "| no-data cases:", out["no_data"])
    return out
