"""LangSmith helpers that can never break the app: every function is a no-op when tracing is off or LangSmith is unreachable.

- stage(name): wraps a function in a named span so a trace shows retrieval, prompt construction and generation separately.
- annotate(**fields): adds metadata (retrieved ids, prompt size, ...) to the span that is running.
- feedback(key, score): attaches a score (grounded, citation_valid, ...) to the current trace, so LangSmith can chart the
  hallucination rate of real traffic. Feedback is sent in the background and never blocks or fails a user request.
"""
import asyncio
import inspect
import logging
import os
from functools import wraps
from typing import Any, Callable

log = logging.getLogger(__name__)
_client = None


def tracing_on() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true" and bool(os.environ.get("LANGSMITH_API_KEY"))


def stage(name: str, run_type: str = "chain", outputs: Callable[[Any], dict] | None = None):
    """Decorator: run the function inside a LangSmith span called `name` (only while tracing is on)."""

    def deco(fn):
        try:
            from langsmith import traceable  # noqa: PLC0415

            kw: dict = {"name": name, "run_type": run_type}
            if outputs is not None:
                kw["process_outputs"] = outputs
            traced = traceable(**kw)(fn)
        except Exception:  # noqa: BLE001 - the SDK is optional at runtime
            return fn
        if inspect.iscoroutinefunction(fn):

            @wraps(fn)
            async def awrapper(*a, **k):
                return await (traced if tracing_on() else fn)(*a, **k)

            return awrapper

        @wraps(fn)
        def wrapper(*a, **k):
            return (traced if tracing_on() else fn)(*a, **k)

        return wrapper

    return deco


def _current_run():
    if not tracing_on():
        return None
    try:
        from langsmith.run_helpers import get_current_run_tree  # noqa: PLC0415

        return get_current_run_tree()
    except Exception:  # noqa: BLE001
        return None


def annotate(**fields: Any) -> None:
    """Add metadata to the span that is currently running."""
    rt = _current_run()
    if rt is not None:
        try:
            rt.add_metadata(fields)
        except Exception:  # noqa: BLE001
            log.debug("could not annotate the LangSmith span", exc_info=True)


def current_trace_id() -> str | None:
    rt = _current_run()
    return str(rt.trace_id) if rt is not None else None


def _send_feedback(trace_id: str, key: str, score: float, comment: str | None) -> None:
    global _client
    try:
        from langsmith import Client  # noqa: PLC0415

        _client = _client or Client()
        _client.create_feedback(run_id=trace_id, key=key, score=score, comment=comment)
    except Exception:  # noqa: BLE001
        log.debug("could not send LangSmith feedback %s", key, exc_info=True)


async def feedback(key: str, score: float, comment: str | None = None, trace_id: str | None = None) -> None:
    """Attach a score to the current trace (or the given one). Fire and forget: the request never waits for LangSmith."""
    tid = trace_id or current_trace_id()
    if tid is None:
        return
    asyncio.get_running_loop().run_in_executor(None, _send_feedback, tid, key, float(score), comment)
