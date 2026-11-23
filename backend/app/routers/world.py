"""Daily events, fog of war, districts."""


from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select

from app.config import settings
from app.deps import CurrentUser, Db, require_unlock
from app.limiter import limiter
from app.models import District, DistrictClaim, ExploredCell, User
from app.schemas import ExploreIn, ExploreOut, MissionOut
from app.services import daily as daily_service
from app.services.farming import start_of_local_day
from app.services.fog import (
    ExplorePoint,
    cell_boundary,
    cell_of,
    cells_within,
    mark_explored,
    new_cells_today,
    sanitize_points,
)
from app.services.areas import overlaps_bbox
from app.services.geo import parse_bbox
from app.services.missions import to_out

router = APIRouter(tags=["world"])

MAX_FEATURES = 20000


@router.get("/daily", response_model=list[MissionOut])
async def daily(
    db: Db,
    lat: float = Query(ge=-90, le=90),
    lng: float = Query(ge=-180, le=180),
    user: User = Depends(require_unlock("daily_events")),
):
    """Today's random events near the user (generated on first request of the day)."""
    missions = await daily_service.generate_daily(db, user, lat, lng)
    await db.commit()
    return [to_out(m, user) for m in missions]


@router.post("/explore", response_model=ExploreOut)
@limiter.limit("60/hour")
async def explore(request: Request, body: ExploreIn, user: CurrentUser, db: Db):
    """Batch of visited points (sent every ~2 min while the app is open) → fog-of-war cells.

    Points must be recent, reasonably accurate and reachable from the last accepted point
    without impossible speed; new cells per day are capped."""
    now = datetime.now(timezone.utc)
    last = (
        ExplorePoint(user.last_explore_lat, user.last_explore_lng, user.last_explore_at)
        if user.last_explore_at is not None
        else None
    )
    points = [ExplorePoint(p.lat, p.lng, p.timestamp or now, p.accuracy_m) for p in body.points]
    for p in points:
        if p.at.tzinfo is None:
            p.at = p.at.replace(tzinfo=timezone.utc)
    kept = sanitize_points(points, last, now)

    new = 0
    if kept:
        room = max(0, settings.explore_new_cells_per_day - await new_cells_today(db, user.id, start_of_local_day(now)))
        new = await mark_explored(db, user.id, {cell_of(p.lat, p.lng) for p in kept}, limit=room)
        user.last_explore_lat, user.last_explore_lng = kept[-1].lat, kept[-1].lng
        user.last_explore_at = min(kept[-1].at, now)
        if new:
            user.stat_explorer += new
    await db.commit()
    total = await db.scalar(select(func.count()).select_from(ExploredCell).where(ExploredCell.user_id == user.id))
    return ExploreOut(new_cells=new, total_cells=total or 0)


@router.get("/map/explored")
async def explored(user: CurrentUser, db: Db, bbox: str = Query(...)):
    """Explored H3 cells inside the visible area, as GeoJSON polygons (the client cuts them out of the fog)."""
    try:
        min_lng, min_lat, max_lng, max_lat = parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    limit = settings.max_explored_bbox_deg
    if max_lng - min_lng > limit or max_lat - min_lat > limit or min_lng > max_lng or min_lat > max_lat:
        raise HTTPException(422, f"Area too large — zoom in (max {limit}° per side)")
    mine = set(
        (await db.execute(select(ExploredCell.h3_index).where(ExploredCell.user_id == user.id))).scalars().all()
    )
    visible = cells_within(mine, min_lng, min_lat, max_lng, max_lat)[:MAX_FEATURES]
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {"h3": c}, "geometry": {"type": "Polygon", "coordinates": [cell_boundary(c)]}}
            for c in visible
        ],
    }


@router.get("/districts")
async def districts(user: CurrentUser, db: Db, bbox: str = Query(...)):
    """District polygons and the user's claim status."""
    try:
        min_lng, min_lat, max_lng, max_lat = parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    rows = (
        await db.execute(
            select(District, DistrictClaim)
            .outerjoin(
                DistrictClaim, (DistrictClaim.district_id == District.id) & (DistrictClaim.user_id == user.id)
            )
            .where(overlaps_bbox(District, min_lng, min_lat, max_lng, max_lat))
            .limit(500)
        )
    ).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": d.boundary,
                "properties": {
                    "id": d.id,
                    "name": d.name,
                    "missions_done": claim.missions_done if claim else 0,
                    "claimed": bool(claim and claim.claimed_at),
                    "needed": 10,
                },
            }
            for d, claim in rows
        ],
    }
