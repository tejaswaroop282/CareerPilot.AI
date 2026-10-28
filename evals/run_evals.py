"""Run the evaluation suite (spec 11.2) and print every metric, the ablation tables and a before/after comparison.

Usage:
  python -m evals.run_evals --offline                      # no API calls: job ranking (rules parser) and isolation tests
  python -m evals.run_evals                                # everything (needs a working Gemini key and quota)
  python -m evals.run_evals --only retrieval,plan,agent    # selected sections
  python -m evals.run_evals --save evals/results/after.json --compare evals/results/before.json

Sections: retrieval, jobs, isolation, plan, agent, generation, hitl, agreement, injection.
A section that fails (for example on API quota) records its error and the run continues.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

OFFLINE = ["jobs", "isolation"]
ALL = ["retrieval", "jobs", "isolation", "plan", "agent", "generation", "hitl", "agreement", "injection"]


def isolation() -> dict:
    """Cross-user and cross-track isolation must pass at 100 percent (pytest, offline)."""
    proc = subprocess.run([sys.executable, "-m", "pytest", "tests/test_isolation.py", "-q", "-p", "no:cacheprovider"],
                          capture_output=True, text=True)
    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
    print(f"\nIsolation tests: {tail}")
    return {"passed": proc.returncode == 0, "summary": tail, "pass_rate": 1.0 if proc.returncode == 0 else 0.0}


def section(name: str, live: bool):
    if name == "retrieval":
        from evals import retrieval_eval

        return retrieval_eval.run()
    if name == "jobs":
        from evals import job_eval

        return job_eval.run(live=live)
    if name == "isolation":
        return isolation()
    if name == "plan":
        from evals import plan_eval

        return plan_eval.run()
    if name == "agent":
        from evals import agent_eval

        return agent_eval.run()
    if name == "generation":
        from evals import generation_eval

        return generation_eval.run()
    if name == "hitl":
        from evals import hitl_eval

        return hitl_eval.run()
    if name == "agreement":
        from evals import agreement_eval

        return agreement_eval.run()
    if name == "injection":
        from evals import injection_eval

        return injection_eval.run()
    raise SystemExit(f"unknown section {name}")


def flatten(d, prefix="") -> dict[str, float]:
    out: dict[str, float] = {}
    if isinstance(d, dict):
        for k, v in d.items():
            out.update(flatten(v, f"{prefix}{k}."))
    elif isinstance(d, (int, float)) and not isinstance(d, bool):
        out[prefix.rstrip(".")] = d
    return out


def compare(before: dict, after: dict) -> None:
    b, a = flatten(before), flatten(after)
    rows = [(k, b[k], a[k]) for k in a if k in b and b[k] != a[k]]
    print("\nBefore / after")
    if not rows:
        print("  no metric changed")
    for k, x, y in rows:
        print(f"  {k:70s} {x:>8} -> {y:>8}  ({y - x:+.3f})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true", help="only evals that need no API calls")
    ap.add_argument("--only", help="comma list of sections")
    ap.add_argument("--save", help="write results JSON here")
    ap.add_argument("--compare", help="a previous results JSON to compare against")
    args = ap.parse_args()
    names = args.only.split(",") if args.only else (OFFLINE if args.offline else ALL)
    results: dict = {}
    for name in names:
        try:
            results[name] = section(name, live=not args.offline)
        except Exception as e:  # noqa: BLE001 - a quota error in one eval must not hide the others
            print(f"\n[{name}] skipped: {type(e).__name__}: {str(e)[:160]}")
            results[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    if args.save:
        Path(args.save).parent.mkdir(parents=True, exist_ok=True)
        Path(args.save).write_text(json.dumps(results, indent=2, default=str))
        print("saved", args.save)
    if args.compare and Path(args.compare).exists():
        compare(json.loads(Path(args.compare).read_text()), json.loads(json.dumps(results, default=str)))


if __name__ == "__main__":
    main()
