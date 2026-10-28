"""Explicit cascade deletes (MongoDB has no foreign keys): S3 files, Mongo documents, vectors."""
from app.db.client import get_db
from app.storage import keys, s3

# Collections that carry track_id + user_id.
TRACK_COLLECTIONS = ("resumes", "documents", "applications", "job_matches", "sessions", "reports")


async def purge_track_data(user_id: str, track_id: str) -> None:
    """Delete everything a track owns except the track document itself."""
    db = get_db()
    await s3.delete_prefix_async(keys.track_prefix(user_id, track_id))
    for name in TRACK_COLLECTIONS:
        await db[name].delete_many({"user_id": user_id, "track_id": track_id})
    prefix = {"$regex": f"^{user_id}:{track_id}:"}  # LangGraph checkpoints are keyed by thread_id
    for name in ("checkpoints", "checkpoint_writes"):
        await db[name].delete_many({"thread_id": prefix})
    # Phase 2 hooks in the personal Chroma collection and BM25 index here.
    from app.rag import stores  # noqa: PLC0415 - imported lazily, module arrives in Phase 2

    await stores.delete_personal_collection(user_id, track_id)


async def purge_track(user_id: str, track_id: str) -> None:
    await purge_track_data(user_id, track_id)
    await get_db().tracks.delete_one({"_id": track_id, "user_id": user_id})
