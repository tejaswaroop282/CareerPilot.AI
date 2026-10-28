"""In-memory progress events per run for the SSE stream (GET /stream/{thread_id}).

Events are node completions published by the graph runner. They are kept briefly after the run ends so a client
that connects late still gets the full list. Single-process only; fine for one backend container.
"""
import asyncio
from collections import OrderedDict

_MAX_RUNS = 200


class Run:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.finished = False
        self.cond = asyncio.Condition()


_runs: "OrderedDict[str, Run]" = OrderedDict()


def start(thread_id: str) -> Run:
    run = Run()
    _runs[thread_id] = run
    _runs.move_to_end(thread_id)
    while len(_runs) > _MAX_RUNS:
        _runs.popitem(last=False)
    return run


def get(thread_id: str) -> Run | None:
    return _runs.get(thread_id)


async def publish(thread_id: str, event: dict) -> None:
    run = _runs.get(thread_id)
    if run is None:
        return
    async with run.cond:
        run.events.append(event)
        run.cond.notify_all()


async def finish(thread_id: str) -> None:
    run = _runs.get(thread_id)
    if run is None:
        return
    async with run.cond:
        run.finished = True
        run.cond.notify_all()
