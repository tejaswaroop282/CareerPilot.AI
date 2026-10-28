"""Read-only GitHub access for the MCP server tools (public REST API). GITHUB_TOKEN is optional and only raises the
rate limit (60 requests per hour without it). Repository content is untrusted data: callers only chunk and index it."""
import base64

import httpx

from app.config import get_settings

API = "https://api.github.com"
MAX_FILE_BYTES = 120_000
README_CHARS = 20_000


class GitHubError(Exception):
    pass


def _get(path: str, **params) -> dict | list:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "CareerPilot/0.1"}
    token = get_settings().github_token
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        r = httpx.get(f"{API}{path}", params=params, headers=headers, timeout=20)
    except httpx.HTTPError as e:
        raise GitHubError(f"GitHub unreachable: {type(e).__name__}") from e
    if r.status_code == 404:
        raise GitHubError("not found")
    if r.status_code in (403, 429):
        raise GitHubError("GitHub rate limit reached; set GITHUB_TOKEN to raise it")
    if r.status_code >= 400:
        raise GitHubError(f"GitHub error {r.status_code}")
    return r.json()


def list_repos(username: str, limit: int = 5) -> list[dict]:
    """Own (non-fork) public repos, most recently pushed first."""
    rows = _get(f"/users/{username}/repos", per_page=50, sort="pushed")
    repos = [r for r in rows if not r.get("fork") and not r.get("archived")][:limit]
    return [{"name": r["name"], "owner": r["owner"]["login"], "description": r.get("description") or "",
             "language": r.get("language") or "", "url": r["html_url"], "pushed_at": r.get("pushed_at")} for r in repos]


def get_readme(owner: str, repo: str) -> str:
    data = _get(f"/repos/{owner}/{repo}/readme")
    return base64.b64decode(data["content"]).decode("utf-8", "replace")[:README_CHARS]


def repo_tree(owner: str, repo: str, limit: int = 120) -> list[str]:
    data = _get(f"/repos/{owner}/{repo}/git/trees/HEAD", recursive=1)
    paths = [t["path"] for t in data.get("tree", []) if t["type"] == "blob" and not t["path"].startswith((".git/", "node_modules/"))]
    return paths[:limit]


def get_file(owner: str, repo: str, path: str) -> str:
    data = _get(f"/repos/{owner}/{repo}/contents/{path}")
    if isinstance(data, list) or data.get("size", 0) > MAX_FILE_BYTES:
        raise GitHubError("not a small text file")
    return base64.b64decode(data["content"]).decode("utf-8", "replace")
