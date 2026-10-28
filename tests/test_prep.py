"""Phases 5-6: the prep graph end to end through the API, with a scripted model (real Mongo, S3, Chroma, embeddings).

Each test scripts the LLM calls in order: plan_prep, the retrieval agent's turns, generate, the grounding judge,
and (mock mode) evaluate_answer. The fake raises if the code makes an LLM call that was not scripted.
"""
from pathlib import Path

import pytest

from app.rag import llm as llm_mod
from app.rag import manifest as mf
from app.rag import seeding, stores
from app.rag.chunking.base import Chunk
from tests.conftest import make_user
from tests.fakes import fake_vec, scripted, tool_call
from tests.test_phase1 import mk_track

FIXTURES = Path(__file__).parent / "fixtures" / "seeds"


@pytest.fixture(scope="module", autouse=True)
def seeded_kbs():
    """Seed the fixture KBs with tiny offline embeddings (no API calls); scripted agents choose the chunk ids."""
    mp = pytest.MonkeyPatch()
    mp.setattr(stores, "embed_texts", lambda texts: [fake_vec(t) for t in texts])
    mp.setattr(stores, "embed_query", lambda q: fake_vec(q))
    stores.delete_collection(stores.ROLE_COLLECTION)
    stores.delete_collection(stores.COMPANY_COLLECTION)
    mf.save_manifest({"roles": {}, "companies": {}})
    seeding.seed_all(FIXTURES)
    yield
    mp.undo()
    stores.delete_collection(stores.ROLE_COLLECTION)  # other modules re-seed with real embeddings
    stores.delete_collection(stores.COMPANY_COLLECTION)


@pytest.fixture
def fake():
    """Install a scripted model; the test passes its script via fake(...)."""
    def install(*items, strict=True):
        model = scripted(*items, strict=strict)
        llm_mod.set_chat_model_override(model)
        return model

    yield install
    llm_mod.set_chat_model_override(None)


def plan(**kw):
    base = {"intent": "role_questions", "mode": "question_set", "role": "genai", "topics": ["rag"], "count": 2,
            "kbs": ["role"], "confidence": "high"}
    return {**base, **kw}


def gen(*items, notes=()):
    return {"items": list(items), "notes": list(notes)}


def item(question, cites, kb="Role", kps=("point one", "point two")):
    return {"question": question, "key_points": list(kps), "kb": kb, "citations": list(cites), "topic": "rag", "difficulty": "easy"}


JUDGE_OK = {"ok": True, "issues": []}


def gen_citing_first_chunk(prompt):
    """A scripted generate that cites whichever chunk the prompt shows first (fallback retrieval picks its own)."""
    import re

    cid = re.search(r'<chunk id="([^"]+)"', str(prompt)).group(1)
    return gen(item("What is RAG?", [cid]))


