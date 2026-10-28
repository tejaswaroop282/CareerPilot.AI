"""Phase 2 acceptance: ingest + build_profile against real Mongo, S3, Chroma and Gemini."""
import time

import pytest

from app.rag import stores
from tests.conftest import make_user
from tests.test_phase1 import mk_track
from scripts.make_sample_resume import build_pdf

NOTES = b"# Vector databases\n\nHNSW is a graph index.\n\n## Trade-offs\n\nRecall versus latency.\n"


def run_ingest(client, user, tid, force=False):
    r = client.post(f"/tracks/{tid}/ingest", params={"force": force}, headers=user["headers"])
    assert r.status_code == 202, r.text
    thread = r.json()["thread_id"]
    for _ in range(120):
        s = client.get(f"/sessions/{thread}", headers=user["headers"]).json()
        if s["status"] != "running":
            return s
        time.sleep(1)
    raise AssertionError("ingest timed out")


@pytest.mark.live
def test_ingest_end_to_end(client, tmp_path):
    u = make_user(client)
    tid = mk_track(client, u)["id"]
    pdf = tmp_path / "resume.pdf"
    build_pdf(pdf)
    rid = client.post(f"/tracks/{tid}/resumes", files={"file": ("resume.pdf", pdf.read_bytes(), "application/pdf")},
                      headers=u["headers"]).json()["id"]
    client.post(f"/tracks/{tid}/documents", files={"file": ("ocular.md", NOTES, "text/markdown")}, headers=u["headers"])

    s = run_ingest(client, u, tid)
    assert s["status"] == "done", s
    col = stores.personal_collection_name(tid)
    resume_chunks = stores.get_chunks(col, {"doc_id": rid})
    headers = {c["metadata"]["header"] for c in resume_chunks}
    assert {"Resume > Projects > Ocular AI", "Resume > Projects > DocuChat RAG", "Resume > Skills"} <= headers
    assert all(c["metadata"]["user_id"] == u["id"] and c["metadata"]["track_id"] == tid for c in resume_chunks)

    docs = client.get(f"/tracks/{tid}/documents", headers=u["headers"]).json()
    assert docs[0]["indexed"] and docs[0]["chunk_count"] >= 2 and docs[0]["chunker_version"] == "v1"
    total = stores.count(col)

    # Re-ingest (forced) replaces chunks without duplicates.
    assert run_ingest(client, u, tid, force=True)["status"] == "done"
    assert stores.count(col) == total
    # Not forced: everything is already indexed, nothing is re-embedded.
    assert run_ingest(client, u, tid)["status"] == "done"
    assert stores.count(col) == total

    # Profile is valid and grounded in the resume.
    prof = client.get(f"/tracks/{tid}/profile", headers=u["headers"]).json()
    skills = {x.lower() for x in prof["skills"]}
    assert "python" in skills and "fastapi" in skills
    assert any("ocular" in p["name"].lower() for p in prof["projects"])
    assert prof["level"]

    # Hybrid building blocks work on the stored chunks.
    qv = stores.embed_query("image classifier for retinal disease")
    top = stores.query_dense(col, qv, stores.where_all(user_id=u["id"], track_id=tid), 3)
    assert "Ocular AI" in top[0]["metadata"]["header"]

    # Deleting a document removes its chunks; deleting the track removes the collection.
    doc_id = docs[0]["id"]
    assert client.delete(f"/tracks/{tid}/documents/{doc_id}", headers=u["headers"]).status_code == 204
    assert stores.count(col, {"doc_id": doc_id}) == 0
    assert client.delete(f"/tracks/{tid}", headers=u["headers"]).status_code == 204
    assert stores.count(col) == 0


