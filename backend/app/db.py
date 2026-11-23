from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Create the PostGIS extension and all tables (MVP: no migrations yet)."""
    from app import models  # noqa: F401  (register models)

    try:
        async with engine.begin() as conn:
            has_postgis = await conn.scalar(text("SELECT 1 FROM pg_extension WHERE extname = 'postgis'"))
            if not has_postgis:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await conn.run_sync(Base.metadata.create_all)
    except OSError as exc:
        raise RuntimeError(
            f"Can't reach PostgreSQL ({exc}). Is it running? Check DATABASE_URL in backend/.env."
        ) from exc
    except Exception as exc:
        msg = str(exc)
        if "postgis" in msg.lower() or "geography" in msg.lower():
            raise RuntimeError(
                "PostGIS isn't enabled in the database. Run `python scripts/setup_db.py` from the "
                "backend folder (see README → Local setup)."
            ) from exc
        if "password authentication failed" in msg or "does not exist" in msg:
            raise RuntimeError(
                f"Database login failed ({msg.splitlines()[0]}). Run `python scripts/setup_db.py` "
                "or fix DATABASE_URL in backend/.env."
            ) from exc
        raise
