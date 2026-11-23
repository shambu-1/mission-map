"""System mission generator: daily random events near the player (Phase 2)."""

import logging
import random
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ExploredCell, Mission, Poi, User
from app.services.fog import cell_of
from app.services.geo import bbox_around, haversine_m

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# OSM tags → (kind, mission category, title template)
POI_KINDS = {
    ("leisure", "park"): ("park", "fitness", "Walk to {name}"),
    ("amenity", "library"): ("library", "explore", "Visit {name}"),
    ("natural", "water"): ("lake", "fitness", "Walk to the lake: {name}"),
    ("tourism", "viewpoint"): ("viewpoint", "explore", "Catch the view at {name}"),
    ("tourism", "museum"): ("museum", "explore", "Explore {name}"),
    ("leisure", "sports_centre"): ("sports", "fitness", "Work out at {name}"),
    ("amenity", "cafe"): ("cafe", "social", "Grab a coffee at {name}"),
}

STAT_TO_CATEGORY = {
    "stat_stamina": "fitness",
    "stat_explorer": "explore",
    "stat_social": "social",
}


def weakest_category(user: User) -> str:
    stat = min(STAT_TO_CATEGORY, key=lambda s: getattr(user, s))
    return STAT_TO_CATEGORY[stat]


def end_of_day(now: datetime) -> datetime:
    """Daily missions expire at local midnight (IST)."""
    local = now.astimezone(IST)
    midnight = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=IST)
    return midnight.astimezone(timezone.utc)


async def fetch_pois_from_osm(db: AsyncSession, lat: float, lng: float, radius_m: int) -> int:
    """Pull nearby POIs from the Overpass API into the `pois` cache. Fails soft."""
    filters = "".join(
        f'node["{k}"="{v}"]["name"](around:{radius_m},{lat},{lng});'
        f'way["{k}"="{v}"]["name"](around:{radius_m},{lat},{lng});'
        for (k, v) in POI_KINDS
    )
    query = f"[out:json][timeout:10];({filters});out center 200;"
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            resp = await client.post(settings.overpass_url, data={"data": query})
            resp.raise_for_status()
            elements = resp.json().get("elements", [])
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Overpass fetch failed: %s", exc)
        return 0
    rows = []
    for el in elements:
        tags = el.get("tags", {})
        kind = next((POI_KINDS[(k, v)][0] for (k, v) in POI_KINDS if tags.get(k) == v), None)
        p_lat = el.get("lat") or el.get("center", {}).get("lat")
        p_lng = el.get("lon") or el.get("center", {}).get("lon")
        if kind and p_lat and p_lng and tags.get("name"):
            rows.append({"osm_id": el["id"], "name": tags["name"][:255], "kind": kind, "lat": p_lat, "lng": p_lng})
    if rows:
        await db.execute(insert(Poi).values(rows).on_conflict_do_nothing(index_elements=["osm_id"]))
    return len(rows)


def _template_for(kind: str) -> tuple[str, str]:
    for (_k, _v), (kind_, category, template) in POI_KINDS.items():
        if kind_ == kind:
            return category, template
    return "explore", "Visit {name}"


async def generate_daily(db: AsyncSession, user: User, lat: float, lng: float, count: int = 3) -> list[Mission]:
    """Pick 1–3 unvisited places within 2 km, weakest stat first, expiring at midnight."""
    now = datetime.now(timezone.utc)
    deadline = end_of_day(now)
    existing = (
        await db.execute(
            select(Mission).where(
                Mission.owner_id == user.id, Mission.type == "daily", Mission.deadline == deadline
            )
        )
    ).scalars().all()
    if existing:
        return list(existing)

    min_lat, max_lat, min_lng, max_lng = bbox_around(lat, lng, settings.daily_radius_m)
    nearby_q = select(Poi).where(Poi.lat.between(min_lat, max_lat), Poi.lng.between(min_lng, max_lng)).limit(500)

    def within_radius(rows):
        return [p for p in rows if haversine_m(lat, lng, p.lat, p.lng) <= settings.daily_radius_m]

    pois = within_radius((await db.execute(nearby_q)).scalars().all())
    if len(pois) < count:
        await fetch_pois_from_osm(db, lat, lng, settings.daily_radius_m)
        pois = within_radius((await db.execute(nearby_q)).scalars().all())
    if not pois:
        return []

    explored = set(
        (await db.execute(select(ExploredCell.h3_index).where(ExploredCell.user_id == user.id))).scalars().all()
    )
    want = weakest_category(user)

    def score(p: Poi) -> float:
        p_lat, p_lng = p.lat, p.lng
        unexplored = 2 if cell_of(p_lat, p_lng) not in explored else 0
        matches = 1 if _template_for(p.kind)[0] == want else 0
        return unexplored + matches + random.random()

    picked = sorted(pois, key=score, reverse=True)[:count]
    missions = []
    for p in picked:
        category, template = _template_for(p.kind)
        p_lat, p_lng = p.lat, p.lng
        m = Mission(
            owner_id=user.id,
            title=template.format(name=p.name)[:120],
            description=f"Daily random event — expires at midnight. Boosts your {category} stat.",
            type="daily",
            category=category,
            lat=p_lat,
            lng=p_lng,
            place_name=p.name,
            radius_m=100,
            deadline=deadline,
            status="active",
            source="system",
        )
        db.add(m)
        missions.append(m)
    await db.flush()
    return missions
