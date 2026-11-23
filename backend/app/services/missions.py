"""Mission helpers shared by routers: serialisation, slots, safety checks."""

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Mission, RestrictedZone, User
from app.schemas import MissionOut
from app.services import progression
from app.services.geo import latlng_of, point_wkt, view_url, waypoint_url

ACTIVE_STATUSES = ("active", "in_progress")


def to_out(m: Mission, user: User | None = None, distance_m: float | None = None) -> MissionOut:
    ll = latlng_of(m.location)
    lat, lng = (ll if ll else (None, None))
    mode = user.travel_mode if user else "driving"
    return MissionOut(
        id=m.id,
        owner_id=m.owner_id,
        title=m.title,
        description=m.description,
        type=m.type,
        category=m.category,
        lat=lat,
        lng=lng,
        place_name=m.place_name,
        radius_m=m.radius_m,
        dwell_min=m.dwell_min,
        proof_required=m.proof_required,
        deadline=m.deadline,
        status=m.status,
        story_id=m.story_id,
        chapter_no=m.chapter_no,
        visibility=m.visibility,
        source=m.source,
        sponsored=m.sponsor_id is not None,
        squad_id=m.squad_id,
        has_proof=m.proof_key is not None,
        dwell_started_at=m.dwell_started_at,
        distance_m=round(distance_m, 1) if distance_m is not None else None,
        waypoint_url=waypoint_url(lat, lng, mode) if lat is not None else None,
        view_url=view_url(lat, lng) if lat is not None else None,
        created_at=m.created_at,
    )


async def active_slots_used(db: AsyncSession, user_id: int) -> int:
    """Daily (system) missions and squad missions don't use a player's slots."""
    return await db.scalar(
        select(func.count()).select_from(Mission).where(
            Mission.owner_id == user_id,
            Mission.status.in_(ACTIVE_STATUSES),
            Mission.type != "daily",
            Mission.squad_id.is_(None),
        )
    ) or 0


async def ensure_free_slot(db: AsyncSession, user: User) -> None:
    used = await active_slots_used(db, user.id)
    limit = progression.slot_limit(user.level, user.is_premium) + user.extra_slots
    if used >= limit:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"All {limit} mission slots are full — finish or abandon a mission first",
        )


async def ensure_safe_location(db: AsyncSession, lat: float, lng: float) -> None:
    """Block missions inside restricted zones (railway tracks, military areas, water bodies)."""
    zone = await db.scalar(
        select(RestrictedZone.kind).where(
            func.ST_Intersects(RestrictedZone.area, func.ST_GeogFromText(point_wkt(lat, lng)))
        ).limit(1)
    )
    if zone:
        raise HTTPException(422, f"Missions can't be placed in a restricted area ({zone})")


def distance_expr(lat: float, lng: float):
    return func.ST_Distance(Mission.location, func.ST_GeogFromText(point_wkt(lat, lng)))
