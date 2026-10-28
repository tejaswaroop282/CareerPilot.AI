"""Job evals (spec 11.2): job-match ranking (precision@3, NDCG@3) and job agent vs a single profile query.

`rules` parser reads the Required / Nice to have sentences of the sample dataset (offline, deterministic baseline);
`llm` parser is the production path (structured output). The score itself is always the code in rank_jobs.py.
"""
import asyncio
import json
import re
import time
from pathlib import Path

from app.graph.nodes.rank_jobs import JobAnalysis, norm_skill, score_from_skills
from app.graph.state import Profile
from app.jobs import provider
from evals.common import mean_std, ndcg_at_k, print_table

DATASETS = Path(__file__).parent / "datasets"


def rules_parse(job: dict) -> JobAnalysis:
    d = job["description"]
    req = re.search(r"Required: (.+?)\. ", d)
    nice = re.search(r"Nice to have: (.+?)\.", d)
    split = lambda m: [x.strip() for x in re.split(r",| or ", m.group(1)) if x.strip()] if m else []  # noqa: E731
    return JobAnalysis(required_skills=split(req), nice_to_have=split(nice), fit_note=job["title"])


async def llm_parse(job: dict) -> JobAnalysis:
    from app.graph.helpers.pii import strip_pii
    from app.graph.prompts_jobs import PARSE_JOB_SYSTEM
    from app.rag.llm import get_chat_model

    text = strip_pii(f"{job['title']}\n{job['description']}")
    llm = get_chat_model(0).with_structured_output(JobAnalysis)
    return await llm.ainvoke([("system", PARSE_JOB_SYSTEM), ("human", f"<job_posting>\n{text}\n</job_posting>")])


async def ranking_eval(parser: str = "rules") -> dict:
    data = json.loads((DATASETS / "job_ranking.json").read_text())
    p3, nd, n_pairs = [], [], 0
    for prof in data:
        skills = {norm_skill(s) for s in prof["skills"]}
        scored = []
        for j in prof["jobs"]:
            job = provider.get_job(j["job_id"])
            analysis = rules_parse(job) if parser == "rules" else await llm_parse(job)
            scored.append((score_from_skills(analysis, skills, "").score, j["job_id"], int(j["relevant"])))
            n_pairs += 1
        scored.sort(key=lambda x: (-x[0], x[1]))
        rel = [r for _, _, r in scored]
        p3.append(sum(rel[:3]) / 3)
        nd.append(ndcg_at_k(rel, 3))
    return {"parser": parser, "pairs": n_pairs, "precision@3": mean_std(p3)[0], "ndcg@3": mean_std(nd)[0]}


async def job_agent_eval(runs: int = 3) -> dict:
    """Job agent (live LLM) vs one query built from the profile. Relevant jobs are the labelled ones per profile."""
    from app.graph.agents.job_search import profile_query, run_job_search
    from evals.common import new_run_ids

    data = json.loads((DATASETS / "job_ranking.json").read_text())
    agent = {"recall": [], "dups": [], "calls": [], "latency": [], "tracked_skipped": []}
    single = {"recall": [], "dups": [], "calls": [], "latency": []}
    stops: dict[str, int] = {}
    for prof in data:
        relevant = {j["job_id"] for j in prof["jobs"] if j["relevant"]}
        profile = Profile(skills=prof["skills"])
        uid, tid = new_run_ids()
        tracked_id = sorted(relevant)[0]
        for _ in range(runs):
            t0 = time.perf_counter()
            jobs, trace, _ = await run_job_search(profile=profile, role="software engineer", user_id=uid, track_id=tid, tracked={tracked_id})
            agent["latency"].append(time.perf_counter() - t0)
            ids = [j["id"] for j in jobs]
            agent["recall"].append(len(relevant - {tracked_id} & set(ids)) / max(len(relevant - {tracked_id}), 1))
            agent["dups"].append(len(ids) - len(set(ids)))
            agent["calls"].append(len([s for s in trace if s.tool not in ("finish",)]))
            agent["tracked_skipped"].append(tracked_id not in ids)
            last = trace[-1].tool if trace else "none"
            key = "fallback" if last == "single_query_fallback" else "finish"
            stops[key] = stops.get(key, 0) + 1
        t0 = time.perf_counter()
        found = provider.search_jobs(profile_query(profile, "software engineer"), None, 10)
        single["latency"].append(time.perf_counter() - t0)
        ids = [j["id"] for j in found]
        single["recall"].append(len(relevant & set(ids)) / len(relevant))
        single["dups"].append(len(ids) - len(set(ids)))
        single["calls"].append(1)
    row = lambda d: {k: str(mean_std([float(x) for x in v])) for k, v in d.items()}  # noqa: E731
    return {"agent": row(agent), "single_query": row(single), "agent_stop_reasons": stops}


def run(live: bool = False) -> dict:
    out = {"ranking_rules": asyncio.run(ranking_eval("rules"))}
    print_table("Job-match ranking (precision@3, NDCG@3)", {"rules parser": {k: v for k, v in out["ranking_rules"].items() if k != "parser"}})
    if live:
        out["ranking_llm"] = asyncio.run(ranking_eval("llm"))
        print_table("Job-match ranking, LLM parser", {"llm parser": {k: v for k, v in out["ranking_llm"].items() if k != "parser"}})
        out["agent"] = asyncio.run(job_agent_eval())
        print_table("Job agent vs single query (mean, spread over 3 runs)", {"agent": out["agent"]["agent"], "single query": out["agent"]["single_query"]})
        print("agent stop reasons:", out["agent"]["agent_stop_reasons"])
    return out
