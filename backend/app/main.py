import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal, init_db
from app.limiter import limiter
from app.models import Unlock
from app.routers import admin, auth, me, missions, social, stories, world
from app.services import progression
from app.services.jobs import scheduler_loop

logging.basicConfig(level=logging.INFO)


async def seed_unlocks() -> None:
    """Keep the `unlocks` config table in sync with the level table."""
    async with SessionLocal() as db:
        if await db.scalar(select(func.count()).select_from(Unlock)):
            return
        for level, key, value, label in progression.DEFAULT_UNLOCKS:
            db.add(Unlock(level=level, feature_key=key, value=value, label=label))
        await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await seed_unlocks()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()


app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

API = "/api/v1"
for r in (auth.router, me.router, missions.router, stories.router, world.router, social.router, admin.router, admin.uploads):
    app.include_router(r, prefix=API)


@app.get("/health")
async def health():
    return {"ok": True}


@app.get(f"{API}/unlocks")
async def unlock_table():
    return [
        {"level": lvl, "feature_key": key, "value": value, "label": label}
        for lvl, key, value, label in progression.DEFAULT_UNLOCKS
    ]
