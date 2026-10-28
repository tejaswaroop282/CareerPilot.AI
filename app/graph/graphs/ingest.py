"""The ingest graph: ingest (node 1) then build_profile (node 2)."""
import logging

from langgraph.graph import END, START, StateGraph

from app.errors import friendly_error
from app.db.client import get_db
from app.db.repos import update_session
from app.graph.checkpoint import get_checkpointer
from app.graph.nodes.build_profile import build_profile
from app.graph.nodes.ingest import ingest
from app.graph.state import State

log = logging.getLogger(__name__)


def build_ingest_graph(checkpointer=None):
    g = StateGraph(State)
    g.add_node("ingest", ingest)
    g.add_node("build_profile", build_profile)
    g.add_edge(START, "ingest")
    g.add_edge("ingest", "build_profile")
    g.add_edge("build_profile", END)
    return g.compile(checkpointer=checkpointer)


async def run_ingest(user_id: str, track_id: str, thread_id: str, force: bool = False) -> dict:
    """Run the ingest graph for a track and record the outcome on its session."""
    db = get_db()
    track = await db.tracks.find_one({"_id": track_id, "user_id": user_id})
    primary = await db.resumes.find_one({"user_id": user_id, "track_id": track_id, "is_primary": True})
    state: State = {
        "user_id": user_id,
        "track_id": track_id,
        "target_role": track["target_role"],
        "github_username": track.get("github_username") or "",
        "force_reindex": force,
    }
    if primary:
        state["resume_id"] = primary["_id"]
    try:
        result = await build_ingest_graph(get_checkpointer()).ainvoke(
            state,
            config={
                "configurable": {"thread_id": thread_id},
                "run_name": "ingest",
                "tags": ["flow:ingest"],
                "metadata": {"flow": "ingest", "track_id": track_id},
            },
        )
    except Exception as e:
        log.exception("ingest run failed")
        await update_session(thread_id, user_id, "failed", error=friendly_error(e))
        raise
    await update_session(thread_id, user_id, "done")
    return result
