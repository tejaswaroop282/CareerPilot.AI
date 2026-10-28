"""The single get_current_user dependency (Cognito can replace it later) and ownership helpers."""
from typing import Annotated

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.auth.security import decode_access_token
from app.db.client import get_db
from app.db.repos import get_owned

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    unauthorized = HTTPException(status_code=401, detail="Invalid or missing token")
    if creds is None:
        raise unauthorized
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise unauthorized
    user = await get_db().users.find_one({"_id": user_id})
    if user is None:
        raise unauthorized
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


async def get_owned_track(track_id: str, user: CurrentUser) -> dict:
    return await get_owned("tracks", track_id, user["_id"])


OwnedTrack = Annotated[dict, Depends(get_owned_track)]
