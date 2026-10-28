"""Jobs and tracker endpoints: stored matches, refresh (discover graph), dismiss, applications."""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api import progress
from app.auth.deps import OwnedTrack
from app.db.client import get_db
from app.db.models import utcnow
from app.db.repos import claim_ai_run, create_session, not_found
from app.graph.graphs.discover import run_discover
from app.jobs.provider import search_links
from app.jobs.tracker import STATUSES
from app.mcp_client import McpUnavailable, call_tool

router = APIRouter(prefix="/tracks/{track_id}", tags=["jobs"])
log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def start_discover_task(user_id: str, track_id: str, session: dict) -> None:
    """Run the discover graph in the background (also used right after the primary resume is ingested)."""
    thread_id = session["thread_id"]
    progress.start(thread_id)

    async def _cb(event: dict) -> None:
        await progress.publish(thread_id, event)

    async def _run() -> None:
        try:
            await run_discover(user_id, track_id, thread_id, on_progress=_cb)
            await progress.publish(thread_id, {"event": "status", "status": "done"})
        except Exception:  # recorded on the session
            await progress.publish(thread_id, {"event": "status", "status": "failed"})
        finally:
            await progress.finish(thread_id)

    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


@router.get("/jobs")
async def list_jobs(track: OwnedTrack) -> dict:
    db = get_db()
    q = {"user_id": track["user_id"], "track_id": track["_id"], "status": {"$ne": "dismissed"}}
    rows = [m async for m in db.job_matches.find(q).sort("score", -1)]
    latest = await db.sessions.find_one({"user_id": track["user_id"], "track_id": track["_id"], "flow": "discover"}, sort=[("updated_at", -1)])
    matches = [
        {"job_id": m["job_id"], "job": m["job"], "score": m["score"], "reasons": m["reasons"], "missing": m["missing"],
         "fit_note": m["fit_note"], "source": m["source"], "found_at": m["found_at"]} for m in rows
    ]
    primary = await db.resumes.find_one({"user_id": track["user_id"], "track_id": track["_id"], "is_primary": True})
    changed = bool(primary and matches and (primary.get("primary_since") or primary["created_at"]) > min(m["found_at"] for m in matches))
    role = {"sde": "software engineer", "aiml": "machine learning engineer", "genai": "generative ai engineer",
            "devops": "devops engineer"}.get(track["target_role"], track["target_role"])
    return {
        "matches": matches,
        "search_links": search_links(role),
        "needs_refresh": changed,  # the primary resume changed after these matches were found: show a Refresh prompt
        "sample_data": any(m["source"] == "fallback" for m in matches),
        "discovery": {"thread_id": latest["thread_id"], "status": latest["status"], "trace": (latest.get("result") or {}).get("trace", []),
                      "notes": (latest.get("result") or {}).get("notes", [])} if latest else None,
    }


@router.post("/jobs/refresh", status_code=202)
async def refresh_jobs(track: OwnedTrack) -> dict:
    await claim_ai_run(track["user_id"])
    session = await create_session(track["user_id"], track["_id"], "discover")
    start_discover_task(track["user_id"], track["_id"], session)
    return {"thread_id": session["thread_id"], "status": "running"}


@router.post("/jobs/{job_id}/dismiss", status_code=204)
async def dismiss_job(job_id: str, track: OwnedTrack) -> None:
    res = await get_db().job_matches.update_one(
        {"user_id": track["user_id"], "track_id": track["_id"], "job_id": job_id}, {"$set": {"status": "dismissed"}}
    )
    if res.matched_count == 0:
        raise not_found()


class ApplicationIn(BaseModel):
    job_id: str
    status: Literal["saved", "applied", "interview", "offer", "rejected"] = "applied"
    resume_id: str | None = None


class ApplicationPatch(BaseModel):
    status: Literal["saved", "applied", "interview", "offer", "rejected"]


@router.get("/applications")
async def list_applications(track: OwnedTrack) -> list[dict]:
    try:
        return await call_tool("list_applications", {"user_id": track["user_id"], "track_id": track["_id"]})
    except McpUnavailable as e:
        raise HTTPException(status_code=503, detail="The tracker service is unavailable. Try again shortly.") from e


@router.post("/applications", status_code=201)
async def mark_applied(body: ApplicationIn, track: OwnedTrack) -> dict:
    """The user clicks Mark as applied: a tracker row is saved through the MCP save_application tool."""
    match = await get_db().job_matches.find_one({"user_id": track["user_id"], "track_id": track["_id"], "job_id": body.job_id})
    if match is None:
        raise not_found()
    resume_id = body.resume_id
    if resume_id is None:
        primary = await get_db().resumes.find_one({"user_id": track["user_id"], "track_id": track["_id"], "is_primary": True})
        resume_id = primary["_id"] if primary else None
    elif await get_db().resumes.find_one({"_id": resume_id, "user_id": track["user_id"], "track_id": track["_id"]}) is None:
        raise not_found()
    try:
        row_id = await call_tool("save_application", {
            "user_id": track["user_id"], "track_id": track["_id"], "job_id": body.job_id, "status": body.status, "resume_id": resume_id,
        })
    except McpUnavailable as e:
        raise HTTPException(status_code=503, detail="The tracker service is unavailable. Try again shortly.") from e
    return {"id": row_id}


@router.patch("/applications/{application_id}")
async def update_application(application_id: str, body: ApplicationPatch, track: OwnedTrack) -> dict:
    if body.status not in STATUSES:
        raise HTTPException(status_code=422, detail="invalid status")
    res = await get_db().applications.update_one(
        {"_id": application_id, "user_id": track["user_id"], "track_id": track["_id"]},
        {"$set": {"status": body.status, "updated_at": utcnow()}},
    )
    if res.matched_count == 0:
        raise not_found()
    return {"id": application_id, "status": body.status}
