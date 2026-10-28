"""Phase 7: job search agent, ranking fan-out, tracker, job_matches, Prepare from a job (scripted model, no API calls)."""
import re
import time

import pytest

from app.db.client import get_db
from app.graph.helpers.pii import strip_pii
from app.graph.nodes.rank_jobs import JobAnalysis, norm_skill, score_from_skills
from app.rag import llm as llm_mod
from tests.conftest import make_user
from tests.fakes import scripted, tool_call
from tests.test_phase1 import mk_track

PROFILE = {"skills": ["Python", "FastAPI", "MongoDB", "Docker", "RAG", "Vector Databases", "Embeddings"],
           "projects": [{"name": "DocuChat RAG", "summary": "RAG over PDFs", "tech": ["Python"]}], "level": "entry"}


def parse_posting(prompt) -> dict:
    """Scripted job parser: reads 'Required: ...' and 'Nice to have: ...' sentences from the posting text."""
    text = str(prompt)
    req = re.search(r"Required: (.+?)\. ", text)
    nice = re.search(r"Nice to have: (.+?)\.", text)
    split = lambda m: [x.strip() for x in re.split(r",| or ", m.group(1)) if x.strip()] if m else []  # noqa: E731
    return {"required_skills": split(req), "nice_to_have": split(nice), "fit_note": "A role matching the posting."}


@pytest.fixture
def fake():
    def install(*items, **kw):
        m = scripted(*items, fallback=parse_posting, **kw)
        llm_mod.set_chat_model_override(m)
        return m

    yield install
    llm_mod.set_chat_model_override(None)


async def _set_profile(track_id, user_id):
    await get_db().tracks.update_one({"_id": track_id, "user_id": user_id}, {"$set": {"profile": PROFILE}})


def wait_done(client, user, thread):
    for _ in range(60):
        s = client.get(f"/sessions/{thread}", headers=user["headers"]).json()
        if s["status"] != "running":
            return s
        time.sleep(0.5)
    raise AssertionError("discover timed out")


def refresh(client, user, tid):
    r = client.post(f"/tracks/{tid}/jobs/refresh", headers=user["headers"])
    assert r.status_code == 202
    return wait_done(client, user, r.json()["thread_id"])


def prepare_track(client, role="genai"):
    import asyncio

    u = make_user(client)
    tid = mk_track(client, u, "GenAI", role)["id"]
    asyncio.run(_set_profile_sync(tid, u["id"]))
    return u, tid


async def _set_profile_sync(tid, uid):
    from pymongo import AsyncMongoClient

    from app.config import get_settings

    c = AsyncMongoClient(get_settings().mongodb_uri)
    await c[get_settings().mongodb_db].tracks.update_one({"_id": tid, "user_id": uid}, {"$set": {"profile": PROFILE}})
    await c.close()


def test_discover_ranks_jobs_skips_tracked_and_persists(client, fake):
    from app.jobs import tracker

    u, tid = prepare_track(client)
    tracker.save_application(u["id"], tid, "fb-004", "applied", None, {"title": "Generative AI Engineer", "company": "Nimbus Labs"})
    fake(
        tool_call("list_applications", rationale="see what is tracked"),
        tool_call("search_jobs", query="generative ai rag python", rationale="profile is RAG heavy"),
        tool_call("finish", candidate_job_ids=["fb-004", "fb-005", "fb-021", "fb-016", "fb-024"], notes=["skipped tracked jobs"], rationale="enough"),
    )
    s = refresh(client, u, tid)
    assert s["status"] == "done"
    page = client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()
    ids = [m["job_id"] for m in page["matches"]]
    assert "fb-004" not in ids and ids  # tracked job skipped in code even though the agent listed it
    scores = [m["score"] for m in page["matches"]]
    # fewer than JOBS_MIN_SHOWN jobs clear the bar, so the best partial fits follow them, never below the floor
    assert scores == sorted(scores, reverse=True) and scores[0] >= 0.5 and all(sc >= 0.15 for sc in scores)
    top = page["matches"][0]
    assert top["reasons"] and top["fit_note"] and "missing" in top
    assert page["sample_data"] is True and all(m["source"] == "fallback" for m in page["matches"])
    assert [t["tool"] for t in page["discovery"]["trace"]] == ["list_applications", "search_jobs", "finish"]
    # reopening never re-runs the agents: a second read is stable
    assert client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()["matches"] == page["matches"]


