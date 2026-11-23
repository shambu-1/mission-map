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
    """Create all tables (MVP: no migrations yet). Plain PostgreSQL — no extensions needed."""
    from app import models  # noqa: F401  (register models)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            # Tables from an older version aren't altered by create_all — detect and explain.
            stale = await conn.scalar(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'missions' AND column_name = 'location'"
                )
            )
        if stale:
            raise RuntimeError(
                "The database was created by an older version of the app (PostGIS columns). "
                "Recreate it with `python scripts/setup_db.py --reset` (deletes its data)."
            )
    except OSError as exc:
        raise RuntimeError(
            f"Can't reach PostgreSQL ({exc}). Is it running? Check DATABASE_URL in backend/.env."
        ) from exc
    except RuntimeError:
        raise
    except Exception as exc:
        msg = str(exc)
        if "password authentication failed" in msg or "does not exist" in msg:
            raise RuntimeError(
                f"Database login failed ({msg.splitlines()[0]}). Run `python scripts/setup_db.py` "
                "or fix DATABASE_URL in backend/.env."
            ) from exc
        raise
