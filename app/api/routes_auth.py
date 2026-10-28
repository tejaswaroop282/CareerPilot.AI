from fastapi import APIRouter, HTTPException
from pymongo.errors import DuplicateKeyError

from app.auth.deps import CurrentUser
from app.auth.security import create_access_token, hash_password, verify_password
from app.db.client import get_db
from app.db.models import Credentials, TokenOut, UserOut, utcnow
from app.db.repos import new_id

router = APIRouter(prefix="/auth", tags=["auth"])


def _token(user: dict) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user["_id"]),
        user=UserOut(id=user["_id"], email=user["email"]),
    )


@router.post("/register", response_model=TokenOut, status_code=201)
async def register(body: Credentials) -> TokenOut:
    user = {
        "_id": new_id(),
        "email": body.email.lower(),
        "password_hash": hash_password(body.password),
        "created_at": utcnow(),
    }
    try:
        await get_db().users.insert_one(user)
    except DuplicateKeyError:
        raise HTTPException(status_code=409, detail="Email already registered") from None
    return _token(user)


@router.post("/login", response_model=TokenOut)
async def login(body: Credentials) -> TokenOut:
    user = await get_db().users.find_one({"email": body.email.lower()})
    if user is None or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return _token(user)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(id=user["_id"], email=user["email"])
