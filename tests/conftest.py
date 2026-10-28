"""Tests run against the real Mongo and S3 services (docker compose), in a separate test database."""
import os
import uuid

os.environ["MONGODB_DB"] = "careerpilot_test"
os.environ["S3_BUCKET"] = "careerpilot-test"
os.environ["CHROMA_DIR"] = "/tmp/careerpilot_test_chroma"
os.environ["MCP_MODE"] = "local"
os.environ["JOB_ANALYSIS_CACHE"] = "false"  # tests that need it switch it on; others must not share state
os.environ["LANGSMITH_TRACING"] = "false"  # tests never send traces or feedback to LangSmith
os.environ["JOBS_SOURCE"] = "fallback"  # tests never call a live jobs API
os.environ["MANIFEST_PATH"] = "/tmp/careerpilot_test_manifest.json"

import pytest
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


def make_user(client: TestClient) -> dict:
    email = f"{uuid.uuid4().hex[:10]}@example.com"
    r = client.post("/auth/register", json={"email": email, "password": "correct-horse-1"})
    assert r.status_code == 201, r.text
    body = r.json()
    return {"id": body["user"]["id"], "email": email, "headers": {"Authorization": f"Bearer {body['access_token']}"}}


MINI_PDF = b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"


@pytest.fixture
def two_users(client):
    return make_user(client), make_user(client)


@pytest.fixture(autouse=True)
def _reset_job_source_cooldowns():
    """A source that failed in one test must not stay on cooldown for the next."""
    from app.jobs import provider

    provider._cooldown.clear()
    yield
    provider._cooldown.clear()