def test_refresh_replaces_matches(client, fake):
    u, tid = prepare_track(client)
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("finish", candidate_job_ids=["fb-004", "fb-005"], rationale="d"))
    refresh(client, u, tid)
    first = {m["job_id"] for m in client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()["matches"]}
    llm_mod.set_chat_model_override(None)
    fake(tool_call("search_jobs", query="applied ai", rationale="r"), tool_call("finish", candidate_job_ids=["fb-021"], rationale="d"))
    refresh(client, u, tid)
    second = {m["job_id"] for m in client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()["matches"]}
    assert second == {"fb-021"} and first != second


def test_agent_failure_uses_single_query_fallback(client, fake):
    u, tid = prepare_track(client)
    fake("I could not decide what to do.")  # no tool call: the loop ends with no_tool_call
    s = refresh(client, u, tid)
    assert s["status"] == "done"
    page = client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()
    assert page["discovery"]["trace"][-1]["tool"] == "single_query_fallback"
    assert page["matches"]  # still produced results


def test_agent_cannot_finish_with_unseen_ids(client, fake):
    u, tid = prepare_track(client)
    fake(
        tool_call("finish", candidate_job_ids=["fb-004"], rationale="guessing"),  # never searched: rejected
        tool_call("search_jobs", query="rag", rationale="search first"),
        tool_call("finish", candidate_job_ids=["fb-004"], rationale="now valid"),
    )
    refresh(client, u, tid)
    page = client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()
    assert page["discovery"]["trace"][0]["observation"].startswith("error: these ids were never returned")


def test_tracker_and_dismiss_and_isolation(client, fake):
    u, tid = prepare_track(client)
    other = make_user(client)
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("finish", candidate_job_ids=["fb-004", "fb-005"], rationale="d"))
    refresh(client, u, tid)
    h = u["headers"]
    row = client.post(f"/tracks/{tid}/applications", json={"job_id": "fb-004", "status": "applied"}, headers=h)
    assert row.status_code == 201
    rows = client.get(f"/tracks/{tid}/applications", headers=h).json()
    assert [r["job_id"] for r in rows] == ["fb-004"] and rows[0]["status"] == "applied" and rows[0]["title"]
    assert client.patch(f"/tracks/{tid}/applications/{row.json()['id']}", json={"status": "interview"}, headers=h).json()["status"] == "interview"
    assert client.post(f"/tracks/{tid}/applications", json={"job_id": "fb-999"}, headers=h).status_code == 404  # not one of my matches
    assert client.post(f"/tracks/{tid}/jobs/fb-005/dismiss", headers=h).status_code == 204
    assert "fb-005" not in [m["job_id"] for m in client.get(f"/tracks/{tid}/jobs", headers=h).json()["matches"]]
    # another user sees none of it
    oh = other["headers"]
    assert client.get(f"/tracks/{tid}/jobs", headers=oh).status_code == 404
    assert client.get(f"/tracks/{tid}/applications", headers=oh).status_code == 404
    assert client.patch(f"/tracks/{tid}/applications/{row.json()['id']}", json={"status": "offer"}, headers=oh).status_code == 404
    other_track = mk_track(client, other)["id"]
    assert client.get(f"/tracks/{other_track}/applications", headers=oh).json() == []


def test_prepare_from_job_uses_stored_snapshot(client, fake):
    u, tid = prepare_track(client)
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("finish", candidate_job_ids=["fb-004"], rationale="d"))
    refresh(client, u, tid)
    llm_mod.set_chat_model_override(None)
    model = fake({"intent": "company_prep", "mode": "question_set", "role": "genai", "company": "Nimbus Labs", "topics": ["rag"],
                  "count": 3, "kbs": ["company", "role"], "confidence": "high"})
    r = client.post(f"/tracks/{tid}/prep", json={"job_id": "fb-004"}, headers=u["headers"])
    out = r.json()
    prompt = str(model.calls[0])
    assert "Generative AI Engineer" in prompt and "Nimbus Labs" in prompt and 'untrusted="true"' in prompt
    assert out["status"] == "awaiting_input" and out["interrupt"]["type"] == "confirm_plan"  # unknown company: fallback shown
    assert client.post(f"/tracks/{tid}/prep", json={"job_id": "fb-020"}, headers=u["headers"]).status_code == 404  # not a stored match


def test_job_scoring_is_code_and_explainable():
    a = JobAnalysis(required_skills=["Python", "RAG", "Kubernetes"], nice_to_have=["Docker", "Terraform"], fit_note="x")
    m = score_from_skills(a, {norm_skill(s) for s in PROFILE["skills"]}, "")
    assert m.missing == ["Kubernetes"]
    assert m.score == pytest.approx(0.75 * 2 / 3 + 0.25 * 1 / 2, abs=1e-3)
    assert score_from_skills(JobAnalysis(), set(), "").score == 0.0
    # evidence from the personal KB counts too
    m2 = score_from_skills(a, set(), "deployed with kubernetes and terraform; python rag app")
    assert m2.missing == [] and m2.score == pytest.approx(0.875)


