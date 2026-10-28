"""Custom MCP server (FastMCP): KB manifest list tools, jobs search and the application tracker.

Run: python -m app.mcp_servers.careerpilot_server            (streamable HTTP on :8001, path /mcp)
     MCP_TRANSPORT=stdio python -m app.mcp_servers.careerpilot_server   (local, no Docker)
"""
import os

from mcp.server.fastmcp import FastMCP

from app import github_api
from app.jobs import provider, tracker
from app.rag import manifest as mf

mcp = FastMCP("careerpilot", host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8001")))


@mcp.tool()
def list_supported_roles() -> list[str]:
    """Roles available in the role knowledge base (from the KB manifest)."""
    return mf.supported_roles()


@mcp.tool()
def list_supported_companies(role: str | None = None) -> list[str]:
    """Companies available in the company knowledge base, optionally only those with data for a role."""
    return mf.supported_companies(role)


@mcp.tool()
def search_jobs(query: str, location: str | None = None, limit: int = 10) -> list[dict]:
    """Search job listings. Returns dicts with id, title, company, location, description, url and source."""
    return provider.search_jobs(query, location, limit)


@mcp.tool()
def get_job(job_id: str) -> dict:
    """One job listing by id (empty dict when unknown)."""
    return provider.get_job(job_id) or {}


@mcp.tool()
def save_application(user_id: str, track_id: str, job_id: str, status: str, resume_id: str | None = None) -> str:
    """Create or update a tracker row for a job; returns the row id."""
    return tracker.save_application(user_id, track_id, job_id, status, resume_id, provider.get_job(job_id))


@mcp.tool()
def list_applications(user_id: str, track_id: str) -> list[dict]:
    """Tracker rows for one user and track."""
    return tracker.list_applications(user_id, track_id)


@mcp.tool()
def github_list_repos(username: str, limit: int = 5) -> list[dict]:
    """A user's own public GitHub repositories (name, owner, description, language, url), newest first."""
    return github_api.list_repos(username, limit)


@mcp.tool()
def github_get_readme(owner: str, repo: str) -> str:
    """README text of a public repository."""
    return github_api.get_readme(owner, repo)


@mcp.tool()
def github_repo_tree(owner: str, repo: str, limit: int = 120) -> list[str]:
    """File paths in a public repository (truncated to limit)."""
    return github_api.repo_tree(owner, repo, limit)


@mcp.tool()
def github_get_file(owner: str, repo: str, path: str) -> str:
    """Contents of one small text file in a public repository."""
    return github_api.get_file(owner, repo, path)


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "streamable-http"))
