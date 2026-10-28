"""LangSmith helpers must never change behaviour or raise: tracing is off in tests, so every helper is a no-op."""
import asyncio

from app import observability as obs


def test_stage_is_transparent_for_sync_and_async_functions():
    @obs.stage("sync_stage")
    def add(a, b):
        return a + b

    @obs.stage("async_stage", outputs=lambda r: {"n": r})
    async def mul(a, b):
        return a * b

    assert add(2, 3) == 5
    assert asyncio.run(mul(4, 5)) == 20


def test_annotate_and_feedback_do_nothing_without_tracing():
    assert obs.tracing_on() is False
    obs.annotate(anything="ignored")
    assert obs.current_trace_id() is None
    asyncio.run(obs.feedback("grounded", 1.0))  # returns immediately, never raises


def test_failure_stage_classifier_attributes_each_error_to_one_stage():
    from evals.langsmith_rag_diagnosis import stage_of

    assert stage_of(None, 8, False, False, None, False) == "retrieval_miss"      # gold never reached the context
    assert stage_of(12, 8, True, False, None, False) == "retrieval_miss"         # ranked beyond the 8 chunks the model sees
    assert stage_of(3, 8, False, False, None, False) == "prompt_drop"            # retrieved but missing from the prompt
    assert stage_of(2, 8, True, True, 0.2, False) == "generation_error"         # in the prompt, answer ignores it
    assert stage_of(7, 8, True, False, None, False) == "ranking_near_miss"       # in the context, just outside the top 5
    assert stage_of(1, 8, True, True, 0.9, True) == "ok"
