from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.models import User
from app.security import decode_token
from app.services import progression

bearer = HTTPBearer(auto_error=False)

Db = Annotated[AsyncSession, Depends(get_db)]


async def get_current_user(
    db: Db, creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    try:
        payload = decode_token(creds.credentials, "access")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    user = await db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_unlock(feature_key: str):
    """Dependency: 403 unless the user's level has unlocked `feature_key`."""

    async def checker(user: CurrentUser) -> User:
        if progression.unlocks_for_level(user.level).get(feature_key) != "true":
            needed = next(
                (lvl for lvl, key, _v, _l in progression.DEFAULT_UNLOCKS if key == feature_key), None
            )
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Locked — reach level {needed} to unlock")
        return user

    return checker


async def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return user
