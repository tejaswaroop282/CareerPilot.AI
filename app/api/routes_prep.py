"""Prep flow endpoints: start a run, resume a paused one, stream progress, reports and feedback (spec section 10)."""
import asyncio
import json
import logging
from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.errors import friendly_error
from app.api import progress
from app.auth.deps import CurrentUser, OwnedTrack
from app.config import get_settings
from app.db.client import get_db
from app.db.models import FeedbackIn, PrepIn, ResumeIn
from app.db.repos import claim_ai_run, create_session, get_owned, get_session, list_owned, not_found, update_session
from app.graph.graphs.prep import resume_prep, run_prep

router = APIRouter(tags=["prep"])
log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


async def _execute(thread_id: str, make: Callable[[Callable], Awaitable[dict]], wait: bool) -> dict:
    """Run a graph call with progress events. wait=True returns the outcome; wait=False returns at once and the
    client follows GET /stream/{thread_id} or polls GET /sessions/{thread_id}."""
    progress.start(thread_id)

    async def on_progress(event: dict) -> None:
        await progress.publish(thread_id, event)

    async def _run() -> dict:
        try:
            outcome = await make(on_progress)
            await progress.publish(thread_id, {"event": "status", "status": outcome["status"]})
            return outcome
        except Exception as e:  # recorded on the session by the runner
            await progress.publish(thread_id, {"event": "status", "status": "failed", "error": friendly_error(e, 200)})
            raise
        finally:
            await progress.finish(thread_id)

    if wait:
        try:
            return {"thread_id": thread_id, **await _run()}
        except Exception as e:
            log.exception("prep run failed")
            raise HTTPException(status_code=500, detail="The run failed; see the session for details.") from e

    async def _bg() -> None:
        try:
            await _run()
        except Exception:
            log.debug("background run ended with an error", exc_info=True)

    task = asyncio.create_task(_bg())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"thread_id": thread_id, "status": "running"}


@router.post("/tracks/{track_id}/prep")
async def start_prep(body: PrepIn, track: OwnedTrack, wait: bool = True) -> dict:
    request = body.request.strip()
    job = None
    if body.job_id:
        match = await get_db().job_matches.find_one(
            {"user_id": track["user_id"], "track_id": track["_id"], "job_id": body.job_id}
        )
        if match is None:
            raise not_found()
        job = match.get("job")  # stored snapshot; no MCP get_job call
        request = request or f"Prepare me for the {job.get('title', 'role')} job at {job.get('company', 'this company')}."
    if not request:
        raise HTTPException(status_code=422, detail="Write a request or pick a job.")
    await claim_ai_run(track["user_id"])
    session = await create_session(track["user_id"], track["_id"], "prep", job_id=body.job_id)
    return await _execute(
        session["thread_id"],
        lambda cb: run_prep(
            user_id=track["user_id"], track_id=track["_id"], thread_id=session["thread_id"], request=request,
            mode=body.mode, selected_job=job, on_progress=cb,
        ),
        wait,
    )


def _check_payload(interrupt_type: str | None, body: ResumeIn) -> dict:
    if interrupt_type == "confirm_plan":
        if body.approved is None:
            raise HTTPException(status_code=422, detail="approved is required to resume a plan confirmation")
        return {"approved": body.approved, "edits": body.edits or {}}
    if interrupt_type == "clarification":
        if not (body.text or "").strip():
            raise HTTPException(status_code=422, detail="text is required to resume a clarification")
        return {"text": body.text.strip()}
    if interrupt_type == "answer":
        if body.answer is None:
            raise HTTPException(status_code=422, detail="answer is required to resume an interview question")
        return {"answer": body.answer}
    raise HTTPException(status_code=409, detail="This session is not waiting for input.")


@router.post("/sessions/{thread_id}/resume")
async def resume(thread_id: str, body: ResumeIn, user: CurrentUser, wait: bool = True) -> dict:
    session = await get_session(thread_id, user["_id"])  # ownership 404 and expiry handled here
    if session["status"] == "expired":
        raise HTTPException(status_code=410, detail="This session expired and cannot be resumed.")
    if session["status"] != "awaiting_input" or session["flow"] != "prep":
        raise HTTPException(status_code=409, detail="This session is not waiting for input.")
    payload = _check_payload(session.get("interrupt_type"), body)
    # claim the session so two resumes cannot run at once
    claimed = await get_db().sessions.update_one(
        {"thread_id": thread_id, "user_id": user["_id"], "status": "awaiting_input"}, {"$set": {"status": "running"}}
    )
    if claimed.modified_count != 1:
        raise HTTPException(status_code=409, detail="This session is already running.")
    return await _execute(
        thread_id,
        lambda cb: resume_prep(
            user_id=user["_id"], track_id=session["track_id"], thread_id=thread_id, payload=payload, on_progress=cb
        ),
        wait,
    )


@router.get("/stream/{thread_id}")
async def stream(thread_id: str, user: CurrentUser) -> StreamingResponse:
    await get_session(thread_id, user["_id"])  # ownership

    async def gen():
        run = progress.get(thread_id)
        sent = 0
        while run is not None:
            async with run.cond:
                if sent >= len(run.events) and not run.finished:
                    try:
                        await asyncio.wait_for(run.cond.wait(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                batch, sent = run.events[sent:], len(run.events)
                done = run.finished and sent >= len(run.events)
            for ev in batch:
                yield f"data: {json.dumps(ev)}\n\n"
            if done:
                break
        final = await get_db().sessions.find_one({"thread_id": thread_id, "user_id": user["_id"]})
        status = final["status"] if final else "unknown"
        yield f"data: {json.dumps({'event': 'status', 'status': status})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/tracks/{track_id}/reports")
async def list_reports(track: OwnedTrack) -> list[dict]:
    docs = await list_owned("reports", track["user_id"], track["_id"])
    return [{"id": d["_id"], "created_at": d["created_at"], "overall": d["report"]["overall"],
             "weak_areas": d["report"]["weak_areas"], "role": d["plan"]["role"], "company": d["plan"].get("company")}
            for d in reversed(docs)]


@router.get("/reports/{report_id}")
async def read_report(report_id: str, user: CurrentUser) -> dict:
    d = await get_owned("reports", report_id, user["_id"])
    d["id"] = d.pop("_id")
    return d


@router.post("/feedback", status_code=202)
async def feedback(body: FeedbackIn, user: CurrentUser) -> dict:
    session = await get_session(body.thread_id, user["_id"])
    await get_db().feedback.insert_one(
        {"user_id": user["_id"], "track_id": session["track_id"], **body.model_dump()}
    )
    sent = False
    runs = session.get("run_ids") or []
    s = get_settings()
    if runs and s.langsmith_tracing and s.langsmith_api_key:  # best effort: feedback never blocks the user
        try:
            from langsmith import Client  # noqa: PLC0415

            await asyncio.to_thread(
                Client().create_feedback, runs[-1], key="thumbs", score=body.score,
                comment=f"item {body.item_index}: {body.comment}"[:1000],
            )
            sent = True
        except Exception:  # noqa: BLE001
            log.warning("could not send feedback to LangSmith", exc_info=True)
    return {"stored": True, "langsmith": sent}
