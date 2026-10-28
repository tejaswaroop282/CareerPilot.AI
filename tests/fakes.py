"""A scripted chat model so graphs and agents can be tested without calling Gemini.

Each model call (a tool-calling `ainvoke` or a structured-output call) consumes the next item of the script:
  - AIMessage / str: returned as the message (use `tool_call(...)` to build tool-calling messages)
  - pydantic instance or dict: returned as the structured output for `with_structured_output(Schema)`
  - callable(prompt) -> any of the above: lets a test react to the prompt
The model records every prompt it saw in `.calls`, so tests can assert what the agent was shown.
"""
import hashlib
import itertools
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda

_ids = itertools.count(1)


def tool_call(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call_{next(_ids)}"}])


def tool_calls(*calls: tuple[str, dict]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": n, "args": a, "id": f"call_{next(_ids)}"} for n, a in calls]
    )


class FakeChat(BaseChatModel):
    script: list = []
    calls: list = []
    log: list = []  # one entry per consumed script item: "chat" or "structured:<Schema>"
    fallback: Any = None  # callable(prompt) used for every call after the script runs out (e.g. per-job fan-out)
    strict: bool = True  # raise if the script runs out (a test that makes extra LLM calls should fail)

    @property
    def _llm_type(self) -> str:
        return "fake-scripted"

    def _next(self, prompt: Any) -> Any:
        if not self.script:
            if self.fallback is not None:
                return self.fallback(prompt)
            if self.strict:
                raise AssertionError("FakeChat script exhausted: the code made more LLM calls than scripted")
            return AIMessage(content="")
        item = self.script.pop(0)
        return item(prompt) if callable(item) else item

    def _note(self, kind: str) -> None:
        self.log.append(kind)

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(messages)
        self._note("chat")
        item = self._next(messages)
        msg = item if isinstance(item, AIMessage) else AIMessage(content=str(item))
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):
        return self

    def with_structured_output(self, schema, **kwargs):
        def run(prompt):
            self.calls.append(prompt)
            self._note(f"structured:{getattr(schema, '__name__', schema)}")
            item = self._next(prompt)
            if isinstance(item, dict) and isinstance(schema, type):
                return schema(**item)
            return item

        return RunnableLambda(run)


def scripted(*items: Any, strict: bool = True, fallback: Any = None) -> FakeChat:
    return FakeChat(script=list(items), calls=[], log=[], strict=strict, fallback=fallback)


DIM = 8


def fake_vec(text: str) -> list[float]:
    """Deterministic tiny embedding so retrieval tests run offline (dense search is meaningless, BM25 and rerank are real)."""
    h = hashlib.sha256(text.lower().encode()).digest()
    return [b / 255 for b in h[:DIM]]
