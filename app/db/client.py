"""MongoDB (PyMongo async) client and index creation."""
from pymongo import ASCENDING, DESCENDING, AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from app.config import get_settings

_client: AsyncMongoClient | None = None


def get_client() -> AsyncMongoClient:
    global _client
    if _client is None:
        _client = AsyncMongoClient(get_settings().mongodb_uri)
    return _client


def get_db() -> AsyncDatabase:
    return get_client()[get_settings().mongodb_db]


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.close()
        _client = None


async def ensure_indexes() -> None:
    db = get_db()
    await db.users.create_index("email", unique=True)
    await db.tracks.create_index("user_id")
    await db.resumes.create_index([("track_id", ASCENDING), ("user_id", ASCENDING)])
    await db.documents.create_index([("track_id", ASCENDING), ("user_id", ASCENDING)])
    await db.applications.create_index(
        [("track_id", ASCENDING), ("user_id", ASCENDING), ("status", ASCENDING)]
    )
    await db.job_matches.create_index(
        [("track_id", ASCENDING), ("user_id", ASCENDING), ("score", DESCENDING)]
    )
    await db.sessions.create_index("thread_id", unique=True)
    await db.sessions.create_index(
        [("user_id", ASCENDING), ("track_id", ASCENDING), ("status", ASCENDING)]
    )
    await db.reports.create_index([("user_id", ASCENDING), ("track_id", ASCENDING)])
    await db.ai_usage.create_index("created_at", expireAfterSeconds=2 * 24 * 3600)
    await db.job_analyses.create_index("created_at", expireAfterSeconds=14 * 24 * 3600)  # parsed postings are cached for two weeks
