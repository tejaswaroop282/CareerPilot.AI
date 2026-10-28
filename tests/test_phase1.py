import pytest

from app.storage import s3
from tests.conftest import MINI_PDF, make_user


def mk_track(client, user, name="SDE", role="sde"):
    r = client.post("/tracks", json={"name": name, "target_role": role}, headers=user["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def test_auth_flow(client):
    u = make_user(client)
    assert client.get("/auth/me", headers=u["headers"]).json()["email"] == u["email"]
    dup = client.post("/auth/register", json={"email": u["email"], "password": "another-pass-1"})
    assert dup.status_code == 409
    assert client.post("/auth/login", json={"email": u["email"], "password": "wrong-password"}).status_code == 401
    assert client.post("/auth/login", json={"email": u["email"], "password": "correct-horse-1"}).status_code == 200


def test_routes_require_jwt(client):
    assert client.get("/tracks").status_code == 401
    assert client.get("/tracks", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/health").status_code == 200


def test_two_users_two_tracks_each(client):
    a, b = make_user(client), make_user(client)
    for u in (a, b):
        mk_track(client, u, "SDE", "sde")
        mk_track(client, u, "GenAI", "genai")
        assert len(client.get("/tracks", headers=u["headers"]).json()) == 2


def test_cross_user_access_is_404(client):
    a, b = make_user(client), make_user(client)
    t = mk_track(client, a)
    tid = t["id"]
    files = {"file": ("r.pdf", MINI_PDF, "application/pdf")}
    rid = client.post(f"/tracks/{tid}/resumes", files=files, headers=a["headers"]).json()["id"]
    did = client.post(
        f"/tracks/{tid}/documents", files={"file": ("n.md", b"# notes", "text/markdown")}, headers=a["headers"]
    ).json()["id"]

    h = b["headers"]
    checks = [
        client.patch(f"/tracks/{tid}", json={"name": "x"}, headers=h),
        client.delete(f"/tracks/{tid}", headers=h),
        client.delete(f"/tracks/{tid}/data", headers=h),
        client.post(f"/tracks/{tid}/duplicate", headers=h),
        client.get(f"/tracks/{tid}/resumes", headers=h),
        client.post(f"/tracks/{tid}/resumes", files=files, headers=h),
        client.patch(f"/tracks/{tid}/resumes/{rid}", headers=h),
        client.get(f"/tracks/{tid}/documents", headers=h),
        client.delete(f"/tracks/{tid}/documents/{did}", headers=h),
    ]
    assert [c.status_code for c in checks] == [404] * len(checks)
    # Owner still has everything.
    assert client.get(f"/tracks/{tid}/resumes", headers=a["headers"]).status_code == 200
    assert len(client.get("/tracks", headers=h).json()) == 0


def test_uploads_land_in_s3_and_primary_switch(client):
    u = make_user(client)
    t = mk_track(client, u)
    tid = t["id"]
    pdf = {"file": ("one.pdf", MINI_PDF, "application/pdf")}
    r1 = client.post(f"/tracks/{tid}/resumes", files=pdf, headers=u["headers"]).json()
    r2 = client.post(f"/tracks/{tid}/resumes", files=pdf, headers=u["headers"]).json()
    assert r1["is_primary"] and not r2["is_primary"]
    client.patch(f"/tracks/{tid}/resumes/{r2['id']}", headers=u["headers"])
    primaries = [r["id"] for r in client.get(f"/tracks/{tid}/resumes", headers=u["headers"]).json() if r["is_primary"]]
    assert primaries == [r2["id"]]

    doc = client.post(
        f"/tracks/{tid}/documents", files={"file": ("notes.txt", b"hello", "text/plain")}, headers=u["headers"]
    ).json()
    assert doc["chunk_count"] == 0 and doc["indexed"] is False
    keys = s3.list_keys(f"users/{u['id']}/tracks/{tid}/")
    assert len(keys) == 3

    bad = client.post(f"/tracks/{tid}/resumes", files={"file": ("x.pdf", b"not a pdf", "application/pdf")}, headers=u["headers"])
    assert bad.status_code == 415
    bad = client.post(f"/tracks/{tid}/documents", files={"file": ("x.exe", b"MZ", "application/octet-stream")}, headers=u["headers"])
    assert bad.status_code == 415


def test_duplicate_and_delete_cascade(client):
    u = make_user(client)
    t = mk_track(client, u)
    tid = t["id"]
    client.post(f"/tracks/{tid}/resumes", files={"file": ("r.pdf", MINI_PDF, "application/pdf")}, headers=u["headers"])
    client.post(f"/tracks/{tid}/documents", files={"file": ("n.md", b"# n", "text/markdown")}, headers=u["headers"])
    dup = client.post(f"/tracks/{tid}/duplicate", headers=u["headers"]).json()
    assert len(client.get(f"/tracks/{dup['id']}/resumes", headers=u["headers"]).json()) == 1
    assert len(client.get(f"/tracks/{dup['id']}/documents", headers=u["headers"]).json()) == 1
    assert len(s3.list_keys(f"users/{u['id']}/tracks/{dup['id']}/")) == 2

    assert client.delete(f"/tracks/{tid}", headers=u["headers"]).status_code == 204
    assert s3.list_keys(f"users/{u['id']}/tracks/{tid}/") == []
    assert client.get(f"/tracks/{tid}/resumes", headers=u["headers"]).status_code == 404
    # The copy is independent and survives.
    assert len(s3.list_keys(f"users/{u['id']}/tracks/{dup['id']}/")) == 2


@pytest.mark.asyncio
async def test_sessions_expire_and_are_isolated(monkeypatch):
    from datetime import timedelta

    from pymongo import AsyncMongoClient

    from app.config import get_settings
    import app.db.client as dbc

    # The API client lives on the TestClient's loop; use a fresh client on this test's loop.
    fresh = AsyncMongoClient(get_settings().mongodb_uri)
    monkeypatch.setattr(dbc, "_client", fresh)

    from app.db.client import get_db
    from app.db.models import utcnow
    from app.db.repos import create_session, get_session, update_session
    from fastapi import HTTPException

    s = await create_session("u1", "t1", "prep")
    assert s["thread_id"].startswith("u1:t1:prep:")
    await update_session(s["thread_id"], "u1", "awaiting_input", "confirm_plan")
    assert (await get_session(s["thread_id"], "u1"))["status"] == "awaiting_input"
    with pytest.raises(HTTPException) as e:
        await get_session(s["thread_id"], "someone-else")
    assert e.value.status_code == 404
    await get_db().sessions.update_one({"thread_id": s["thread_id"]}, {"$set": {"updated_at": utcnow() - timedelta(hours=25)}})
    assert (await get_session(s["thread_id"], "u1"))["status"] == "expired"
    await fresh.close()
