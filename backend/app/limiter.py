from slowapi import Limiter
from slowapi.util import get_remote_address

# Rate limits on auth, check-in and link-import endpoints (in-memory; use Redis storage in production)
limiter = Limiter(key_func=get_remote_address)
