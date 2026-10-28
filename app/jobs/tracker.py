"""Application tracker storage (sync PyMongo, used by the MCP server). Every call filters by user_id and track_id."""
import uuid
from datetime import datetime, timezone
from functools import lru_cache

from pymongo import MongoClient

from app.config import get_settings

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


STATUSES = ("saved", "applied", "interview", "offer", "rejected")


@lru_cache
def _col():
    s = get_settings()
    return MongoClient(s.mongodb_uri)[s.mongodb_db].applications


def save_application(user_id: str, track_id: str, job_id: str, status: str, resume_id: str | None, job: dict | None = None) -> str:
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    now = utcnow()
    existing = _col().find_one({"user_id": user_id, "track_id": track_id, "job_id": job_id})
    if existing:
        _col().update_one({"_id": existing["_id"]}, {"$set": {"status": status, "resume_id": resume_id, "updated_at": now}})
        return existing["_id"]
    doc = {"_id": new_id(), "user_id": user_id, "track_id": track_id, "job_id": job_id, "status": status,
           "resume_id": resume_id, "job": job or {}, "created_at": now, "updated_at": now}
    _col().insert_one(doc)
    return doc["_id"]


def list_applications(user_id: str, track_id: str) -> list[dict]:
    rows = _col().find({"user_id": user_id, "track_id": track_id}).sort("updated_at", -1)
    return [{"id": r["_id"], "job_id": r["job_id"], "status": r["status"], "resume_id": r.get("resume_id"),
             "title": (r.get("job") or {}).get("title"), "company": (r.get("job") or {}).get("company"),
             "url": (r.get("job") or {}).get("url"), "updated_at": r["updated_at"].isoformat()} for r in rows]
