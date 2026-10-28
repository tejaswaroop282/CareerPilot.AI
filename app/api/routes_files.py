"""Resume and document upload/list. Files go to S3; MongoDB keeps metadata and the s3_key."""
import os

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.auth.deps import OwnedTrack
from app.db.client import get_db
from app.db.models import DocumentOut, ResumeOut, utcnow
from app.db.repos import get_owned, list_owned, new_id
from app.rag import stores
from app.storage import keys, s3

router = APIRouter(prefix="/tracks/{track_id}", tags=["files"])

MAX_BYTES = 10 * 1024 * 1024
DOC_TYPES = {"pdf", "md", "txt"}


async def _read_upload(file: UploadFile) -> bytes:
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="File too large (10 MB max)")
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    return data


def _resume_out(r: dict) -> ResumeOut:
    return ResumeOut(
        id=r["_id"],
        track_id=r["track_id"],
        filename=r["filename"],
        version=r["version"],
        is_primary=r["is_primary"],
        created_at=r["created_at"],
    )


def _doc_out(d: dict) -> DocumentOut:
    return DocumentOut(
        id=d["_id"],
        track_id=d["track_id"],
        filename=d["filename"],
        type=d["type"],
        size=d["size"],
        chunk_count=d["chunk_count"],
        chunker_version=d.get("chunker_version"),
        indexed=d["indexed"],
        created_at=d["created_at"],
    )


# ---- resumes --------------------------------------------------------------

@router.get("/resumes", response_model=list[ResumeOut])
async def list_resumes(track: OwnedTrack) -> list[ResumeOut]:
    return [_resume_out(r) for r in await list_owned("resumes", track["user_id"], track["_id"])]


@router.post("/resumes", response_model=ResumeOut, status_code=201)
async def upload_resume(track: OwnedTrack, file: UploadFile = File(...)) -> ResumeOut:
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=415, detail="Resumes must be PDF files")
    data = await _read_upload(file)
    if not data.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="File is not a valid PDF")
    uid, tid, rid = track["user_id"], track["_id"], new_id()
    existing = await list_owned("resumes", uid, tid)
    key = keys.resume_key(uid, tid, rid)
    await s3.put_object_async(key, data, "application/pdf")
    resume = {
        "_id": rid,
        "user_id": uid,
        "track_id": tid,
        "s3_key": key,
        "filename": os.path.basename(file.filename or "resume.pdf"),
        "version": len(existing) + 1,
        "is_primary": not existing,  # the first resume is primary
        "created_at": utcnow(),
    }
    await get_db().resumes.insert_one(resume)
    return _resume_out(resume)


@router.patch("/resumes/{resume_id}", response_model=ResumeOut)
async def make_primary(resume_id: str, track: OwnedTrack) -> ResumeOut:
    uid, tid = track["user_id"], track["_id"]
    resume = await get_owned("resumes", resume_id, uid, track_id=tid)
    coll = get_db().resumes
    await coll.update_many({"user_id": uid, "track_id": tid}, {"$set": {"is_primary": False}})
    await coll.update_one({"_id": resume_id, "user_id": uid}, {"$set": {"is_primary": True, "primary_since": utcnow()}})
    resume["is_primary"] = True
    return _resume_out(resume)


# ---- documents ------------------------------------------------------------

@router.get("/documents", response_model=list[DocumentOut])
async def list_documents(track: OwnedTrack) -> list[DocumentOut]:
    return [_doc_out(d) for d in await list_owned("documents", track["user_id"], track["_id"])]


@router.post("/documents", response_model=DocumentOut, status_code=201)
async def upload_document(track: OwnedTrack, file: UploadFile = File(...)) -> DocumentOut:
    name = os.path.basename(file.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in DOC_TYPES:
        raise HTTPException(status_code=415, detail="Supported types: pdf, md, txt")
    data = await _read_upload(file)
    uid, tid, did = track["user_id"], track["_id"], new_id()
    key = keys.doc_key(uid, tid, did, ext)
    await s3.put_object_async(key, data)
    doc = {
        "_id": did,
        "user_id": uid,
        "track_id": tid,
        "s3_key": key,
        "filename": name,
        "type": ext,
        "size": len(data),
        "chunk_count": 0,
        "chunker_version": None,
        "indexed": False,
        "created_at": utcnow(),
    }
    await get_db().documents.insert_one(doc)
    return _doc_out(doc)


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(doc_id: str, track: OwnedTrack) -> None:
    doc = await get_owned("documents", doc_id, track["user_id"], track_id=track["_id"])
    await s3.delete_object_async(doc["s3_key"])
    await stores.delete_where_async(
        stores.personal_collection_name(track["_id"]), {"doc_id": doc_id}
    )
    await get_db().documents.delete_one({"_id": doc_id, "user_id": track["user_id"]})
