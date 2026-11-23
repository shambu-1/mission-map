import jwt
from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import or_, select

from app.deps import Db
from app.limiter import limiter
from app.models import User
from app.schemas import LoginIn, RefreshIn, RegisterIn, TokenOut
from app.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _tokens(user: User) -> TokenOut:
    return TokenOut(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


@router.post("/register", response_model=TokenOut, status_code=201)
@limiter.limit("10/hour")
async def register(request: Request, body: RegisterIn, db: Db):
    taken = await db.scalar(
        select(User.id).where(or_(User.username == body.username.lower(), User.email == body.email.lower()))
    )
    if taken:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username or email already in use")
    user = User(
        username=body.username.lower(),
        email=body.email.lower(),
        password_hash=hash_password(body.password),
    )
    db.add(user)
    await db.commit()
    return _tokens(user)


@router.post("/login", response_model=TokenOut)
@limiter.limit("20/hour")
async def login(request: Request, body: LoginIn, db: Db):
    ident = body.username_or_email.lower().strip()
    user = await db.scalar(select(User).where(or_(User.username == ident, User.email == ident)))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong username or password")
    return _tokens(user)


@router.post("/refresh", response_model=TokenOut)
@limiter.limit("60/hour")
async def refresh(request: Request, body: RefreshIn, db: Db):
    """Refresh tokens are rotated: each use invalidates the previous one."""
    try:
        payload = decode_token(body.refresh_token, "refresh")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    user = await db.get(User, int(payload["sub"]))
    if user is None or payload.get("ver") != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token already used or revoked")
    user.token_version += 1
    await db.commit()
    return _tokens(user)
