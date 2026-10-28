"""The job search agent (node 3): the shared ReAct loop over the MCP jobs tools (spec 6.3).

It maximises recall and never ranks. user_id and track_id are bound by closure. Code deduplicates the result and
drops jobs already in the tracker; on failure one query built from the profile runs instead.
"""
from pydantic import Field

from app.config import get_settings
from app.graph.agents.react_loop import ToolArgs, ToolResult, ToolSpec, run_react_agent
from app.graph.prompts_jobs import JOB_AGENT_SYSTEM, JOB_AGENT_TASK
from app.graph.state import AgentStep, Profile
from app.mcp_client import McpUnavailable, call_tool, call_tool_local


class SearchJobsArgs(ToolArgs):
    query: str = Field(min_length=2, max_length=200)
    location: str | None = Field(default=None, max_length=80)
    limit: int = Field(default=15, ge=1, le=25)


class GetJobArgs(ToolArgs):
    job_id: str = Field(min_length=1, max_length=80)


class ListApplicationsArgs(ToolArgs):
    pass


class FinishJobsArgs(ToolArgs):
    candidate_job_ids: list[str]
    notes: list[str] = Field(default_factory=list)


def _line(j: dict) -> str:
    desc = " ".join(str(j.get("description", "")).split())[:110]
    return f"{j['id']} | {j.get('title')} | {j.get('company')} | {j.get('location', '')} | {desc}"


def build_job_tools(*, user_id: str, track_id: str) -> list[ToolSpec]:
    seen: set[str] = set()

    async def search(a: SearchJobsArgs) -> ToolResult:
        location = a.location or get_settings().jobs_default_location or None  # the agent often leaves it out
        jobs = await call_tool("search_jobs", {"query": a.query, "location": location, "limit": a.limit})
        jobs = [j for j in jobs if isinstance(j, dict) and j.get("id")]
        seen.update(j["id"] for j in jobs)
        if not jobs:
            return ToolResult(observation="no jobs found. Broaden the query.")
        return ToolResult(observation=f"{len(jobs)} jobs\n" + "\n".join(_line(j) for j in jobs), artifacts={j["id"]: j for j in jobs})

    async def get(a: GetJobArgs) -> ToolResult:
        job = await call_tool("get_job", {"job_id": a.job_id})
        if not job:
            return ToolResult(observation=f"no job with id {a.job_id}")
        seen.add(job["id"])
        text = " ".join(str(job.get("description", "")).split())[:500]
        return ToolResult(observation=_line(job) + "\n" + text, artifacts={job["id"]: job})

    async def apps(_: ListApplicationsArgs) -> ToolResult:
        rows = await call_tool("list_applications", {"user_id": user_id, "track_id": track_id})  # identity bound here
        if not rows:
            return ToolResult(observation="the tracker is empty")
        return ToolResult(observation="tracked jobs: " + ", ".join(f"{r['job_id']} ({r['status']})" for r in rows))

    def check_finish(a: FinishJobsArgs) -> str | None:
        unknown = [i for i in a.candidate_job_ids if i not in seen]
        return f"these ids were never returned by a search: {', '.join(unknown)}" if unknown else None

    return [
        ToolSpec("search_jobs", "Search job listings by query and optional location.", SearchJobsArgs, run=search),
        ToolSpec("get_job", "Fetch one job listing by id.", GetJobArgs, run=get),
        ToolSpec("list_applications", "List jobs already in the user's tracker.", ListApplicationsArgs, run=apps),
        ToolSpec("finish", "Finish with the candidate job ids and short notes.", FinishJobsArgs, kind="finish", validate=check_finish),
    ]


def profile_query(profile: Profile | None, role: str) -> str:
    skills = " ".join(profile.skills[:5] if profile else [])
    return f"{role} {skills}".strip()


def dedupe(jobs: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out = []
    for j in jobs:
        key = (str(j.get("title", "")).lower().strip(), str(j.get("company", "")).lower().strip())
        if key not in seen:
            seen.add(key)
            out.append(j)
    return out


async def run_job_search(
    *, profile: Profile | None, role: str, user_id: str, track_id: str, tracked: set[str], config: dict | None = None, llm=None
) -> tuple[list[dict], list[AgentStep], list[str]]:
    steps = get_settings().agent_max_steps_jobs
    task = JOB_AGENT_TASK.format(
        role=role, skills=", ".join(profile.skills[:20]) if profile else "unknown",
        projects=", ".join(p.name for p in profile.projects) if profile else "none",
        level=(profile.level if profile else "") or "unknown",
    )
    res = await run_react_agent(
        system_prompt=JOB_AGENT_SYSTEM.format(max_steps=steps), task=task,
        tools=build_job_tools(user_id=user_id, track_id=track_id), max_steps=steps, config=config, llm=llm,
    )
    trace, notes = list(res.trace), []
    pool = res.artifacts
    if res.status == "finished" and pool:
        ids = [i for i in res.finish_args.get("candidate_job_ids", []) if i in pool]
        notes = [str(n) for n in res.finish_args.get("notes", [])]
        jobs = [pool[i] for i in ids] or list(pool.values())
    else:
        query = profile_query(profile, role)
        args = {"query": query, "location": get_settings().jobs_default_location or None, "limit": 20}
        notes = ["Used a single profile-based query because the job search agent did not finish."]
        try:
            found = await call_tool("search_jobs", args)
        except McpUnavailable as e:  # visible, never silent: the trace and notes say the MCP server was bypassed
            found = await call_tool_local("search_jobs", args)
            notes.append(f"The MCP server was unreachable ({e}); jobs came from the in-process provider.")
        jobs = [j for j in found if isinstance(j, dict) and j.get("id")]
        trace.append(AgentStep(
            step=len(trace) + 1, rationale=f"Fallback: agent ended with status '{res.status}'; one query from the profile",
            tool="single_query_fallback", args={"query": query}, observation=f"{len(jobs)} jobs",
        ))
    jobs = [j for j in dedupe(jobs) if j["id"] not in tracked]  # code, not the model, enforces tracker filtering
    return jobs, trace, notes
