"""Latency and load check that never calls an LLM (no cost): HTTP endpoints under concurrency, retrieval and job search.

Creates a throwaway user and track, drives read endpoints, then deletes everything it created.
Usage (inside the backend container): python -m scripts.load_test [--requests 200] [--concurrency 10] [--base http://localhost:8000]
"""
import argparse
import asyncio
import statistics
import time
import uuid

import httpx

from app.db.client import close_client, get_db


def pct(values: list[float], p: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(p / 100 * (len(s) - 1))))]


async def hammer(client: httpx.AsyncClient, method: str, path: str, n: int, conc: int, headers: dict | None = None) -> dict:
    sem = asyncio.Semaphore(conc)
    lat: list[float] = []
    errors = 0

    async def one() -> None:
        nonlocal errors
        async with sem:
            t = time.perf_counter()
            try:
                r = await client.request(method, path, headers=headers)
                if r.status_code >= 400:
                    errors += 1
            except httpx.HTTPError:
                errors += 1
            lat.append((time.perf_counter() - t) * 1000)

    start = time.perf_counter()
    await asyncio.gather(*[one() for _ in range(n)])
    wall = time.perf_counter() - start
    return {"path": f"{method} {path}", "n": n, "errors": errors, "p50": pct(lat, 50), "p95": pct(lat, 95),
            "max": max(lat), "rps": n / wall}


def retrieval_latency() -> list[tuple[str, float, float]]:
    from app.rag import retriever

    cases = {
        "role (hybrid, no rerank)": lambda q: retriever.search_role(q, role="sde"),
        "company (hybrid + rerank)": lambda q: retriever.search_company(q, company="amazon", role="sde"),
    }
    queries = ["what does the process control block store", "how does tcp finish a connection", "two sum",
               "design a rate limiter", "what is a deadlock", "serialize a binary tree"]
    out = []
    for name, fn in cases.items():
        fn(queries[0])  # warm-up: model load and index build are one-time costs
        times = []
        for q in queries:
            t = time.perf_counter()
            fn(q)
            times.append((time.perf_counter() - t) * 1000)
        out.append((name, statistics.median(times), max(times)))
    return out


def job_search_latency() -> list[tuple[str, float, int]]:
    from app.jobs import provider

    out = []
    for label in ("cold (live sites)", "warm (cached)"):
        t = time.perf_counter()
        jobs = provider.search_jobs("python developer", "India", 25)
        out.append((label, (time.perf_counter() - t) * 1000, len(jobs)))
    return out


async def main(base: str, n: int, conc: int) -> None:
    email = f"load-{uuid.uuid4().hex[:8]}@example.com"
    async with httpx.AsyncClient(base_url=base, timeout=30) as client:
        r = await client.post("/auth/register", json={"email": email, "password": "correct-horse-1"})
        r.raise_for_status()
        auth = {"Authorization": f"Bearer {r.json()['access_token']}"}
        uid = r.json()["user"]["id"]
        tid = (await client.post("/tracks", json={"name": "load", "target_role": "sde"}, headers=auth)).json()["id"]
        try:
            rows = [
                await hammer(client, "GET", "/health", n, conc),
                await hammer(client, "GET", "/tracks", n, conc, auth),
                await hammer(client, "GET", f"/tracks/{tid}/jobs", n, conc, auth),
                await hammer(client, "GET", "/kb/roles", n, conc, auth),
                await hammer(client, "GET", "/kb/companies", n, conc, auth),
            ]
        finally:
            await client.delete(f"/tracks/{tid}", headers=auth)
            await get_db().users.delete_one({"_id": uid})
    print(f"\nHTTP endpoints: {n} requests each, concurrency {conc}")
    print(f"{'endpoint':32}{'errors':>8}{'p50 ms':>9}{'p95 ms':>9}{'max ms':>9}{'req/s':>9}")
    for x in rows:
        print(f"{x['path']:32}{x['errors']:>8}{x['p50']:>9.1f}{x['p95']:>9.1f}{x['max']:>9.1f}{x['rps']:>9.1f}")
    print("\nRetrieval latency (local embeddings, median and max over 6 queries, after warm-up)")
    for name, med, mx in await asyncio.to_thread(retrieval_latency):
        print(f"  {name:30}{med:>9.0f} ms median{mx:>9.0f} ms max")
    print("\nJob search latency (5 free sources, merged)")
    for label, ms, found in await asyncio.to_thread(job_search_latency):
        print(f"  {label:22}{ms:>9.0f} ms  {found} jobs")
    await close_client()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--base", default="http://localhost:8000")
    a = ap.parse_args()
    asyncio.run(main(a.base, a.requests, a.concurrency))
