"""Numerical health check of a running CareerPilot stack (local or on the server). Prints a PASS/FAIL table, exits 1 on failure.

No model calls and no cost: health and latency, auth and database round trip, S3 round trip, knowledge-base counts against the
spec's acceptance bar (at least 4 roles and 3 companies), vector store counts, MCP tools, a job search and a retrieval spot check.
Usage (inside the backend container): python -m scripts.validate_stack [--base http://localhost:8000]
"""
import argparse
import asyncio
import statistics
import sys
import time
import uuid

import httpx

from app.config import get_settings, production_problems

rows: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str) -> None:
    rows.append((name, ok, detail))


def timed(fn):
    t = time.perf_counter()
    out = fn()
    return out, (time.perf_counter() - t) * 1000


def api_checks(base: str) -> None:
    lat, errors = [], 0
    with httpx.Client(base_url=base, timeout=15) as c:
        for _ in range(20):
            t = time.perf_counter()
            try:
                errors += c.get("/health").status_code != 200
            except httpx.HTTPError:
                errors += 1
            lat.append((time.perf_counter() - t) * 1000)
        check("API /health (20 requests)", errors == 0, f"{errors} errors, p50 {statistics.median(lat):.0f} ms, max {max(lat):.0f} ms")
        email = f"validate-{uuid.uuid4().hex[:8]}@example.com"
        try:
            r = c.post("/auth/register", json={"email": email, "password": "correct-horse-1"})
            ok = r.status_code == 201
            token = r.json().get("access_token", "") if ok else ""
            h = {"Authorization": f"Bearer {token}"}
            t = c.post("/tracks", json={"name": "validate", "target_role": "sde"}, headers=h)
            ok = ok and t.status_code == 201
            listing = c.get("/tracks", headers=h)
            ok = ok and listing.status_code == 200 and len(listing.json()) == 1
            other = c.get("/tracks")  # no token must be refused
            ok = ok and other.status_code in (401, 403)
            if t.status_code == 201:
                c.delete(f"/tracks/{t.json()['id']}", headers=h)
            check("auth + Mongo write/read, unauthenticated refused", ok, f"register {r.status_code}, track {t.status_code}, no-token {other.status_code}")
        finally:
            from pymongo import MongoClient

            s = get_settings()
            MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=3000)[s.mongodb_db].users.delete_many({"email": email})


def store_checks(min_roles: int = 4) -> None:
    from pymongo import MongoClient

    s = get_settings()
    db = MongoClient(s.mongodb_uri, serverSelectionTimeoutMS=4000)[s.mongodb_db]
    _, ms = timed(lambda: db.command("ping"))
    counts = {c: db[c].estimated_document_count() for c in ("users", "tracks", "sessions", "checkpoints")}
    check("MongoDB ping", True, f"{ms:.0f} ms, {counts}")

    from app.storage import s3

    key = f"healthcheck/{uuid.uuid4().hex}"
    t = time.perf_counter()
    s3.put_object(key, b"ok", "text/plain")
    back = s3.get_object(key)
    s3.delete_object(key)
    check("S3 put/get/delete round trip", back == b"ok", f"{(time.perf_counter() - t) * 1000:.0f} ms, bucket {s.s3_bucket}")

    from app.rag import manifest, stores

    roles, companies = manifest.supported_roles(), manifest.supported_companies()
    check(f"knowledge bases meet the bar ({min_roles} roles, 3 companies)", len(roles) >= min_roles and len(companies) >= 3,
          f"{len(roles)} roles {roles}, {len(companies)} companies {companies}")
    n_role, n_company = stores.count(stores.ROLE_COLLECTION), stores.count(stores.COMPANY_COLLECTION)
    check("vector store counts", n_role >= 300 and n_company >= 100, f"kb_role {n_role} records, kb_company {n_company} records")

    from app.rag import retriever

    t = time.perf_counter()
    top = retriever.search_role("what is a deadlock and how can it be prevented", role="sde")[:3]
    ok = any("deadlock" in c.text.lower() for c in top)
    check("retrieval spot check (role KB)", ok, f"{(time.perf_counter() - t) * 1000:.0f} ms, top: {top[0].text[:50] if top else 'none'!r}")
    problems = production_problems(s) if s.app_env == "production" else []
    check("production settings guard", not problems, f"APP_ENV={s.app_env}" + (f"; {problems}" if problems else ""))


async def mcp_checks() -> None:
    from app import mcp_client

    try:
        roles = await mcp_client.call_tool("list_supported_roles", {})
        t = time.perf_counter()
        jobs = await mcp_client.call_tool("search_jobs", {"query": "python developer", "location": "India", "limit": 10})
        sources = sorted({j.get("site") or j.get("source") for j in jobs})
        check("MCP server: tools answer", len(roles) >= 3 and len(jobs) >= 1,
              f"{len(roles)} roles listed, search_jobs returned {len(jobs)} jobs in {(time.perf_counter() - t) * 1000:.0f} ms from {sources}")
    except Exception as e:  # noqa: BLE001
        check("MCP server: tools answer", False, f"{type(e).__name__}: {str(e)[:120]}")
    finally:
        await mcp_client.close() if hasattr(mcp_client, "close") else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--min-roles", type=int, default=4, help="spec bar is 4; a server without the private DevOps seed has 3")
    a = ap.parse_args()
    for step in (lambda: api_checks(a.base), lambda: store_checks(a.min_roles), lambda: asyncio.run(mcp_checks())):
        try:
            step()
        except Exception as e:  # noqa: BLE001 - a crashed check is a failed check, not a crashed run
            check(f"check crashed ({getattr(step, '__name__', 'step')})", False, f"{type(e).__name__}: {str(e)[:150]}")
    w = max(len(n) for n, _, _ in rows) + 2
    print(f"\n{'check'.ljust(w)}result  detail")
    for name, ok, detail in rows:
        print(f"{name.ljust(w)}{'PASS' if ok else 'FAIL'}    {detail}")
    failed = sum(not ok for _, ok, _ in rows)
    print(f"\n{len(rows) - failed} of {len(rows)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
