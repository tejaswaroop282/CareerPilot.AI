from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.db.models import SessionOut
from app.db.repos import get_session, list_sessions

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _out(s: dict) -> SessionOut:
    return SessionOut(**{k: s.get(k) for k in SessionOut.model_fields})


@router.get("", response_model=list[SessionOut])
async def sessions(user: CurrentUser, track_id: str | None = None) -> list[SessionOut]:
    return [_out(s) for s in await list_sessions(user["_id"], track_id)]


@router.get("/{thread_id}", response_model=SessionOut)
async def session(thread_id: str, user: CurrentUser) -> SessionOut:
    return _out(await get_session(thread_id, user["_id"]))
