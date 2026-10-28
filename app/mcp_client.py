"""Calls to the custom MCP server through langchain-mcp-adapters (MultiServerMCPClient, streamable HTTP).

The tools are loaded from the server (`get_tools`) and invoked as LangChain tools. If the server is unreachable this
raises McpUnavailable: nothing is silently bypassed. Callers that have a defined fallback catch it, run the in-process
equivalent through `call_tool_local`, and record that in their trace. MCP_MODE=local (tests, running without Docker)
runs the same functions in-process on purpose.
"""
import asyncio
import json
import logging
from typing import Any

from app import github_api
from app.config import get_settings
from app.jobs import provider, tracker
from app.rag import manifest

log = logging.getLogger(__name__)
_tools: dict | None = None


class McpUnavailable(Exception):
    pass


LOCAL = {
    "search_jobs": lambda a: provider.search_jobs(a["query"], a.get("location"), a.get("limit", 10)),
    "get_job": lambda a: provider.get_job(a["job_id"]) or {},
    "save_application": lambda a: tracker.save_application(
        a["user_id"], a["track_id"], a["job_id"], a["status"], a.get("resume_id"), provider.get_job(a["job_id"])
    ),
    "list_applications": lambda a: tracker.list_applications(a["user_id"], a["track_id"]),
    "list_supported_roles": lambda a: manifest.supported_roles(),
    "list_supported_companies": lambda a: manifest.supported_companies(a.get("role")),
    "github_list_repos": lambda a: github_api.list_repos(a["username"], a.get("limit", 5)),
    "github_get_readme": lambda a: github_api.get_readme(a["owner"], a["repo"]),
    "github_repo_tree": lambda a: github_api.repo_tree(a["owner"], a["repo"], a.get("limit", 120)),
    "github_get_file": lambda a: github_api.get_file(a["owner"], a["repo"], a["path"]),
}


LIST_TOOLS = {"search_jobs", "list_applications", "list_supported_roles", "list_supported_companies", "github_list_repos", "github_repo_tree"}


def _parse(block: Any) -> Any:
    if isinstance(block, dict) and "text" in block:
        block = block["text"]
    if isinstance(block, str):
        try:
            return json.loads(block)
        except ValueError:
            return block
    return block


def _normalise(name: str, result: Any) -> Any:
    """Adapters return one content block per list item (or one block for a scalar); rebuild the Python value."""
    if isinstance(result, tuple):  # (content, artifact)
        result = result[0]
    if name in LIST_TOOLS:
        if result in (None, "", []):
            return []
        blocks = result if isinstance(result, list) else [result]
        items = [_parse(b) for b in blocks]
        return items[0] if len(items) == 1 and isinstance(items[0], list) else items
    if isinstance(result, list):
        result = result[0] if len(result) == 1 else "".join(b["text"] if isinstance(b, dict) else str(b) for b in result)
    return _parse(result)


async def _remote_tools() -> dict:
    global _tools
    if _tools is None:
        from langchain_mcp_adapters.client import MultiServerMCPClient  # noqa: PLC0415

        client = MultiServerMCPClient({"careerpilot": {"url": get_settings().mcp_server_url, "transport": "streamable_http"}})
        _tools = {t.name: t for t in await asyncio.wait_for(client.get_tools(), timeout=5)}
    return _tools


def reset() -> None:
    """Forget loaded tools (tests, or after the server restarts)."""
    global _tools
    _tools = None


async def call_tool_local(name: str, args: dict) -> Any:
    return await asyncio.to_thread(LOCAL[name], args)


async def call_tool(name: str, args: dict) -> Any:
    """Call an MCP tool. Raises McpUnavailable when the server cannot be reached; tool errors propagate as exceptions."""
    if get_settings().mcp_mode == "local":
        return await call_tool_local(name, args)
    try:
        tool = (await _remote_tools())[name]
    except Exception as e:  # noqa: BLE001 - connection, timeout, or the tool is missing on the server
        reset()
        raise McpUnavailable(f"MCP server unavailable ({type(e).__name__})") from e
    return _normalise(name, await asyncio.wait_for(tool.ainvoke(args), timeout=30))