def test_pii_and_bias_signals_are_stripped():
    raw = ("Contact priya.sharma@example.com or +91 98765 43210. She studied at IIT Madras and Stanford University. "
           "Photo attached. See https://github.com/priya for her projects. Mr. Rao will interview.")
    out = strip_pii(raw)
    for leaked in ("priya.sharma", "98765", "IIT", "Stanford", "github.com", "She ", "Rao", "Photo attached"):
        assert leaked not in out
    assert "Python" in strip_pii("Strong Python and SQL skills")


def test_scoring_prompt_contains_no_pii(client, fake):
    u, tid = prepare_track(client)
    from app.graph.nodes import rank_jobs as rj

    model = scripted(fallback=parse_posting)
    llm_mod.set_chat_model_override(model)
    import asyncio

    job = {"id": "x", "title": "Engineer", "description": "Required: Python. Email hr@corp.com. Graduates of IIT preferred. He will lead."}
    asyncio.run(rj.score_job({"job": job, "profile_skills": [], "evidence": ""}))
    sent = str(model.calls[0])
    assert "hr@corp.com" not in sent and "IIT" not in sent and "He will" not in sent


def test_discover_run_is_checkpointed_and_purged_with_the_track(client, fake):
    import asyncio

    from app.graph.checkpoint import get_checkpointer

    u, tid = prepare_track(client)
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("finish", candidate_job_ids=["fb-004"], rationale="d"))
    s = refresh(client, u, tid)
    cp = get_checkpointer()
    assert cp.get_tuple({"configurable": {"thread_id": s["thread_id"]}}) is not None  # resumable thread exists
    assert client.delete(f"/tracks/{tid}", headers=u["headers"]).status_code == 204
    assert cp.get_tuple({"configurable": {"thread_id": s["thread_id"]}}) is None  # purged with the track


def test_mcp_unavailable_is_visible_not_silent(client, fake, monkeypatch):
    """With the MCP server unreachable the job agent errors out, the single-query fallback runs in-process, and the
    discovery notes and API say so."""
    from app import mcp_client
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "mcp_mode", "auto")
    monkeypatch.setattr(get_settings(), "mcp_server_url", "http://127.0.0.1:9/mcp")  # nothing listens here
    mcp_client.reset()
    u, tid = prepare_track(client)
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("search_jobs", query="rag python", rationale="again"))
    s = refresh(client, u, tid)
    assert s["status"] == "done"
    page = client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()
    assert page["discovery"]["trace"][-1]["tool"] == "single_query_fallback"
    assert any("MCP server was unreachable" in n for n in page["discovery"]["notes"])
    assert client.get(f"/tracks/{tid}/applications", headers=u["headers"]).status_code == 503


def test_live_jobs_mapping_and_fallback_labels(monkeypatch):
    from app.config import get_settings
    from app.jobs import provider

    sample = [{"id": 1, "position": "Python Developer", "company": "Acme", "location": "", "description": "<p>Build <b>APIs</b> with Python &amp; FastAPI</p>",
               "tags": ["python", "api"], "url": "https://remoteok.com/remote-jobs/1"},
              {"id": 2, "position": "Nurse", "company": "Clinic", "description": "care", "tags": [], "url": "https://remoteok.com/remote-jobs/2"}]

    class Resp:
        def raise_for_status(self): pass
        def json(self): return [{"legal": "notice"}] + sample

    monkeypatch.setattr(get_settings(), "jobs_source", "remoteok")
    monkeypatch.setattr(provider.httpx, "get", lambda *a, **k: Resp())
    provider._cache.clear()
    jobs = provider.search_jobs("python fastapi developer")
    assert [j["id"] for j in jobs] == ["rok-1"] and jobs[0]["source"] == "live" and jobs[0]["location"] == "Remote"
    assert "<" not in jobs[0]["description"] and "Python & FastAPI" in jobs[0]["description"]
    assert provider.get_job("rok-1")["title"] == "Python Developer"

    # live API failure or no results -> labelled sample data
    def boom(*a, **k): raise provider.httpx.ConnectError("down")
    provider._cache.clear()
    monkeypatch.setattr(provider.httpx, "get", boom)
    fb = provider.search_jobs("python fastapi developer")
    assert fb and all(j["source"] == "fallback" for j in fb)


