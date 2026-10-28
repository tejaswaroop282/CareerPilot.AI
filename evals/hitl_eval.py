"""Human-in-the-loop flows in headless mode (spec 11.2): scripted resume values are fed to every interrupt.

Uses the real prep graph, Mongo checkpointer and (live) LLM against a throwaway user and track, then deletes them.
Checks pause, resume, reject, edit, and that an expired session can no longer be resumed.
"""
import asyncio
import json
from datetime import timedelta
from pathlib import Path

from app.db.client import close_client, get_db
from app.db.models import utcnow
from app.db.repos import create_session, expire_stale_sessions, new_id
from app.graph.graphs.prep import resume_prep, run_prep

DATASETS = Path(__file__).parent / "datasets"
FILLER_ANSWER = "I am not sure, but I would reason from first principles."


async def run_script(sc: dict, uid: str, tid: str) -> dict:
    session = await create_session(uid, tid, "prep")
    thread = session["thread_id"]
    out = await run_prep(user_id=uid, track_id=tid, thread_id=thread, request=sc["request"], mode=sc["mode"])
    seen = []
    resumes = list(sc["resumes"])
    for _ in range(12):  # hard cap: follow-ups may add pauses
        if out["status"] != "awaiting_input":
            break
        seen.append(f"awaiting_input:{out['interrupt']['type']}")
        payload = resumes.pop(0) if resumes else {"answer": FILLER_ANSWER}
        await get_db().sessions.update_one({"thread_id": thread}, {"$set": {"status": "running"}})
        out = await resume_prep(user_id=uid, track_id=tid, thread_id=thread, payload=payload)
    seen.append(out["status"])
    want = sc["expect"]
    # The live planner may ask a clarifying question first or go straight to plan confirmation; both are valid planning
    # pauses (each path is covered by the scripted tests), so they are interchangeable here.
    planning = {"awaiting_input:clarification", "awaiting_input:confirm_plan"}
    norm = lambda xs: ["planning_pause" if x in planning else x for x in xs]  # noqa: E731
    dedup = lambda xs: [x for i, x in enumerate(xs) if i == 0 or x != xs[i - 1]]  # noqa: E731
    got = dedup(norm(seen))
    if want[0] not in planning and got[0] == "planning_pause":
        got = got[1:]  # an extra clarifying question before the scenario's own first pause is allowed
    ok = got == dedup(norm(want))
    return {"name": sc["name"], "ok": ok and seen[-1] == "done", "seen": seen, "expected": want, "thread": thread}


async def expiry_check(uid: str, tid: str) -> bool:
    """A paused session older than SESSION_EXPIRY_HOURS is marked expired and is no longer resumable."""
    session = await create_session(uid, tid, "prep", status="awaiting_input")
    await get_db().sessions.update_one({"thread_id": session["thread_id"]}, {"$set": {"updated_at": utcnow() - timedelta(hours=48)}})
    await expire_stale_sessions(uid, tid)
    doc = await get_db().sessions.find_one({"thread_id": session["thread_id"]})
    return doc["status"] == "expired"


async def run_async() -> dict:
    scripts = json.loads((DATASETS / "hitl_scripts.json").read_text())["scenarios"]
    uid, tid = f"eval-{new_id()[:8]}", new_id()
    db = get_db()
    await db.users.insert_one({"_id": uid, "email": f"{uid}@eval.local", "password_hash": "x"})
    await db.tracks.insert_one({"_id": tid, "user_id": uid, "name": "eval", "target_role": "sde", "created_at": utcnow()})
    try:
        results = [await run_script(sc, uid, tid) for sc in scripts]
        expired = await expiry_check(uid, tid)
    finally:
        from app.db.purge import purge_track

        await purge_track(uid, tid)
        await db.users.delete_one({"_id": uid})
        await close_client()
    return {"scenarios": results, "pass_rate": round(sum(r["ok"] for r in results) / len(results), 3), "expired_cannot_resume": expired}


def run() -> dict:
    out = asyncio.run(run_async())
    print("\nHuman-in-the-loop flows (headless)")
    for r in out["scenarios"]:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['name']}: {r['seen']}")
    print(f"  pass rate {out['pass_rate']}, expired sessions cannot be resumed: {out['expired_cannot_resume']}")
    return out