def test_ingest_offline_then_auto_discovery(client, tmp_path, monkeypatch):
    """Same pipeline with scripted LLM and tiny embeddings (no API calls), plus the automatic discover hand-off."""
    from app.rag import llm as llm_mod
    from tests.fakes import fake_vec, scripted, tool_call
    from tests.test_jobs import parse_posting

    monkeypatch.setattr(stores, "embed_texts", lambda texts: [fake_vec(t) for t in texts])
    monkeypatch.setattr(stores, "embed_query", lambda q: fake_vec(q))
    profile = {"skills": ["Python", "FastAPI", "PyTorch", "Docker"], "level": "entry",
               "projects": [{"name": "Ocular AI", "summary": "Retinal disease classifier", "tech": ["PyTorch"]}]}
    llm_mod.set_chat_model_override(scripted(
        profile,  # build_profile
        tool_call("search_jobs", query="python fastapi machine learning", rationale="profile skills"),
        tool_call("finish", candidate_job_ids=["fb-006", "fb-009", "fb-021"], rationale="enough"),
        fallback=parse_posting,
    ))
    try:
        u = make_user(client)
        tid = mk_track(client, u)["id"]
        pdf = tmp_path / "resume.pdf"
        build_pdf(pdf)
        rid = client.post(f"/tracks/{tid}/resumes", files={"file": ("resume.pdf", pdf.read_bytes(), "application/pdf")},
                          headers=u["headers"]).json()["id"]
        client.post(f"/tracks/{tid}/documents", files={"file": ("ocular.md", NOTES, "text/markdown")}, headers=u["headers"])
        assert run_ingest(client, u, tid)["status"] == "done"
        col = stores.personal_collection_name(tid)
        headers = {c["metadata"]["header"] for c in stores.get_chunks(col, {"doc_id": rid})}
        assert {"Resume > Projects > Ocular AI", "Resume > Projects > DocuChat RAG", "Resume > Skills"} <= headers
        assert client.get(f"/tracks/{tid}/profile", headers=u["headers"]).json()["skills"][0] == "Python"

        # ingest of the primary resume started the discover graph on its own
        disc = None
        for _ in range(60):
            sessions = client.get("/sessions", params={"track_id": tid}, headers=u["headers"]).json()
            disc = next((s for s in sessions if s["flow"] == "discover"), None)
            if disc and disc["status"] != "running":
                break
            time.sleep(0.5)
        assert disc and disc["status"] == "done", disc
        page = client.get(f"/tracks/{tid}/jobs", headers=u["headers"]).json()
        assert page["matches"] and page["sample_data"] and page["discovery"]["trace"]
    finally:
        llm_mod.set_chat_model_override(None)


def test_github_repos_are_ingested_through_mcp_tools(client, monkeypatch):
    """GitHub content comes from the MCP github tools (mocked here), is indexed with the repo as its project."""
    import asyncio

    from app import github_api
    from app.graph.helpers.github import fetch_github_docs

    monkeypatch.setattr(github_api, "list_repos", lambda u, limit=5: [{"name": "ocular-ai", "owner": u, "description": "Retinal classifier", "language": "Python", "url": "x", "pushed_at": None}])
    monkeypatch.setattr(github_api, "get_readme", lambda o, r: "# Ocular AI\n\nEfficientNet classifier served with FastAPI.\n\n## Training\n\nWeighted cross-entropy.\n")
    monkeypatch.setattr(github_api, "repo_tree", lambda o, r, limit=120: ["train.py", "api/main.py"])
    docs = asyncio.run(fetch_github_docs("octocat"))
    assert docs[0][0] == "github/octocat/ocular-ai" and docs[0][2] == "ocular-ai"
    assert "EfficientNet" in docs[0][1] and "- api/main.py" in docs[0][1]

    def rate_limited(*a, **k):
        raise github_api.GitHubError("GitHub rate limit reached")

    monkeypatch.setattr(github_api, "list_repos", rate_limited)
    assert asyncio.run(fetch_github_docs("octocat")) == []  # ingest continues from uploads alone
    assert asyncio.run(fetch_github_docs(None)) == []
