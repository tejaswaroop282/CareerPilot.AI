"""Latency and token report from LangSmith traces: which graph node and which model call cost the most?

Pulls the project's runs, attributes every LLM call to the graph node it ran under (plan_prep, score_job, generate, ...),
and prints per node: LLM calls, tokens in and out, share of all tokens, p50 and p95 latency of the node itself.
Tokens are the honest unit on a free tier; dollar cost is shown only when LLM_PRICE_IN_PER_M / LLM_PRICE_OUT_PER_M are set.

Usage: python -m scripts.langsmith_cost_report [--hours 24] [--flow prep|discover|ingest] [--project careerpilot] [--save out.json]
"""
import argparse
import json
import os
import itertools
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from langsmith import Client

NODES = {
    "plan_prep", "confirm_plan", "retrieve", "generate", "check_grounding", "await_answer", "evaluate_answer", "report",
    "search_jobs", "score_job", "rank_jobs", "build_profile", "parse_resume", "ingest_github", "index_chunks", "chunk_and_embed",
    "agent", "tools",
}


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))] if s else 0.0


def seconds(run) -> float | None:
    return (run.end_time - run.start_time).total_seconds() if run.end_time and run.start_time else None


def node_of(run, by_id: dict) -> str:
    """Walk up the trace to the nearest graph node (the agent loop's own nodes are folded into the node that hosts it)."""
    cur, seen, host = run, 0, None
    while cur is not None and seen < 12:
        if cur.name in NODES:
            if cur.name in ("agent", "tools"):
                host = host or cur.name  # remember the loop, keep climbing to the hosting node
            else:
                return f"{cur.name} (agent loop)" if host else cur.name
        cur = by_id.get(cur.parent_run_id)
        seen += 1
    return host or "other"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=24)
    ap.add_argument("--flow", default="", help="only traces tagged flow:<name>")
    ap.add_argument("--project", default=os.environ.get("LANGSMITH_PROJECT", "careerpilot"))
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--save", default="")
    a = ap.parse_args()
    client = Client()
    since = datetime.now(timezone.utc) - timedelta(hours=a.hours)
    runs = list(itertools.islice(client.list_runs(project_name=a.project, start_time=since), a.limit))  # the API pages by 100
    if a.flow:  # keep only traces whose root run carries the flow tag
        roots = {r.trace_id for r in runs if r.parent_run_id is None and f"flow:{a.flow}" in (r.tags or [])}
        runs = [r for r in runs if r.trace_id in roots]
    by_id = {r.id: r for r in runs}
    llm = [r for r in runs if r.run_type == "llm"]
    price_in, price_out = os.environ.get("LLM_PRICE_IN_PER_M"), os.environ.get("LLM_PRICE_OUT_PER_M")

    stats: dict[str, dict] = defaultdict(lambda: {"calls": 0, "tin": 0, "tout": 0, "errors": 0, "llm_s": []})
    for r in llm:
        s = stats[node_of(r, by_id)]
        s["calls"] += 1
        s["tin"] += r.prompt_tokens or 0
        s["tout"] += r.completion_tokens or 0
        s["errors"] += bool(r.error)
        if (d := seconds(r)) is not None:
            s["llm_s"].append(d)
    node_s: dict[str, list[float]] = defaultdict(list)
    for r in runs:
        if r.name in NODES and (d := seconds(r)) is not None and r.name not in ("agent", "tools"):
            node_s[r.name].append(d)

    total = sum(s["tin"] + s["tout"] for s in stats.values()) or 1
    roots = [r for r in runs if r.parent_run_id is None]
    print(f"project {a.project}: {len(runs)} runs in the last {a.hours:g} h, {len(roots)} traces, {len(llm)} LLM calls, {total} tokens")
    head = f"{'node':28}{'LLM calls':>10}{'tokens in':>11}{'tokens out':>11}{'share':>7}{'LLM p50 s':>10}{'LLM p95 s':>10}{'node p50 s':>11}{'errors':>7}"
    print(head)
    rows = []
    for name, s in sorted(stats.items(), key=lambda kv: -(kv[1]["tin"] + kv[1]["tout"])):
        base = name.split(" (")[0]
        row = {"node": name, "calls": s["calls"], "tokens_in": s["tin"], "tokens_out": s["tout"],
               "share": (s["tin"] + s["tout"]) / total, "llm_p50_s": pct(s["llm_s"], 50), "llm_p95_s": pct(s["llm_s"], 95),
               "node_p50_s": statistics.median(node_s[base]) if node_s.get(base) else 0.0, "errors": s["errors"]}
        rows.append(row)
        print(f"{name:28}{row['calls']:>10}{row['tokens_in']:>11}{row['tokens_out']:>11}{row['share']:>7.0%}"
              f"{row['llm_p50_s']:>10.1f}{row['llm_p95_s']:>10.1f}{row['node_p50_s']:>11.1f}{row['errors']:>7}")
    if price_in and price_out:
        cost = sum(s["tin"] for s in stats.values()) / 1e6 * float(price_in) + sum(s["tout"] for s in stats.values()) / 1e6 * float(price_out)
        print(f"\nestimated cost at the configured prices: ${cost:.4f}")
    root_s = [d for r in roots if (d := seconds(r)) is not None]
    if root_s:
        print(f"\nend-to-end traces: p50 {pct(root_s, 50):.1f} s, p95 {pct(root_s, 95):.1f} s, max {max(root_s):.1f} s")
    if a.save:
        with open(a.save, "w", encoding="utf-8") as f:
            json.dump({"runs": len(runs), "traces": len(roots), "tokens": total, "nodes": rows}, f, indent=1)


if __name__ == "__main__":
    main()