def start(client, user, tid, request="Give me 2 GenAI questions on RAG", **body):
    r = client.post(f"/tracks/{tid}/prep", json={"request": request, **body}, headers=user["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def resume(client, user, thread, **body):
    r = client.post(f"/sessions/{thread}/resume", json=body, headers=user["headers"])
    return r


def test_role_questions_with_agent_trace_and_citations(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    model = fake(
        plan(),
        tool_call("search_role", query="retrieval augmented generation", topic="rag", rationale="find RAG questions"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001", "genai-rag-0002", "made-up-id"], notes=[], rationale="enough"),
        gen(item("What is RAG?", ["genai-rag-0001"]), item("How does chunk size matter?", ["genai-rag-0002"])),
        JUDGE_OK,
    )
    out = start(client, u, tid)
    assert out["status"] == "done" and out["interrupt"] is None
    items = out["output"]["items"]
    assert [i["citations"] for i in items] == [["genai-rag-0001"], ["genai-rag-0002"]]
    assert all(i["kb"] == "role" for i in items)
    assert [s["tool"] for s in out["trace"]] == ["search_role", "finish"]
    assert out["trace"][0]["rationale"] == "find RAG questions"
    assert {c["kb"] for c in out["chunks"]} == {"role"}  # the invented id never became a chunk
    assert not model.script  # exactly the scripted calls were made
    sess = client.get(f"/sessions/{out['thread_id']}", headers=u["headers"]).json()
    assert sess["status"] == "done" and sess["result"]["trace"]


def test_prompt_shows_key_points_and_marks_chunks_as_data(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    model = fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    start(client, u, tid)
    generate_prompt = str(model.calls[-2])
    assert '<chunk id="genai-rag-0001" kb="Role">' in generate_prompt and "Key point:" in generate_prompt


def test_unknown_company_falls_back_with_visible_note(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "SDE", "sde")["id"]
    fake(
        plan(intent="company_prep", role="sde", company="Globex", topics=[], kbs=["company", "role"], confidence="high"),
    )
    out = start(client, u, tid, "Prepare me for a Globex SDE interview")
    # a company that is not in the manifest pauses for confirmation and shows the expected fallback
    assert out["status"] == "awaiting_input" and out["interrupt"]["type"] == "confirm_plan"
    assert "no company-specific data" in out["interrupt"]["expected_fallback"].lower()
    assert "company" not in out["interrupt"]["plan"]["kbs"]

    llm_mod.set_chat_model_override(None)
    fake(
        tool_call("search_role", query="software engineer interview", rationale="role level only"),
        tool_call("finish", selected_chunk_ids=["sde-arrays-0001"], notes=[], rationale="done"),
        gen(item("Two numbers that add to a target?", ["sde-arrays-0001"])),
        JUDGE_OK,
    )
    done = resume(client, u, out["thread_id"], approved=True, edits={"count": 1}).json()
    assert done["status"] == "done"
    assert any("No company-specific data" in n for n in done["output"]["notes"])
    assert done["plan"]["count"] == 1


def test_company_prep_uses_company_role_and_personal(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "SDE", "sde")["id"]
    stores.upsert_chunks(
        stores.personal_collection_name(tid),
        [Chunk(chunk_id="doc1:v1:0", kb="personal", text="Led a team of 4 on a warehouse routing project.",
               header="Resume > Experience > Intern", source_type="resume", source_name="resume.pdf",
               metadata={"user_id": u["id"], "track_id": tid, "doc_id": "doc1", "project": "Routing"})],
    )
    fake(
        plan(intent="company_prep", role="sde", company="Amazon Web Services", topics=["graphs"], count=2,
             kbs=["company", "role", "personal"]),
        tool_call("search_company", query="graph problems", company="amazon", rationale="company data"),
        tool_call("search_personal", query="routing project leadership", rationale="behavioural story"),
        tool_call("finish", selected_chunk_ids=["amzn-sde-0003", "doc1:v1:0"], notes=[], rationale="done"),
        gen(item("Reach every city with the fewest warehouses?", ["amzn-sde-0003"], kb="Company"),
            item("Tell me about leading your routing project.", ["doc1:v1:0"], kb="Personal")),
        JUDGE_OK,
    )
    out = start(client, u, tid, "Prepare me for an Amazon SDE interview")
    assert out["status"] == "done", out
    assert [i["kb"] for i in out["output"]["items"]] == ["company", "personal"]
    assert {c["kb"] for c in out["chunks"]} == {"company", "personal"}


def test_clarification_pauses_then_replans(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(plan(clarification_question="Which topics should I focus on?", topics=[], confidence="medium"))
    out = start(client, u, tid, "Ask me some questions")
    assert out["status"] == "awaiting_input" and out["interrupt"]["type"] == "clarification"
    assert client.get(f"/sessions/{out['thread_id']}", headers=u["headers"]).json()["interrupt_type"] == "clarification"

    llm_mod.set_chat_model_override(None)
    model = fake(
        plan(topics=["rag"], count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    done = resume(client, u, out["thread_id"], text="Focus on RAG").json()
    assert done["status"] == "done"
    assert "Focus on RAG" in str(model.calls[0])  # the answer was folded into the request


def test_resume_validates_payload_and_state(client, fake):
    u, other = make_user(client), make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(plan(clarification_question="Which topics?", topics=[]))
    out = start(client, u, tid, "questions please")
    thread = out["thread_id"]
    assert resume(client, u, thread, approved=True).status_code == 422  # wrong payload for a clarification
    assert resume(client, other, thread, text="x").status_code == 404  # other users cannot see the session
    assert client.get(f"/stream/{thread}", headers=other["headers"]).status_code == 404


def test_unsupported_role_after_clarification_lists_supported_roles(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "Chef", "quantum chef")["id"]
    fake(plan(role="quantum chef", clarification_question="Which role do you mean?", topics=[], kbs=[]))
    out = start(client, u, tid, "prep me")
    assert out["interrupt"]["type"] == "clarification"
    llm_mod.set_chat_model_override(None)
    fake(plan(role="quantum chef", topics=[], kbs=[]))
    done = resume(client, u, out["thread_id"], text="quantum chef").json()
    assert done["status"] == "done"
    assert "genai" in done["output"]["notes"][0] and "sde" in done["output"]["notes"][0]


def test_cancelled_confirmation_ends_the_run(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(plan(confidence="low"))
    out = start(client, u, tid)
    assert out["interrupt"]["type"] == "confirm_plan"
    done = resume(client, u, out["thread_id"], approved=False).json()
    assert done["status"] == "done" and done["output"]["items"] == [] and done["output"]["notes"] == ["Plan cancelled."]


def test_confirmation_edits_are_validated_against_the_manifest(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(plan(confidence="low"))
    out = start(client, u, tid)
    llm_mod.set_chat_model_override(None)
    fake(
        tool_call("search_role", query="embeddings", topic="embeddings", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-emb-0001"], notes=[], rationale="d"),
        gen(item("What is an embedding?", ["genai-emb-0001"])),
        JUDGE_OK,
    )
    done = resume(client, u, out["thread_id"], approved=True,
                  edits={"topics": ["embeddings"], "count": 1, "company": "Globex", "role": "chef", "kbs": ["role"]}).json()
    assert done["plan"]["topics"] == ["embeddings"] and done["plan"]["role"] == "genai" and done["plan"]["company"] is None
    assert any(n.startswith("Ignored edit") for n in done["output"]["notes"])


def test_grounding_failure_retries_then_passes(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    model = fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["not-a-retrieved-chunk"])),  # deterministic failure: cites an unknown id
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid)
    assert out["status"] == "done" and out["output"]["items"][0]["citations"] == ["genai-rag-0001"]
    assert "not retrieved" in str(model.calls[-2])  # the issue was fed back into generate


def test_grounding_retries_are_bounded_and_curated_records_replace_unverified_items(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    bad = gen(item("What is RAG?", []))
    fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        bad, bad, bad,  # first attempt plus two retries, then the run moves on
    )
    out = start(client, u, tid)
    assert out["status"] == "done"
    assert [i["citations"] for i in out["output"]["items"]] == [["genai-rag-0001"]]  # the curated record, never an empty page
    assert any("could not be verified" in n for n in out["output"]["notes"])
    assert any("curated records" in n for n in out["output"]["notes"])


def test_unverifiable_set_without_curated_records_explains_what_is_missing(client, fake, monkeypatch):
    monkeypatch.setattr("app.graph.nodes.check_grounding.seed_fallback_items", lambda *a, **k: [])
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    bad = gen(item("What is RAG?", []))
    fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        bad, bad, bad,
    )
    out = start(client, u, tid)
    assert out["status"] == "done" and out["output"]["items"] == []
    assert any("No questions could be produced" in n for n in out["output"]["notes"])


def test_llm_judge_failure_triggers_regeneration(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"], kps=("invented claim",))),
        {"ok": False, "issues": ["item 1: key point not supported"]},
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid)
    assert out["status"] == "done" and out["output"]["items"][0]["key_points"] == ["point one", "point two"]


def test_repeated_searches_that_add_nothing_new_stop_the_agent_and_keep_the_evidence(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    steps = [tool_call("search_role", query=f"RAG variant {i}", rationale="again") for i in range(3)]
    fake(plan(count=1), *steps, gen_citing_first_chunk, JUDGE_OK)
    out = start(client, u, tid)
    assert out["status"] == "done"
    assert out["trace"][-1]["tool"] != "fixed_pipeline"
    assert len(out["trace"]) == 3  # two searches in a row added nothing new, so the loop stopped instead of using all 5 steps
    assert any("repeating itself" in n for n in out["notes"])


def test_agent_with_repeated_tool_errors_falls_back_to_the_fixed_pipeline(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    steps = [tool_call("search_role", query=f"RAG {i}", topic=f"made-up-{i}", rationale="bad filter") for i in range(2)]
    fake(plan(count=1), *steps, gen_citing_first_chunk, JUDGE_OK)
    out = start(client, u, tid)
    assert out["status"] == "done"
    assert out["trace"][-1]["tool"] == "fixed_pipeline"
    assert any("fixed retrieval pipeline" in n for n in out["notes"])


def test_agent_tool_arguments_are_validated_against_the_manifest(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="made-up-topic", rationale="bad filter"),
        tool_call("search_company", query="RAG", company="amazon", rationale="not in the plan"),
        tool_call("search_role", query="RAG", topic="rag", rationale="valid now"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid)
    assert out["trace"][0]["observation"].startswith("error: unknown topic")
    assert "does not use the company" in out["trace"][1]["observation"]


def test_ask_user_interrupts_inside_the_agent_and_resumes(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(
        plan(count=1),
        tool_call("ask_user", question="Beginner or advanced level?", rationale="difficulty unknown"),
    )
    out = start(client, u, tid)
    assert out["status"] == "awaiting_input"
    assert out["interrupt"] == {"type": "clarification", "source": "agent", "question": "Beginner or advanced level?"}
    llm_mod.set_chat_model_override(None)
    model = fake(
        tool_call("search_role", query="RAG", topic="rag", difficulty="easy", rationale="beginner"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    # the retrieve node restarts on resume; the agent loop continues from its checkpoint with the user's answer
    done = resume(client, u, out["thread_id"], text="Beginner").json()
    assert done["status"] == "done"
    assert "The user answered: Beginner" in str(model.calls[0])


def test_stream_replays_node_progress(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(
        plan(count=1),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid)
    with client.stream("GET", f"/stream/{out['thread_id']}", headers=u["headers"]) as r:
        body = "".join(r.iter_text())
    for node in ("plan_prep", "retrieve", "generate", "check_grounding"):
        assert f'"node": "{node}"' in body
    assert '"status": "done"' in body


# ---- Phase 6: mock interview ---------------------------------------------------------------------------------

def score(correct, depth, clarity, covered, feedback="Good.", follow_up=False):
    return {"correctness": correct, "depth": depth, "clarity": clarity, "covered_points": covered,
            "missed_points": [], "feedback": feedback, "follow_up_needed": follow_up}


def test_mock_interview_loop_report_and_expiry(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(
        plan(mode="mock_interview", count=2, topics=["rag"]),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001", "genai-rag-0002"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid, "Mock interview on RAG", mode="mock_interview")
    thread = out["thread_id"]
    assert out["status"] == "awaiting_input" and out["interrupt"]["type"] == "answer"
    assert out["interrupt"]["turn"] == 1 and out["interrupt"]["total"] == 2
    assert "correct_index" not in out["interrupt"]

    llm_mod.set_chat_model_override(None)
    fake(
        score(8, 7, 8, [0], "Covers the retrieval idea.", follow_up=True),  # evaluate turn 1 -> follow-up
        gen(item("Which point about grounding did you skip?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    nxt = resume(client, u, thread, answer="RAG fetches documents and adds them to the prompt.").json()
    assert nxt["status"] == "awaiting_input" and nxt["interrupt"]["follow_up"] is True
    assert nxt["scores"][0]["covered_points"] == [0] and nxt["scores"][0]["missed_points"] == [1]

    llm_mod.set_chat_model_override(None)
    fake(
        score(4, 3, 5, [], "Too brief."),  # evaluate the follow-up -> next planned question, same topic -> generate
        gen(item("How does chunk size matter?", ["genai-rag-0002"])),
        JUDGE_OK,
    )
    nxt = resume(client, u, thread, answer="Not sure.").json()
    assert nxt["interrupt"]["turn"] == 2

    llm_mod.set_chat_model_override(None)
    fake(score(9, 9, 9, [0, 1], "Excellent."))
    done = resume(client, u, thread, answer="Small chunks are precise, large ones keep context.").json()
    assert done["status"] == "done" and done["report"]["overall"] > 0
    assert len(done["scores"]) == 3 and len(done["output"]["items"]) == 3  # scores line up with asked questions
    assert "rag" in done["report"]["weak_areas"] or done["report"]["overall"] >= 6
    saved = client.get(f"/reports/{done['report_id']}", headers=u["headers"]).json()
    assert saved["report"]["overall"] == done["report"]["overall"] and saved["user_id"] == u["id"]
    assert [r["id"] for r in client.get(f"/tracks/{tid}/reports", headers=u["headers"]).json()] == [done["report_id"]]
    assert resume(client, u, thread, answer="again").status_code == 409  # finished sessions cannot be resumed


def test_report_is_private_to_its_owner(client, fake):
    u, other = make_user(client), make_user(client)
    assert client.get("/reports/does-not-exist", headers=u["headers"]).status_code == 404
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    assert client.get(f"/tracks/{tid}/reports", headers=other["headers"]).status_code == 404


def test_expired_session_cannot_be_resumed(client, fake):
    import asyncio
    from datetime import timedelta

    from pymongo import AsyncMongoClient

    from app.config import get_settings
    from app.db.models import utcnow

    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    fake(plan(clarification_question="Which topics?", topics=[]))
    out = start(client, u, tid, "questions please")

    async def age():
        c = AsyncMongoClient(get_settings().mongodb_uri)
        await c[get_settings().mongodb_db].sessions.update_one(
            {"thread_id": out["thread_id"]}, {"$set": {"updated_at": utcnow() - timedelta(hours=48)}}
        )
        await c.close()

    asyncio.run(age())
    r = resume(client, u, out["thread_id"], text="RAG")
    assert r.status_code == 410
    assert client.get(f"/sessions/{out['thread_id']}", headers=u["headers"]).json()["status"] == "expired"


def test_mcq_is_graded_in_code_and_options_come_from_the_seed(client, fake):
    u = make_user(client)
    tid = mk_track(client, u, "SDE", "sde")["id"]
    fake(
        plan(role="sde", topics=["os"], mode="mock_interview", count=1),
        tool_call("search_role", query="scheduling starvation", topic="os", rationale="r"),
        tool_call("finish", selected_chunk_ids=["sde-os-0001"], notes=[], rationale="d"),
        gen({"question": "reworded by the model", "key_points": ["x"], "kb": "Role", "citations": ["sde-os-0001"], "topic": "os"}),
        JUDGE_OK,
    )
    out = start(client, u, tid, "Quiz me on OS", mode="mock_interview")
    q = out["interrupt"]
    assert q["kind"] == "mcq" and q["question"].startswith("Which scheduling algorithm") and len(q["options"]) == 4
    assert "correct_index" not in q
    llm_mod.set_chat_model_override(None)
    fake()  # grading must not call the model at all
    done = resume(client, u, out["thread_id"], answer="1").json()
    assert done["status"] == "done"
    assert done["scores"][0]["correctness"] == 10 and "Correct" in done["scores"][0]["feedback"]


def test_mock_interview_ends_cleanly_when_no_question_can_be_verified(client, fake, monkeypatch):
    monkeypatch.setattr("app.graph.nodes.check_grounding.seed_fallback_items", lambda *a, **k: [])
    """Regression: grounding failing on every retry used to leave no current question and crash await_answer."""
    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    bad = gen(item("What is RAG?", []))
    fake(
        plan(mode="mock_interview", count=2, topics=["rag"]),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        bad, bad, bad,
    )
    out = start(client, u, tid, "Mock interview on RAG", mode="mock_interview")
    assert out["status"] == "done" and out["output"]["items"] == []
    assert any("could not be verified" in n for n in out["output"]["notes"])


def test_private_reference_grades_answers_but_is_never_exposed(client, fake):
    """A copyrighted source's answer is stored as a private `reference`: the grader sees it, no API response does."""
    from app.rag import stores as st

    secret = "SECRET-REFERENCE-TEXT-7731 an owned answer text that must never leave the server"
    rows = st.get_by_ids(st.ROLE_COLLECTION, ["genai-rag-0001"])
    import json as _json

    rec = _json.loads(rows[0]["metadata"]["record"])
    rec["reference"] = secret
    meta = {**rows[0]["metadata"], "record": _json.dumps(rec)}
    st.get_collection(st.ROLE_COLLECTION).update(ids=["genai-rag-0001"], metadatas=[meta])

    u = make_user(client)
    tid = mk_track(client, u, "GenAI", "genai")["id"]
    model = fake(
        plan(mode="mock_interview", count=1, topics=["rag"]),
        tool_call("search_role", query="RAG", topic="rag", rationale="r"),
        tool_call("finish", selected_chunk_ids=["genai-rag-0001"], notes=[], rationale="d"),
        gen(item("What is RAG?", ["genai-rag-0001"])),
        JUDGE_OK,
    )
    out = start(client, u, tid, "Mock interview on RAG", mode="mock_interview")
    assert secret not in _json.dumps(out)  # not in the question, trace, chunks or interrupt
    assert all(secret not in str(c) for c in model.calls[:-1])  # generation and grounding prompts never saw it
    llm_mod.set_chat_model_override(None)
    grader = fake(score(8, 8, 8, [0, 1], "Good."))
    done = resume(client, u, out["thread_id"], answer="RAG retrieves documents and grounds the answer in them.").json()
    assert secret in str(grader.calls[0])  # the grader used it as reference
    assert secret not in _json.dumps(done)  # but the response, feedback and report do not expose it
    saved = client.get(f"/reports/{done['report_id']}", headers=u["headers"]).text
    assert secret not in saved


def test_track_role_aliases_are_stored_as_the_supported_role(client):
    u = make_user(client)
    assert mk_track(client, u, "Gen", "Generative AI Intern")["target_role"] == "genai"  # level words are ignored
    assert mk_track(client, u, "Dev", "Junior SDE")["target_role"] == "sde"
    assert mk_track(client, u, "Chef", "quantum chef")["target_role"] == "quantum chef"  # kept as typed; prep explains it


def test_kbs_edit_cannot_remove_the_knowledge_bases_a_request_needs():
    from app.graph.nodes.confirm_plan import apply_edits
    from app.graph.state import PrepPlan

    plan = PrepPlan(intent="company_prep", mode="question_set", role="aiml", count=5, kbs=["role", "personal"])
    new, problems = apply_edits(plan, {"kbs": ["personal"]})
    assert set(new.kbs) == {"personal", "role"}
    assert any("role knowledge base kept" in p for p in problems)


def test_grounding_judge_is_skipped_for_verbatim_curated_records_but_not_for_model_text():
    from app.graph.nodes.check_grounding import is_verbatim_seed
    from app.graph.state import PrepItem, RetrievedChunk

    rec = {"question": "What is RAG?", "key_points": ["retrieve then generate", "reduces hallucination"], "type": "question"}
    import json

    chunk = RetrievedChunk(chunk_id="genai-rag-0001", kb="role", text="What is RAG?", metadata={"record": json.dumps(rec)})
    by_id = {chunk.chunk_id: chunk}
    verbatim = PrepItem(question="What is RAG?", key_points=list(rec["key_points"]), kb="role", citations=["genai-rag-0001"])
    reworded = PrepItem(question="Explain RAG.", key_points=list(rec["key_points"]), kb="role", citations=["genai-rag-0001"])
    invented = PrepItem(question="What is RAG?", key_points=["retrieve then generate", "used by 73% of teams"], kb="role", citations=["genai-rag-0001"])
    assert is_verbatim_seed(verbatim, by_id)
    assert not is_verbatim_seed(reworded, by_id) and not is_verbatim_seed(invented, by_id)
