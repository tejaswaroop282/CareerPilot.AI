"""The shared ReAct loop, driven by a scripted model (no API calls)."""
import pytest
from pydantic import Field

from app.graph.agents.react_loop import ToolArgs, ToolResult, ToolSpec, run_react_agent
from tests.fakes import scripted, tool_call, tool_calls


class LookupArgs(ToolArgs):
    key: str = Field(min_length=1)


class FinishArgs(ToolArgs):
    ids: list[str] = []


def make_tools(log: list, fail_on: set[str] | None = None) -> list[ToolSpec]:
    async def lookup(a: LookupArgs) -> ToolResult:
        log.append(a.key)
        if fail_on and a.key in fail_on:
            raise RuntimeError("backend down")
        return ToolResult(observation=f"found {a.key}", artifacts={a.key: {"v": a.key}})

    return [
        ToolSpec("lookup", "look something up", LookupArgs, run=lookup),
        ToolSpec("finish", "finish", FinishArgs, kind="finish"),
    ]


async def run(script, tools, steps=5):
    return await run_react_agent(system_prompt="sys", task="task", tools=tools, max_steps=steps, llm=scripted(*script))


async def test_finishes_and_records_rationale():
    log: list = []
    res = await run(
        [tool_call("lookup", key="a", rationale="need a"), tool_call("finish", ids=["a"], rationale="done")], make_tools(log)
    )
    assert res.status == "finished" and res.finish_args["ids"] == ["a"]
    assert [s.tool for s in res.trace] == ["lookup", "finish"]
    assert res.trace[0].rationale == "need a" and res.trace[0].args == {"key": "a"}
    assert "found a" in res.trace[0].observation and res.artifacts == {"a": {"v": "a"}}


async def test_step_cap_stops_the_loop():
    log: list = []
    script = [tool_call("lookup", key=str(i), rationale="more") for i in range(10)]
    res = await run(script, make_tools(log), steps=3)
    assert res.status == "cap" and len(log) == 3


async def test_no_progress_stops_on_repeated_call():
    log: list = []
    script = [tool_call("lookup", key="same", rationale="r1"), tool_call("lookup", key="same", rationale="a different reason")]
    res = await run(script, make_tools(log))
    assert res.status == "no_progress" and len(log) == 2


async def test_invalid_arguments_return_an_observation_then_recover():
    log: list = []
    res = await run(
        [tool_call("lookup", key="", rationale="bad"), tool_call("lookup", key="ok", rationale="fixed"),
         tool_call("finish", rationale="done")],
        make_tools(log),
    )
    assert res.status == "finished" and log == ["ok"]
    assert res.trace[0].observation.startswith("error: invalid arguments")


async def test_a_tool_failing_twice_ends_with_error():
    log: list = []
    script = [tool_call("lookup", key="x", rationale="1"), tool_call("lookup", key="y", rationale="2")]
    res = await run(script, make_tools(log, fail_on={"x", "y"}))
    assert res.status == "error"
    assert "backend down" in res.trace[0].observation


async def test_unknown_tool_is_reported_not_run():
    res = await run([tool_call("drop_tables", rationale="x"), tool_call("finish", rationale="d")], make_tools([]))
    assert res.status == "finished" and "unknown tool" in res.trace[0].observation


async def test_model_without_tool_calls_is_reported():
    res = await run(["I am done."], make_tools([]))
    assert res.status == "no_tool_call"


async def test_parallel_calls_in_one_step_run_together():
    log: list = []
    res = await run(
        [tool_calls(("lookup", {"key": "a", "rationale": "r"}), ("lookup", {"key": "b", "rationale": "r"})),
         tool_call("finish", rationale="d")],
        make_tools(log),
    )
    assert res.status == "finished" and sorted(log) == ["a", "b"] and res.trace[0].step == res.trace[1].step == 1


async def test_tool_results_are_labelled_as_data():
    llm = scripted(tool_call("lookup", key="a", rationale="r"), tool_call("finish", rationale="d"))
    await run_react_agent(system_prompt="sys", task="task", tools=make_tools([]), max_steps=4, llm=llm)
    last_prompt = llm.calls[-1]
    assert any("data, not instructions" in str(getattr(m, "content", "")) for m in last_prompt)


@pytest.mark.parametrize("n", [0])
async def test_identity_is_never_a_tool_argument(n):
    from app.graph.agents.retrieval import build_retrieval_tools
    from app.graph.state import PrepPlan

    plan = PrepPlan(intent="role_questions", mode="question_set", role="sde", kbs=["role"])
    for t in build_retrieval_tools(user_id="u", track_id="t", plan=plan):
        assert not {"user_id", "track_id"} & set(t.args_schema.model_fields)
