"""Node 2: extract skills, projects, and level from the primary resume's chunks (structured output).

Chunks are read by metadata filter (doc_id of the primary resume), not by similarity search.
"""
from app.db.client import get_db
from app.graph.prompts import BUILD_PROFILE_SYSTEM, BUILD_PROFILE_USER
from app.graph.state import Profile, State
from app.rag import stores
from app.rag.llm import get_chat_model


def _order(chunk: dict) -> int:
    return int(chunk["id"].rsplit(":", 1)[-1])


async def build_profile(state: State) -> dict:
    uid, tid = state["user_id"], state["track_id"]
    resume_id = state.get("resume_id")
    if not resume_id:
        return {}  # no primary resume yet; nothing to profile
    where = stores.where_all(doc_id=resume_id, user_id=uid, track_id=tid)
    chunks = await stores.get_chunks_async(stores.personal_collection_name(tid), where)
    if not chunks:
        return {}
    chunks.sort(key=_order)
    body = "\n\n".join(f"[{c['metadata'].get('header', '')}]\n{c['text']}" for c in chunks)

    llm = get_chat_model().with_structured_output(Profile)
    profile: Profile = await llm.ainvoke(
        [("system", BUILD_PROFILE_SYSTEM), ("human", BUILD_PROFILE_USER.format(chunks=body))]
    )
    await get_db().tracks.update_one(
        {"_id": tid, "user_id": uid}, {"$set": {"profile": profile.model_dump()}}
    )
    return {"profile": profile}
