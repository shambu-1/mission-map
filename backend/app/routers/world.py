"""Daily events, fog of war, districts."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select

from app.deps import CurrentUser, Db, require_unlock
from app.models import District, DistrictClaim, ExploredCell, User
from app.schemas import ExploreIn, ExploreOut, MissionOut
from app.services import daily as daily_service
from app.services.fog import cell_boundary, cell_of, cells_in_bbox, mark_explored
from app.services.geo import parse_bbox
from app.services.missions import to_out

router = APIRouter(tags=["world"])

MAX_BBOX_CELLS = 20000


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
async def explore(body: ExploreIn, user: CurrentUser, db: Db):
    """Batch of visited points (sent every ~2 min while the app is open) → fog-of-war cells."""
    cells = {cell_of(p.lat, p.lng) for p in body.points if p.accuracy_m is None or p.accuracy_m <= 100}
    new = await mark_explored(db, user.id, cells)
    if new:
        user.stat_explorer += new
    await db.commit()
    total = await db.scalar(select(func.count()).select_from(ExploredCell).where(ExploredCell.user_id == user.id))
    return ExploreOut(new_cells=new, total_cells=total or 0)


@router.get("/map/explored")
async def explored(user: CurrentUser, db: Db, bbox: str = Query(...)):
    """Explored H3 cells inside the visible area, as GeoJSON polygons (the client cuts them out of the fog)."""
    try:
        box = parse_bbox(bbox)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    mine = set(
        (await db.execute(select(ExploredCell.h3_index).where(ExploredCell.user_id == user.id))).scalars().all()
    )
    if not mine:
        visible = set()
    else:
        area_cells = cells_in_bbox(*box)
        visible = mine & area_cells if len(area_cells) <= MAX_BBOX_CELLS else mine
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
    envelope = func.ST_MakeEnvelope(min_lng, min_lat, max_lng, max_lat, 4326)
    rows = (
        await db.execute(
            select(District, func.ST_AsGeoJSON(District.boundary), DistrictClaim)
            .outerjoin(
                DistrictClaim, (DistrictClaim.district_id == District.id) & (DistrictClaim.user_id == user.id)
            )
            .where(func.ST_Intersects(District.boundary, func.geography(envelope)))
        )
    ).all()
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": json.loads(geojson),
                "properties": {
                    "id": d.id,
                    "name": d.name,
                    "missions_done": claim.missions_done if claim else 0,
                    "claimed": bool(claim and claim.claimed_at),
                    "needed": 10,
                },
            }
            for d, geojson, claim in rows
        ],
    }