def test_multiple_live_sources_are_merged_deduplicated_and_labelled(monkeypatch):
    from app.config import get_settings
    from app.jobs import provider

    feeds = {
        "remoteok.com": [{"legal": "x"}, {"id": 1, "position": "Python Developer", "company": "Acme", "description": "python apis",
                                          "tags": ["python"], "url": "https://remoteok.com/1"}],
        "remotive.com": {"jobs": [{"id": 7, "title": "Python Developer", "company_name": "ACME", "description": "same job again",
                                   "tags": ["python"], "url": "https://remotive.com/7", "candidate_required_location": "Worldwide"},
                                  {"id": 8, "title": "Backend Engineer (Python)", "company_name": "Beta", "description": "python services",
                                   "tags": [], "url": "https://remotive.com/8", "candidate_required_location": "India"}]},
        "jobicy.com": {"jobs": [{"id": 3, "jobTitle": "Python Engineer", "companyName": "Gamma", "jobGeo": ["India"],
                                 "jobDescription": "<p>python</p>", "url": "https://jobicy.com/3"}]},
    }

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    def fake_get(url, *a, **k):
        for host, data in feeds.items():
            if host in url:
                return Resp(data)
        raise provider.httpx.ConnectError("down")  # arbeitnow and himalayas are unreachable in this test

    monkeypatch.setattr(get_settings(), "jobs_source", "auto")
    monkeypatch.setattr(get_settings(), "jobs_api_key", "")
    monkeypatch.setattr(get_settings(), "jooble_api_key", "")
    monkeypatch.setattr(provider.httpx, "get", fake_get)
    for c in (provider._cache, provider._query_cache, provider._seen):
        c.clear()
    jobs = provider.search_jobs("python developer", "India", limit=10)
    assert {j["site"] for j in jobs} == {"RemoteOK", "Remotive", "Jobicy"}  # failing sources are skipped
    assert sum(j["title"] == "Python Developer" for j in jobs) == 1  # same title and company on two sites counts once
    assert all(j["source"] == "live" and j["url"].startswith("https://") and "tags" not in j for j in jobs)
    assert provider.get_job("rem-8")["company"] == "Beta"
    links = provider.search_links("software engineer")
    assert {"LinkedIn", "Naukri"} <= {link["site"] for link in links}


def test_changing_primary_resume_shows_refresh_prompt(client, fake, tmp_path):
    from scripts.make_sample_resume import build_pdf

    pdf = tmp_path / "r.pdf"
    build_pdf(pdf)
    data = pdf.read_bytes()
    u, tid = prepare_track(client)
    h = u["headers"]
    first = client.post(f"/tracks/{tid}/resumes", files={"file": ("a.pdf", data, "application/pdf")}, headers=h).json()["id"]
    fake(tool_call("search_jobs", query="rag", rationale="r"), tool_call("finish", candidate_job_ids=["fb-004", "fb-005"], rationale="d"))
    refresh(client, u, tid)
    assert client.get(f"/tracks/{tid}/jobs", headers=h).json()["needs_refresh"] is False
    second = client.post(f"/tracks/{tid}/resumes", files={"file": ("b.pdf", data, "application/pdf")}, headers=h).json()["id"]
    assert client.patch(f"/tracks/{tid}/resumes/{second}", headers=h).status_code == 200
    assert client.get(f"/tracks/{tid}/jobs", headers=h).json()["needs_refresh"] is True  # prompt, not a silent re-run
    assert first != second


def test_sessions_left_running_by_a_restart_are_marked_failed(monkeypatch):
    import asyncio
    import uuid

    from pymongo import AsyncMongoClient

    from app.config import get_settings
    from app.db import repos
    from app.db.models import utcnow

    async def go():
        c = AsyncMongoClient(get_settings().mongodb_uri)
        db = c[get_settings().mongodb_db]
        monkeypatch.setattr(repos, "get_db", lambda: db)  # a client bound to this event loop
        a, b = uuid.uuid4().hex, uuid.uuid4().hex
        await db.sessions.insert_many([
            {"thread_id": a, "user_id": "u", "track_id": "t", "flow": "discover", "status": "running", "updated_at": utcnow()},
            {"thread_id": b, "user_id": "u", "track_id": "t", "flow": "prep", "status": "awaiting_input", "updated_at": utcnow()},
        ])
        try:
            assert await repos.fail_orphaned_sessions() >= 1
            assert (await db.sessions.find_one({"thread_id": a}))["status"] == "failed"
            assert (await db.sessions.find_one({"thread_id": b}))["status"] == "awaiting_input"  # paused runs survive restarts
        finally:
            await db.sessions.delete_many({"thread_id": {"$in": [a, b]}})
            await c.close()

    asyncio.run(go())


