"""Ownership-aware queries. Every read of user data filters by user_id (and track_id where relevant)."""
import uuid
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

from app.config import get_settings
from app.db.client import get_db
from app.db.models import utcnow


def new_id() -> str:
    return uuid.uuid4().hex


def not_found() -> HTTPException:
    # 404, never 403, so other users' resources are indistinguishable from missing ones.
    return HTTPException(status_code=404, detail="Not found")


async def get_owned(collection: str, doc_id: str, user_id: str, **extra: Any) -> dict:
    doc = await get_db()[collection].find_one({"_id": doc_id, "user_id": user_id, **extra})
    if doc is None:
        raise not_found()
    return doc


async def list_owned(collection: str, user_id: str, track_id: str | None = None, **extra: Any) -> list[dict]:
    query: dict[str, Any] = {"user_id": user_id, **extra}
    if track_id is not None:
        query["track_id"] = track_id
    return [d async for d in get_db()[collection].find(query).sort("created_at", 1)]


# ---- sessions -------------------------------------------------------------

def make_thread_id(user_id: str, track_id: str, flow: str) -> str:
    return f"{user_id}:{track_id}:{flow}:{uuid.uuid4().hex}"


async def create_session(
    user_id: str, track_id: str, flow: str, job_id: str | None = None, status: str = "running"
) -> dict:
    doc = {
        "thread_id": make_thread_id(user_id, track_id, flow),
        "user_id": user_id,
        "track_id": track_id,
        "flow": flow,
        "job_id": job_id,
        "status": status,
        "interrupt_type": None,
        "updated_at": utcnow(),
    }
    await get_db().sessions.insert_one(doc)
    return doc


async def update_session(
    thread_id: str, user_id: str, status: str, interrupt_type: str | None = None, error: str | None = None
) -> None:
    await get_db().sessions.update_one(
        {"thread_id": thread_id, "user_id": user_id},
        {"$set": {"status": status, "interrupt_type": interrupt_type, "error": error, "updated_at": utcnow()}},
    )


async def expire_stale_sessions(user_id: str, track_id: str | None = None) -> None:
    cutoff = utcnow() - timedelta(hours=get_settings().session_expiry_hours)
    query: dict[str, Any] = {"user_id": user_id, "status": "awaiting_input", "updated_at": {"$lt": cutoff}}
    if track_id:
        query["track_id"] = track_id
    await get_db().sessions.update_many(query, {"$set": {"status": "expired"}})


async def fail_orphaned_sessions() -> int:
    """Runs execute inside the API process, so a session still 'running' when the app starts lost its run (restart or
    crash). Mark it failed so the UI stops waiting and the user can start again. Assumes one API process."""
    res = await get_db().sessions.update_many(
        {"status": "running"},
        {"$set": {"status": "failed", "error": "The server restarted while this run was in progress.", "updated_at": utcnow()}},
    )
    return res.modified_count


async def get_session(thread_id: str, user_id: str) -> dict:
    await expire_stale_sessions(user_id)
    doc = await get_db().sessions.find_one({"thread_id": thread_id, "user_id": user_id})
    if doc is None:
        raise not_found()
    return doc


async def list_sessions(user_id: str, track_id: str | None) -> list[dict]:
    await expire_stale_sessions(user_id, track_id)
    query: dict[str, Any] = {"user_id": user_id}
    if track_id:
        query["track_id"] = track_id
    return [d async for d in get_db().sessions.find(query).sort("updated_at", -1)]


async def claim_ai_run(user_id: str) -> None:
    """Count one AI run (prep or job refresh) against the user's daily allowance, so one person cannot use up the shared
    model quota for everyone. Raises 429 once USER_DAILY_RUNS is exceeded (0 turns the limit off)."""
    from fastapi import HTTPException  # noqa: PLC0415

    from app.config import get_settings  # noqa: PLC0415

    limit = get_settings().user_daily_runs
    if limit <= 0:
        return
    now = utcnow()
    doc = await get_db().ai_usage.find_one_and_update(
        {"_id": f"{user_id}:{now:%Y-%m-%d}"},
        {"$inc": {"runs": 1}, "$setOnInsert": {"created_at": now}},
        upsert=True, return_document=True,
    )
    if doc["runs"] > limit:
        raise HTTPException(status_code=429, detail=f"Daily limit of {limit} AI runs reached. It resets at midnight UTC.")
