"""Node 3: the job search ReAct agent (see graph/agents/job_search.py). Writes jobs and search_trace; never ranks."""
from langchain_core.runnables import RunnableConfig

from app.graph.agents.job_search import run_job_search
from app.graph.state import Profile, State


async def search_jobs(state: State, config: RunnableConfig) -> dict:
    jobs, trace, notes = await run_job_search(
        profile=state.get("profile"), role=state.get("target_role", ""), user_id=state["user_id"],
        track_id=state["track_id"], tracked=set(state.get("tracked_job_ids") or []), config=config,
    )
    source = "fallback" if any(j.get("source") == "fallback" for j in jobs) else "live"
    return {"jobs": jobs, "search_trace": trace, "job_notes": notes, "job_source": source if jobs else "fallback"}
