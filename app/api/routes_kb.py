from fastapi import APIRouter

from app.auth.deps import CurrentUser
from app.rag import manifest as mf

router = APIRouter(prefix="/kb", tags=["kb"])


@router.get("/roles")
async def roles(_: CurrentUser) -> dict:
    m = mf.load_manifest()["roles"]
    return {"roles": [{"role": r, **info} for r, info in sorted(m.items())]}


@router.get("/companies")
async def companies(_: CurrentUser, role: str | None = None) -> dict:
    m = mf.load_manifest()["companies"]
    wanted = set(mf.supported_companies(role)) if role else set(m)
    return {"companies": [{"company": c, **info} for c, info in sorted(m.items()) if c in wanted]}
