# The CareerPilot MCP server

## What it is and why it exists
The Model Context Protocol (MCP) is a standard way for an AI application to discover and call tools exposed by a server. CareerPilot runs
**one MCP server** (`app/mcp_servers/careerpilot_server.py`, built with FastMCP, streamable HTTP on port 8001, path `/mcp`) that puts
everything the agents need behind typed tools:

| Tool | What it does |
|---|---|
| `search_jobs(query, location, limit)` | Searches **five free job sources at once** (RemoteOK, Remotive, Arbeitnow, Jobicy, Himalayas), plus Adzuna and Jooble when keyed. Merges, de-duplicates and ranks. Labelled sample data if every source fails. |
| `get_job(job_id)` | One posting by id. |
| `save_application`, `list_applications` | The application tracker (status per job, per user and track). |
| `list_supported_roles`, `list_supported_companies` | What the knowledge bases cover; the planner validates against these. |
| `github_list_repos`, `github_get_readme`, `github_repo_tree`, `github_get_file` | Read-only access to public GitHub repositories (no token needed; 60 requests per hour without one). |

The application and its agents talk to this one server instead of to seven different services. Benefits that are real in this project:
- **One place for source logic:** per-source caching, the Adzuna hard cap (20 of the key's 25 calls, counted in Mongo), fallbacks, attribution. Replacing or adding a job source changes the server only.
- **Typed tools the model discovers by itself:** the job-search agent (a ReAct loop) is given the tool list and schemas from the server; there is no hand-written glue per source.
- **Isolation:** identity (`user_id`, `track_id`) is bound by the caller, never chosen by the model; the server is not published outside the Docker network.
- **Portability:** any MCP client can use the same tools.

Honest limit: today CareerPilot's own backend is the only client, on the same private network. The portability is a design property you can
demonstrate (below), not yet something other teams use.

## Where it is used
- Job discovery: the job-search agent calls `search_jobs`, `get_job`, `list_applications` (`app/graph/agents/job_search.py`).
- Resume ingest: GitHub tools (`app/graph/helpers/github.py`).
- Planning: roles and companies are validated through the list tools (`app/graph/nodes/plan_prep.py`).
- The app reaches it with `langchain-mcp-adapters` (`app/mcp_client.py`). If the server is unreachable, the call is made in process **and the trace says so**
  (`MCP_MODE=auto`); in tests `MCP_MODE=local` skips the network.

## Show it working with another client (a good live demo)
Expose the port to your own machine only (it is internal by default), then connect any MCP client. With the official inspector:

```bash
# local stack: temporarily publish the port
docker compose -f docker-compose.yml run --rm -p 127.0.0.1:8001:8001 mcp   # or add  ports: ["127.0.0.1:8001:8001"]  to the mcp service
npx @modelcontextprotocol/inspector
# Transport: Streamable HTTP, URL: http://localhost:8001/mcp  ->  Connect  ->  Tools  ->  search_jobs
#   query: "machine learning engineer"   location: "India"   limit: 5
```
You will see the nine tools with their JSON schemas, and the merged result with a `site` field per job.

From Python, the same call the application makes:
```python
from app import mcp_client
jobs = await mcp_client.call_tool("search_jobs", {"query": "python developer", "location": "India", "limit": 10})
```

## Check it in the numbers
`python -m scripts.validate_stack` includes an "MCP server: tools answer" line: roles listed, jobs returned, latency and which sources answered.
