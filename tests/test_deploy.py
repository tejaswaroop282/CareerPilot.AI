"""Deployment safety: the production guard refuses unsafe settings, and bucket creation works on real S3 outside us-east-1."""
import pytest
from botocore.exceptions import ClientError

from app.config import Settings, production_problems
from app.storage import s3


def good(**over) -> Settings:
    base = dict(app_env="production", jwt_secret="x" * 40, cors_origins="https://careerpilot.example.com",
                mongodb_uri="mongodb+srv://user:pw@cluster0.abcde.mongodb.net", s3_endpoint_url="", llm_provider="groq", groq_api_key="gsk_x")
    return Settings(**{**base, **over})


def test_production_guard_accepts_a_cloud_configuration():
    assert production_problems(good()) == []


@pytest.mark.parametrize("over,needle", [
    ({"jwt_secret": "change-me"}, "JWT_SECRET"),
    ({"jwt_secret": "short"}, "JWT_SECRET"),
    ({"cors_origins": "http://localhost:5173"}, "CORS_ORIGINS"),
    ({"mongodb_uri": "mongodb://mongo:27017"}, "MONGODB_URI"),
    ({"s3_endpoint_url": "http://minio:9000"}, "S3_ENDPOINT_URL"),
    ({"groq_api_key": ""}, "GROQ_API_KEY"),
])
def test_production_guard_rejects_local_or_default_settings(over, needle):
    assert any(needle in p for p in production_problems(good(**over)))


class FakeS3:
    def __init__(self):
        self.created = None

    def head_bucket(self, Bucket):  # noqa: N803
        raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

    def create_bucket(self, **kw):
        self.created = kw


def test_bucket_creation_sets_the_location_constraint_on_aws_outside_us_east_1(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(s3, "get_s3", lambda: fake)
    monkeypatch.setattr(s3.get_settings(), "s3_endpoint_url", "")
    monkeypatch.setattr(s3.get_settings(), "aws_region", "ap-south-1")
    s3.ensure_bucket()
    assert fake.created["CreateBucketConfiguration"] == {"LocationConstraint": "ap-south-1"}


def test_bucket_creation_has_no_constraint_for_a_local_store(monkeypatch):
    fake = FakeS3()
    monkeypatch.setattr(s3, "get_s3", lambda: fake)
    monkeypatch.setattr(s3.get_settings(), "s3_endpoint_url", "http://minio:9000")
    s3.ensure_bucket()
    assert "CreateBucketConfiguration" not in fake.created


# --- secrets, key pool, daily budget, readiness ------------------------------------------------------------------

def test_secret_from_parameter_store_is_cached_and_falls_back(monkeypatch):
    from app import secrets

    calls = []

    class FakeSsm:
        def get_parameter(self, Name, WithDecryption):
            calls.append(Name)
            if Name.endswith("MISSING"):
                raise RuntimeError("denied")
            return {"Parameter": {"Value": " key-from-ssm \n"}}

    monkeypatch.setenv("SECRETS_BACKEND", "ssm")
    from app.config import get_settings
    get_settings.cache_clear()
    secrets.clear_cache()
    monkeypatch.setattr(secrets, "_ssm", lambda: FakeSsm())
    assert secrets.get_secret("GROQ_API_KEY", "env-key") == "key-from-ssm"
    assert secrets.get_secret("GROQ_API_KEY", "env-key") == "key-from-ssm"
    assert calls == ["/careerpilot/GROQ_API_KEY"]  # second read came from the 60 s cache
    assert secrets.get_secret("MISSING", "env-key") == "env-key"  # an unreachable store must not break a request
    get_settings.cache_clear()
    secrets.clear_cache()


def test_secret_uses_environment_by_default():
    from app import secrets

    assert secrets.get_secret("GROQ_API_KEY", "env-key") == "env-key"


def test_groq_key_pool_order(monkeypatch):
    from app.config import get_settings
    from app.rag import llm

    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "k1")
    monkeypatch.setenv("GROQ_API_KEYS", "k2, k1 ,k3")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    try:
        m = llm.get_chat_model()
        assert m.groq_api_key.get_secret_value() == "k1"
        assert [f.groq_api_key.get_secret_value() for f in m.fallbacks] == ["k2", "k3"]  # duplicates dropped, order kept
    finally:
        get_settings.cache_clear()


def test_friendly_error_for_quota():
    from app.errors import QUOTA_MESSAGE, friendly_error

    class RateLimitError(Exception):
        pass

    assert friendly_error(RateLimitError("x")) == QUOTA_MESSAGE
    assert friendly_error(ValueError("Error code: 429 - tokens per day")) == QUOTA_MESSAGE
    assert friendly_error(ValueError("boom")) == "boom"


def test_daily_ai_run_budget_returns_429(client, monkeypatch):
    import asyncio

    from fastapi import HTTPException

    from app.config import get_settings
    from app.db.repos import claim_ai_run
    from tests.conftest import make_user

    user = make_user(client)
    monkeypatch.setenv("USER_DAILY_RUNS", "2")
    get_settings.cache_clear()

    async def run():
        from app.db.client import get_db
        await claim_ai_run(user["id"])
        await claim_ai_run(user["id"])
        try:
            await claim_ai_run(user["id"])
        except HTTPException as e:
            return e.status_code
        finally:
            await get_db().ai_usage.delete_many({"_id": {"$regex": f"^{user['id']}:"}})

    try:
        # the app's own loop owns the Mongo client, so run the coroutine on it
        assert client.portal.call(run) == 429
    finally:
        get_settings.cache_clear()


def test_ready_reports_each_check(client):
    r = client.get("/ready")
    assert r.status_code in (200, 503)
    assert set(r.json()["checks"]) >= {"mongodb", "s3"}
