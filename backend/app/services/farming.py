"""Anti-farming rules: stop players earning XP from missions they make at their own feet.

- New missions must be some distance from where the player is right now.
- Check-in only opens a while after a mission was created or activated.
- Self-made missions share a daily XP cap; home missions only count a few times a day.
- No XP for another self-made mission in the same H3 cell within a cooldown window.

Missions the player didn't write (daily events, sponsored) aren't capped.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CheckinAttempt, Completion, Mission
from app.services.geo import haversine_m

IST = ZoneInfo("Asia/Kolkata")

# Missions the player chose and placed (or a friend placed for them) — farmable
PLAYER_MADE_SOURCES = ("self", "friend")


def is_player_made(mission: Mission) -> bool:
    return mission.source in PLAYER_MADE_SOURCES and mission.type != "daily"


def checkin_opens_at(mission: Mission) -> datetime | None:
    """When check-in becomes possible. None = right away."""
    if not is_player_made(mission) or settings.min_minutes_before_checkin <= 0:
        return None
    start = mission.activated_at or mission.created_at
    if start is None:
        return None
    return start + timedelta(minutes=settings.min_minutes_before_checkin)


def start_of_local_day(now: datetime) -> datetime:
    local = now.astimezone(IST)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


async def last_known_position(db: AsyncSession, user_id: int, within_min: int = 30) -> tuple[float, float] | None:
    """Most recent server-side position (accepted check-in) in the last `within_min` minutes."""
    since = datetime.now(timezone.utc) - timedelta(minutes=within_min)
    row = (
        await db.execute(
            select(CheckinAttempt.lat, CheckinAttempt.lng)
            .where(
                CheckinAttempt.user_id == user_id,
                CheckinAttempt.created_at >= since,
                CheckinAttempt.lat.is_not(None),
            )
            .order_by(CheckinAttempt.created_at.desc())
            .limit(1)
        )
    ).first()
    return (row[0], row[1]) if row else None


async def too_close_to_player(
    db: AsyncSession, user_id: int, lat: float, lng: float, origin: tuple[float, float] | None
) -> bool:
    """True if the new mission is within min_mission_distance_m of the player's current spot."""
    points = [p for p in (origin, await last_known_position(db, user_id)) if p is not None]
    return any(haversine_m(p[0], p[1], lat, lng) < settings.min_mission_distance_m for p in points)


@dataclass
class XpAllowance:
    allowed: int
    note: str | None = None


async def allowed_xp(
    db: AsyncSession, mission: Mission, user_id: int, xp: int, cell: str | None, now: datetime
) -> XpAllowance:
    """Apply the daily caps and same-cell cooldown to `xp` earned from `mission`."""
    if xp <= 0 or not is_player_made(mission):
        return XpAllowance(xp)

    day_start = start_of_local_day(now)

    if mission.type == "home":
        home_today = await db.scalar(
            select(func.count())
            .select_from(Completion)
            .join(Mission, Mission.id == Completion.mission_id)
            .where(Completion.user_id == user_id, Completion.completed_at >= day_start, Mission.type == "home")
        ) or 0
        if home_today >= settings.daily_home_missions:
            return XpAllowance(0, f"Only {settings.daily_home_missions} home missions earn XP per day")

    if cell is not None and settings.same_cell_cooldown_h > 0:
        since = now - timedelta(hours=settings.same_cell_cooldown_h)
        recent_here = await db.scalar(
            select(Completion.id)
            .join(Mission, Mission.id == Completion.mission_id)
            .where(
                Completion.user_id == user_id,
                Completion.h3_index == cell,
                Completion.completed_at >= since,
                Completion.xp_awarded > 0,
                Mission.source.in_(PLAYER_MADE_SOURCES),
                Mission.type != "daily",
            )
            .limit(1)
        )
        if recent_here is not None:
            return XpAllowance(0, f"You already earned XP here in the last {settings.same_cell_cooldown_h} h")

    earned_today = await db.scalar(
        select(func.coalesce(func.sum(Completion.xp_awarded), 0))
        .join(Mission, Mission.id == Completion.mission_id)
        .where(
            Completion.user_id == user_id,
            Completion.completed_at >= day_start,
            Mission.source.in_(PLAYER_MADE_SOURCES),
            Mission.type != "daily",
        )
    ) or 0
    left = max(0, settings.daily_self_xp_cap - earned_today)
    if left < xp:
        return XpAllowance(left, f"Daily XP cap for your own missions reached ({settings.daily_self_xp_cap} XP)")
    return XpAllowance(xp)
