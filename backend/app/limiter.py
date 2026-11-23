"""Rate limiting (auth, check-in, link import, explore).

Signed-in requests are limited per user, so players behind the same NAT or the hosting
provider's proxy don't share one bucket. Anonymous requests (login/register) are limited
per client IP — run uvicorn with --proxy-headers and FORWARDED_ALLOW_IPS set to your
proxy so the real client IP is used.
"""

import jwt
from slowapi import Limiter
from slowapi.util import get_remote_address
from starlette.requests import Request

from app.config import settings


def user_or_ip(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], settings.jwt_secret, algorithms=[settings.jwt_algorithm])
            if payload.get("type") == "access":
                return f"user:{payload['sub']}"
        except jwt.PyJWTError:
            pass
    return f"ip:{get_remote_address(request)}"


limiter = Limiter(key_func=user_or_ip, storage_uri=settings.rate_limit_storage_uri)
