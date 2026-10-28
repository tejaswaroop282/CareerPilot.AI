from fastapi import APIRouter

from app.auth.deps import CurrentUser, OwnedTrack
from app.db.client import get_db
from app.db.models import TrackIn, TrackOut, TrackPatch, utcnow
from app.db.purge import purge_track, purge_track_data
from app.db.repos import list_owned, new_id
from app.rag import manifest
from app.storage import keys, s3

router = APIRouter(prefix="/tracks", tags=["tracks"])


def _role_key(raw: str) -> str:
    """A role the knowledge base supports is stored under its key ('ai intern' -> 'aiml'); anything else is kept as typed
    and the prep flow tells the user which roles are supported."""
    text = raw.strip().lower()
    return manifest.resolve_role(text) or text


def _out(t: dict) -> TrackOut:
    return TrackOut(id=t["_id"], name=t["name"], target_role=t["target_role"], github_username=t.get("github_username"), created_at=t["created_at"])


@router.get("", response_model=list[TrackOut])
async def list_tracks(user: CurrentUser) -> list[TrackOut]:
    return [_out(t) for t in await list_owned("tracks", user["_id"])]


@router.post("", response_model=TrackOut, status_code=201)
async def create_track(body: TrackIn, user: CurrentUser) -> TrackOut:
    track = {
        "_id": new_id(),
        "user_id": user["_id"],
        "name": body.name.strip(),
        "target_role": _role_key(body.target_role),
        "github_username": body.github_username or None,
        "created_at": utcnow(),
    }
    await get_db().tracks.insert_one(track)
    return _out(track)


@router.patch("/{track_id}", response_model=TrackOut)
async def update_track(body: TrackPatch, track: OwnedTrack) -> TrackOut:
    changes = {}
    if body.name is not None:
        changes["name"] = body.name.strip()
    if body.target_role is not None:
        changes["target_role"] = _role_key(body.target_role)
    if body.github_username is not None:
        changes["github_username"] = body.github_username or None
    if changes:
        await get_db().tracks.update_one({"_id": track["_id"], "user_id": track["user_id"]}, {"$set": changes})
        track.update(changes)
    return _out(track)


@router.delete("/{track_id}", status_code=204)
async def delete_track(track: OwnedTrack) -> None:
    await purge_track(track["user_id"], track["_id"])


@router.delete("/{track_id}/data", status_code=204)
async def delete_track_data(track: OwnedTrack) -> None:
    """Delete my data: S3 files, Mongo documents, and the personal vector collection. The track stays."""
    await purge_track_data(track["user_id"], track["_id"])


@router.post("/{track_id}/duplicate", response_model=TrackOut, status_code=201)
async def duplicate_track(track: OwnedTrack) -> TrackOut:
    """Copy a track's resumes and documents as a starting point (files copied in S3; not yet indexed)."""
    db, uid = get_db(), track["user_id"]
    new_track = {
        "_id": new_id(),
        "user_id": uid,
        "name": f"{track['name']} (copy)",
        "target_role": track["target_role"],
        "github_username": track.get("github_username"),
        "created_at": utcnow(),
    }
    await db.tracks.insert_one(new_track)
    for r in await list_owned("resumes", uid, track["_id"]):
        rid = new_id()
        dst = keys.resume_key(uid, new_track["_id"], rid)
        await s3.copy_object_async(r["s3_key"], dst)
        await db.resumes.insert_one(
            {**r, "_id": rid, "track_id": new_track["_id"], "s3_key": dst, "created_at": utcnow()}
        )
    for d in await list_owned("documents", uid, track["_id"]):
        did = new_id()
        dst = keys.doc_key(uid, new_track["_id"], did, d["type"])
        await s3.copy_object_async(d["s3_key"], dst)
        await db.documents.insert_one(
            {
                **d,
                "_id": did,
                "track_id": new_track["_id"],
                "s3_key": dst,
                "chunk_count": 0,
                "chunker_version": None,
                "indexed": False,
                "created_at": utcnow(),
            }
        )
    return _out(new_track)
