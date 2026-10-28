import json
from pathlib import Path

import pytest

from app.rag import manifest as mf
from app.rag import seeding, stores
from tests.conftest import make_user
from tests.fakes import fake_vec

FIXTURES = Path(__file__).parent / "fixtures" / "seeds"


@pytest.fixture(scope="module", autouse=True)
def offline_embeddings():
    """Seeding logic is tested with tiny offline embeddings, so these tests do not use API quota."""
    mp = pytest.MonkeyPatch()
    mp.setattr(stores, "embed_texts", lambda texts: [fake_vec(t) for t in texts])
    mp.setattr(stores, "embed_query", lambda q: fake_vec(q))
    yield
    mp.undo()
    stores.delete_collection(stores.ROLE_COLLECTION)
    stores.delete_collection(stores.COMPANY_COLLECTION)


@pytest.fixture(scope="module")
def seeded(offline_embeddings):
    stores.delete_collection(stores.ROLE_COLLECTION)
    stores.delete_collection(stores.COMPANY_COLLECTION)
    mf.save_manifest({"roles": {}, "companies": {}})
    return seeding.seed_all(FIXTURES)


def test_validation_passes_on_fixtures():
    assert seeding.validate_dir(FIXTURES) == {}


def test_validation_catches_problems(tmp_path):
    (tmp_path / "role_kb").mkdir()
    (tmp_path / "company_kb").mkdir()
    good = json.loads((FIXTURES / "role_kb" / "genai.json").read_text())
    bad = json.loads(json.dumps(good))
    bad["questions"].append(dict(bad["questions"][0]))  # duplicate id + content
    del bad["questions"][1]["source"]  # missing required metadata
    (tmp_path / "role_kb" / "genai.json").write_text(json.dumps(bad))
    comp = json.loads((FIXTURES / "company_kb" / "amazon.json").read_text())
    del comp["records"][0]["license"]
    comp["records"][1]["confidence"] = "certain"
    (tmp_path / "company_kb" / "amazon.json").write_text(json.dumps(comp))
    failures = seeding.validate_dir(tmp_path)
    assert set(failures) == {"role_kb/genai.json", "company_kb/amazon.json"}
    assert any("source" in e for e in failures["role_kb/genai.json"])
    assert any("license" in e for e in failures["company_kb/amazon.json"])
    assert any("confidence" in e for e in failures["company_kb/amazon.json"])


def test_seed_counts_and_manifest(seeded):
    assert set(mf.supported_roles()) == {"genai", "sde"}
    assert mf.supported_companies() == ["amazon"]
    assert mf.supported_companies("sde") == ["amazon"] and mf.supported_companies("genai") == []
    assert stores.count(stores.ROLE_COLLECTION, {"role": "genai"}) == 6  # 5 questions + 1 resource
    assert stores.count(stores.COMPANY_COLLECTION, {"company": "amazon"}) == 3
    assert mf.load_manifest()["roles"]["genai"]["questions"] == 5


def test_aliases_resolve(seeded):
    assert mf.resolve_company("Amazon Web Services") == "amazon"
    assert mf.resolve_company("AWS") == "amazon"
    assert mf.resolve_company("Globex") is None
    assert mf.resolve_role("Software Engineer") == "sde"
    assert mf.resolve_role("quantum chef") is None


def test_reseed_is_idempotent(seeded):
    seeding.seed_role_file(FIXTURES / "role_kb" / "genai.json")
    assert stores.count(stores.ROLE_COLLECTION, {"role": "genai"}) == 6


def test_atomic_chunks_keep_record_in_metadata(seeded):
    where = stores.where_all(role="genai", source_type="role_question")
    rows = stores.get_chunks(stores.ROLE_COLLECTION, where)
    row = next(r for r in rows if r["id"] == "genai-rag-0001")
    rec = json.loads(row["metadata"]["record"])
    assert len(rec["key_points"]) == 3
    assert row["text"] == rec["question"]  # embedded text is the question; key points stay in metadata
    comp = stores.get_chunks(stores.COMPANY_COLLECTION, {"company": "amazon"})[0]["metadata"]
    assert comp["license"] and comp["confidence"] and comp["year"]


def test_kb_endpoints_and_objects_in_s3(client, seeded):
    u = make_user(client)
    assert client.get("/kb/roles").status_code == 401
    roles = client.get("/kb/roles", headers=u["headers"]).json()["roles"]
    assert {r["role"] for r in roles} == {"genai", "sde"}
    comps = client.get("/kb/companies", headers=u["headers"]).json()["companies"]
    assert [c["company"] for c in comps] == ["amazon"]
    assert client.get("/kb/companies", params={"role": "genai"}, headers=u["headers"]).json()["companies"] == []
    from app.storage import s3

    keys = s3.list_keys("global/")
    assert "global/manifests/kb_manifest.json" in keys and "global/role_kb/genai.json" in keys


def test_mcq_and_company_question_rules():
    from app.rag.seeds import validate_company_seed, validate_role_seed

    base = json.loads((FIXTURES / "role_kb" / "sde.json").read_text())
    mcq = next(q for q in base["questions"] if q["type"] == "mcq")
    assert validate_role_seed(base)[1] == []

    def errors(**changes):
        data = json.loads(json.dumps(base))
        q = next(x for x in data["questions"] if x["type"] == "mcq")
        q.update(changes)
        return validate_role_seed(data)[1]

    assert errors(correct_index=9) and errors(correct_index=None) and errors(explanation="")
    assert errors(options=["same", "same"], correct_index=0)
    # a plain question must not carry options
    data = json.loads(json.dumps(base))
    data["questions"][0]["options"] = ["a", "b"]
    assert validate_role_seed(data)[1]

    comp = json.loads((FIXTURES / "company_kb" / "amazon.json").read_text())
    assert validate_company_seed(comp)[1] == []
    comp["records"][1]["key_points"] = []
    assert validate_company_seed(comp)[1]  # a company question without key points is rejected


def test_mcq_chunk_keeps_options_in_record(seeded):
    rows = stores.get_chunks(stores.ROLE_COLLECTION, stores.where_all(role="sde", kind="mcq"))
    assert [r["id"] for r in rows] == ["sde-os-0001"]
    rec = json.loads(rows[0]["metadata"]["record"])
    assert rec["options"][rec["correct_index"]] == "Shortest job first"
