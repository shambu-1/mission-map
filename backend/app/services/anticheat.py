"""Server-side location sanity checks. Pure functions + one DB helper."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CheckinAttempt
from app.services.geo import haversine_m


@dataclass
class PrevPoint:
    lat: float
    lng: float
    at: datetime
    accuracy_m: float | None = None


def speed_kmh(prev: PrevPoint, lat: float, lng: float, ts: datetime) -> float | None:
    seconds = (ts - prev.at).total_seconds()
    if seconds <= 0:
        return None
    return haversine_m(prev.lat, prev.lng, lat, lng) / seconds * 3.6


def impossible_travel(prev: PrevPoint | None, lat: float, lng: float, ts: datetime) -> bool:
    if prev is None:
        return False
    # Ignore tiny moves (GPS jitter) even if timestamps are close together
    if haversine_m(prev.lat, prev.lng, lat, lng) < 200:
        return False
    v = speed_kmh(prev, lat, lng, ts)
    return v is None or v > settings.max_travel_speed_kmh


def suspicious_accuracy(accuracy_m: float, recent: list[PrevPoint], lat: float, lng: float) -> str | None:
    if accuracy_m == 0:
        return "accuracy exactly 0 m"
    identical = sum(1 for p in recent if p.lat == lat and p.lng == lng)
    if identical >= 2:
        return "identical coordinates repeated"
    return None


async def recent_points(db: AsyncSession, user_id: int, limit: int = 5) -> list[PrevPoint]:
    """Last accepted points (speed is measured between accepted points only)."""
    rows = (
        await db.execute(
            select(CheckinAttempt)
            .where(CheckinAttempt.user_id == user_id, CheckinAttempt.result.in_(("accepted", "in_progress")))
            .order_by(CheckinAttempt.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    points = []
    for r in rows:
        if r.lat is not None and r.lng is not None:
            points.append(PrevPoint(r.lat, r.lng, r.created_at, r.accuracy_m))
    return points


async def attempts_last_hour(db: AsyncSession, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    return await db.scalar(
        select(func.count()).select_from(CheckinAttempt).where(
            CheckinAttempt.user_id == user_id, CheckinAttempt.created_at >= since
        )
    ) or 0


async def flags_last_week(db: AsyncSession, user_id: int) -> int:
    since = datetime.now(timezone.utc) - timedelta(days=7)
    return await db.scalar(
        select(func.count()).select_from(CheckinAttempt).where(
            CheckinAttempt.user_id == user_id,
            CheckinAttempt.result == "flagged",
            CheckinAttempt.created_at >= since,
        )
    ) or 0
