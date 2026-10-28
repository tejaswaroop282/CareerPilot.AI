import asyncio
import logging

from fastapi import APIRouter

from app.auth.deps import OwnedTrack
from app.db.client import get_db
from app.db.repos import create_session
from app.graph.graphs.ingest import run_ingest
from app.graph.state import Profile

router = APIRouter(prefix="/tracks/{track_id}", tags=["ingest"])
log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()  # keep references so background runs are not garbage collected


@router.post("/ingest", status_code=202)
async def start_ingest(track: OwnedTrack, force: bool = False) -> dict:
    """Start the ingest graph in the background; poll GET /sessions/{thread_id} for status."""
    session = await create_session(track["user_id"], track["_id"], "ingest")

    async def _run() -> None:
        try:
            await run_ingest(track["user_id"], track["_id"], session["thread_id"], force)
        except Exception:  # already recorded on the session
            log.debug("ingest task ended with an error", exc_info=True)
            return
        # Only the primary resume triggers discovery; uploading other documents alone does not.
        primary = await get_db().resumes.find_one({"user_id": track["user_id"], "track_id": track["_id"], "is_primary": True})
        if primary:
            from app.api.routes_jobs import start_discover_task  # noqa: PLC0415

            discover = await create_session(track["user_id"], track["_id"], "discover")
            start_discover_task(track["user_id"], track["_id"], discover)

    task = asyncio.create_task(_run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"thread_id": session["thread_id"], "status": "running"}


@router.get("/profile")
async def get_profile(track: OwnedTrack) -> Profile:
    return Profile(**track["profile"]) if track.get("profile") else Profile()