def test_parsed_posting_is_cached_so_a_refresh_makes_no_model_call(monkeypatch):
    import asyncio
    import uuid

    from pymongo import AsyncMongoClient

    from app.config import get_settings
    from app.graph.nodes import rank_jobs as rj

    calls = []

    class CountingLLM:
        def with_structured_output(self, schema):
            return self

        async def ainvoke(self, messages):
            calls.append(1)
            return JobAnalysis(required_skills=["python", "fastapi"], nice_to_have=["docker"], fit_note="backend role")

    monkeypatch.setattr(get_settings(), "job_analysis_cache", True)
    monkeypatch.setattr(rj, "get_chat_model", lambda *a, **k: CountingLLM())
    job = {"id": f"cache-test-{uuid.uuid4().hex[:8]}", "title": "Backend Engineer", "description": "Python and FastAPI services."}
    payload = {"job": job, "profile_skills": ["python"], "evidence": ""}

    async def go():
        c = AsyncMongoClient(get_settings().mongodb_uri)
        db = c[get_settings().mongodb_db]
        monkeypatch.setattr(rj, "get_db", lambda: db)
        try:
            first = await rj.score_job(payload)
            second = await rj.score_job(payload)  # a later Refresh sees the same posting
            changed = await rj.score_job({**payload, "job": {**job, "description": "Now it needs Go and Kubernetes."}})
            return first, second, changed
        finally:
            await db.job_analyses.delete_many({"_id": {"$regex": f"^{job['id']}:"}})
            await c.close()

    first, second, changed = asyncio.run(go())
    assert first["scored"][0].score == second["scored"][0].score
    assert len(calls) == 2  # one for the posting, none for the repeat, one more because the posting text changed


def test_adzuna_calls_are_capped_cached_and_india_only(monkeypatch):
    """The free Adzuna key allows 25 calls in total: the app must never exceed its own lower cap, even across restarts."""
    from app.config import get_settings
    from app.jobs import provider

    calls = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"results": [{"id": 1, "title": "Python Developer", "company": {"display_name": "Acme"},
                                              "location": {"display_name": "Pune, India"}, "description": "python", "redirect_url": "https://x"}]}

    def fake_get(url, *a, **k):
        calls.append(url)
        return Resp()

    s = get_settings()
    monkeypatch.setattr(s, "jobs_source", "adzuna")
    monkeypatch.setattr(s, "jobs_api_key", "id:key")
    monkeypatch.setattr(s, "jobs_adzuna_max_calls", 2)
    monkeypatch.setattr(provider.httpx, "get", fake_get)
    db = provider._quota_db()
    db.api_quota.delete_many({})
    db.adzuna_cache.delete_many({})
    try:
        assert provider.search_jobs("python developer", "India", 5)[0]["site"] == "Adzuna"
        provider.search_jobs("python developer", "India", 5)                 # same query: served from the cache
        assert len(calls) == 1
        provider.search_jobs("java developer", "India", 5)                    # second distinct query uses the last allowed call
        provider.search_jobs("rust developer", "India", 5)                    # cap reached: no HTTP call, sample data instead
        assert len(calls) == 2 and provider.adzuna_calls_used() == 2
        provider.search_jobs("go developer", "Berlin", 5)                     # not India: never asked
        assert len(calls) == 2
    finally:
        db.api_quota.delete_many({})
        db.adzuna_cache.delete_many({})


def test_a_failed_source_cools_down_instead_of_timing_out_on_every_search(monkeypatch):
    from app.config import get_settings
    from app.jobs import provider

    calls = []

    class Resp:
        def raise_for_status(self): pass
        def json(self): return [{"legal": "x"}, {"id": 1, "position": "Python Developer", "company": "Acme", "description": "python", "tags": [], "url": "https://r/1"}]

    def fake_get(url, *a, **k):
        calls.append(url)
        if "jobicy" in url:
            raise provider.httpx.ConnectError("down")
        return Resp()

    monkeypatch.setattr(get_settings(), "jobs_source", "remoteok,jobicy")
    monkeypatch.setattr(provider.httpx, "get", fake_get)
    for c in (provider._cache, provider._query_cache, provider._seen, provider._cooldown):
        c.clear()
    provider.search_jobs("python developer", None, 5)
    provider.search_jobs("python developer", None, 5)
    provider._cache.clear()  # force RemoteOK to be fetched again so only the cooldown explains the missing Jobicy call
    provider.search_jobs("python developer", None, 5)
    assert sum("jobicy" in c for c in calls) == 1  # one failed attempt, then skipped while cooling down
    provider._cooldown.clear()
