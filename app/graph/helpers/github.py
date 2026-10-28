"""GitHub repo content for the personal KB, fetched through the MCP server's GitHub tools (spec 7).

For each of the user's most recent own public repos this returns the README plus a short file-tree listing as markdown.
Content is untrusted data: it is only chunked and indexed. Failures (no such user, rate limit, MCP down) are logged
and ingest continues from uploads alone.
"""
import asyncio
import logging

from app.mcp_client import McpUnavailable, call_tool

log = logging.getLogger(__name__)
MAX_REPOS = 5
TREE_FILES = 40


async def _repo_doc(owner: str, repo: dict) -> tuple[str, str, str] | None:
    name = repo["name"]
    try:
        readme, tree = await asyncio.gather(
            call_tool("github_get_readme", {"owner": owner, "repo": name}),
            call_tool("github_repo_tree", {"owner": owner, "repo": name, "limit": TREE_FILES}),
            return_exceptions=True,
        )
    except McpUnavailable:
        raise
    readme = readme if isinstance(readme, str) else ""
    files = tree if isinstance(tree, list) else []
    if not readme and not files:
        return None
    parts = [f"# {name}"]
    if repo.get("description"):
        parts.append(repo["description"])
    if repo.get("language"):
        parts.append(f"Main language: {repo['language']}")
    parts.append(readme if readme else "(no README)")
    if files:
        parts.append("## Repository structure\n" + "\n".join(f"- {p}" for p in files))
    return f"github/{owner}/{name}", "\n\n".join(parts), name


async def fetch_github_docs(username: str | None) -> list[tuple[str, str, str]]:
    """Return (source_name, markdown_text, repo_name) per repo, or [] when unavailable."""
    if not username:
        return []
    try:
        repos = await call_tool("github_list_repos", {"username": username, "limit": MAX_REPOS})
        docs = await asyncio.gather(*[_repo_doc(r.get("owner", username), r) for r in repos if isinstance(r, dict)])
    except McpUnavailable as e:
        log.warning("GitHub ingest skipped for %s: %s", username, e)
        return []
    except Exception as e:  # noqa: BLE001 - unknown user, rate limit, network
        log.warning("GitHub ingest skipped for %s: %s", username, str(e)[:160])
        return []
    return [d for d in docs if d]
