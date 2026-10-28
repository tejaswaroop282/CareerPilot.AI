"""Node 1: parse, chunk, embed, and store every unindexed upload in the track's personal KB.

Idempotent: chunk ids are {doc_id}:{chunker_version}:{index}, and a document's old chunks are deleted before the
new ones are inserted, so re-running never duplicates. Parsing and fetching run concurrently with asyncio.
"""
import asyncio
import logging

from app.db.client import get_db
from app.db.models import utcnow
from app.db.repos import list_owned
from app.graph.helpers.github import fetch_github_docs
from app.graph.helpers.loaders import parse_file
from app.graph.state import State
from app.rag import stores
from app.rag.chunking.base import CHUNKER_VERSION, Chunk
from app.rag.chunking.router import chunk_document, pick_source_type
from app.storage import s3

log = logging.getLogger(__name__)


async def _load_and_chunk(item: dict, *, is_resume: bool, base_meta: dict) -> tuple[dict, list[Chunk]]:
    data = await s3.get_object_async(item["s3_key"])
    file_type = "pdf" if is_resume else item["type"]
    text = await asyncio.to_thread(parse_file, data, file_type)
    source_type = pick_source_type(file_type, text, is_resume)
    chunks = await asyncio.to_thread(
        lambda: chunk_document(
            doc_id=item["_id"],
            source_type=source_type,
            source_name=item["filename"],
            text=text,
            base_meta=base_meta,
        )
    )
    return item, chunks


async def _store(collection: str, collection_name: str, item: dict, chunks: list[Chunk]) -> int:
    pcol = collection_name
    await stores.delete_where_async(pcol, {"doc_id": item["_id"]})  # replace, never duplicate
    n = await stores.upsert_chunks_async(pcol, chunks)
    await get_db()[collection].update_one(
        {"_id": item["_id"], "user_id": item["user_id"]},
        {"$set": {"chunk_count": n, "chunker_version": CHUNKER_VERSION, "indexed": True, "indexed_at": utcnow()}},
    )
    return n


async def ingest(state: State) -> dict:
    uid, tid = state["user_id"], state["track_id"]
    force = state.get("force_reindex", False)
    base_meta = {"user_id": uid, "track_id": tid, "role": state.get("target_role")}
    base_meta = {k: v for k, v in base_meta.items() if v is not None}
    collection_name = stores.personal_collection_name(tid)

    resumes = await list_owned("resumes", uid, tid)
    documents = await list_owned("documents", uid, tid)

    def stale(it: dict) -> bool:
        return force or not it.get("indexed") or it.get("chunker_version") != CHUNKER_VERSION

    jobs = [(r, "resumes", True) for r in resumes if stale(r)] + [(d, "documents", False) for d in documents if stale(d)]
    skipped = len(resumes) + len(documents) - len(jobs)

    gh_task = asyncio.create_task(fetch_github_docs(state.get("github_username")))
    parsed = await asyncio.gather(
        *[_load_and_chunk(it, is_resume=is_res, base_meta=base_meta) for it, _, is_res in jobs],
        return_exceptions=True,
    )

    report: dict = {"indexed": {}, "skipped": skipped, "errors": {}, "github_docs": 0}
    for (item, coll, _), result in zip(jobs, parsed, strict=True):
        if isinstance(result, Exception):
            log.exception("ingest failed for %s", item["filename"], exc_info=result)
            report["errors"][item["filename"]] = str(result)
            continue
        _, chunks = result
        report["indexed"][item["filename"]] = await _store(coll, collection_name, item, chunks)

    # GitHub repo content fetched through the MCP GitHub tools: indexed as project docs under a synthetic doc id.
    gh_docs = await gh_task
    for name, text, repo in gh_docs:
        doc_id = f"gh:{name}"
        chunks = chunk_document(
            doc_id=doc_id, source_type="project_doc", source_name=name, text=text,
            base_meta={**base_meta, "project": repo},  # lets "questions about my <repo> project" filter to it
        )
        await stores.delete_where_async(collection_name, {"doc_id": doc_id})
        await stores.upsert_chunks_async(collection_name, chunks)
    report["github_docs"] = len(gh_docs)

    return {"kb_ready": not report["errors"], "ingest_report": report}
