"""The discover graph: search_jobs (node 3, ReAct agent) then rank_jobs (node 4, Send fan-out and merge).

Results are stored in job_matches (one document per job: snapshot, score, reasons, missing skills, source) and replace
the previous matches. Reopening the Jobs page reads job_matches and never re-runs the agents.
"""
import logging

from langgraph.graph import END, START, StateGraph

from app.errors import friendly_error
from app.config import get_settings
from app.db.client import get_db
from app.db.models import utcnow
from app.db.repos import update_session
from app.graph.checkpoint import get_checkpointer
from app.graph.nodes.rank_jobs import fan_out, rank_jobs, score_job
from app.graph.nodes.search_jobs import search_jobs
from app.graph.state import Profile, State

log = logging.getLogger(__name__)


def build_discover_graph(checkpointer=None):
    g = StateGraph(State)
    g.add_node("search_jobs", search_jobs)
    g.add_node("score_job", score_job)  # the per-job worker of rank_jobs
    g.add_node("rank_jobs", rank_jobs)
    g.add_edge(START, "search_jobs")
    g.add_conditional_edges("search_jobs", fan_out, ["score_job", "rank_jobs"])
    g.add_edge("score_job", "rank_jobs")
    g.add_edge("rank_jobs", END)
    return g.compile(checkpointer=checkpointer)


async def run_discover(user_id: str, track_id: str, thread_id: str, on_progress=None) -> dict:
    db = get_db()
    track = await db.tracks.find_one({"_id": track_id, "user_id": user_id})
    tracked = [a["job_id"] async for a in db.applications.find({"user_id": user_id, "track_id": track_id})]
    state: State = {
        "user_id": user_id, "track_id": track_id, "target_role": track["target_role"], "tracked_job_ids": tracked,
    }
    if track.get("profile"):
        state["profile"] = Profile(**track["profile"])
    cfg = {
        "configurable": {"thread_id": thread_id}, "run_name": "discover", "tags": ["flow:discover"],
        "metadata": {"flow": "discover", "track_id": track_id},
        # every posting is scored by its own model call; unlimited fan-out overshoots per-minute token limits and drops jobs
        "max_concurrency": get_settings().jobs_scoring_concurrency,
    }
    try:
        final: dict = {}
        async for chunk in build_discover_graph(get_checkpointer()).astream(state, cfg, stream_mode="updates"):
            for node, upd in chunk.items():
                if on_progress:
                    await on_progress({"node": node, "event": "done"})
                final.update(upd or {})
        jobs = {j["id"]: j for j in (final.get("jobs") or [])}
        ranked = final.get("ranked_jobs") or []
        docs = [
            {"user_id": user_id, "track_id": track_id, "job_id": m.job_id, "job": jobs[m.job_id], "score": m.score,
             "reasons": m.reasons, "missing": m.missing, "fit_note": m.fit_note,
             "source": jobs[m.job_id].get("source", "live"), "status": "active", "found_at": utcnow()}
            for m in ranked if m.job_id in jobs
        ]
        await db.job_matches.delete_many({"user_id": user_id, "track_id": track_id})
        if docs:
            await db.job_matches.insert_many(docs)
        result = {"trace": [t.model_dump() for t in final.get("search_trace") or []], "notes": final.get("job_notes") or [],
                  "candidates": len(jobs), "matches": len(docs), "source": final.get("job_source", "fallback")}
        await db.sessions.update_one({"thread_id": thread_id, "user_id": user_id}, {"$set": {"result": result}})
    except Exception as e:
        log.exception("discover run failed")
        await update_session(thread_id, user_id, "failed", error=friendly_error(e))
        raise
    await update_session(thread_id, user_id, "done")
    return result
