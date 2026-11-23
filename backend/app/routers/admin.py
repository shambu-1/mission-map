"""Admin: public-mission review, anti-cheat review, sponsors, districts and restricted zones (Phase 4)."""

import json

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select

from app.config import settings
from app.deps import Db, require_admin
from app.models import CheckinAttempt, District, Mission, MissionReport, RestrictedZone, Sponsor, User
from app.schemas import MissionOut, SponsoredMissionIn, SponsorIn
from app.services import storage
from app.services.geo import point_wkt
from app.services.missions import ensure_safe_location, to_out

def geojson_to_geog(geometry: dict):
    return func.geography(func.ST_SetSRID(func.ST_GeomFromGeoJSON(json.dumps(geometry)), 4326))


router = APIRouter(prefix="/admin", tags=["admin"])
uploads = APIRouter(tags=["uploads"])


class ReviewIn(BaseModel):
    approve: bool


class GeoJsonAreaIn(BaseModel):
    name: str
    geometry: dict  # GeoJSON Polygon (districts) or any geometry (restricted zones)
    kind: str = "custom"


@router.get("/review", response_model=list[MissionOut])
async def pending_public(db: Db, admin: User = Depends(require_admin)):
    """Public missions waiting for review, plus sponsored missions with 3+ reports."""
    rows = (
        await db.execute(
            select(Mission).where(
                Mission.visibility == "public",
                (Mission.review_status == "pending") | ((Mission.sponsor_id.is_not(None)) & (Mission.report_count >= 3)),
            )
        )
    ).scalars().all()
    return [to_out(m, admin) for m in rows]


@router.post("/review/{mission_id}", response_model=MissionOut)
async def review(mission_id: int, body: ReviewIn, db: Db, admin: User = Depends(require_admin)):
    m = await db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    m.review_status = "approved" if body.approve else "rejected"
    if body.approve:
        m.report_count = 0
        await db.execute(delete(MissionReport).where(MissionReport.mission_id == m.id))
    await db.commit()
    return to_out(m, admin)


@router.get("/flags")
async def flagged_users(db: Db, admin: User = Depends(require_admin)):
    rows = (
        await db.execute(
            select(User.id, User.username, User.xp_frozen, func.count(CheckinAttempt.id))
            .join(CheckinAttempt, CheckinAttempt.user_id == User.id)
            .where(CheckinAttempt.result == "flagged")
            .group_by(User.id)
        )
    ).all()
    return [{"user_id": i, "username": u, "xp_frozen": f, "flags": c} for i, u, f, c in rows]


@router.post("/users/{user_id}/unfreeze")
async def unfreeze(user_id: int, db: Db, admin: User = Depends(require_admin)):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    user.xp_frozen = False
    await db.commit()
    return {"ok": True}


@router.post("/sponsors", status_code=201)
async def create_sponsor(body: SponsorIn, db: Db, admin: User = Depends(require_admin)):
    s = Sponsor(business_name=body.business_name, contact=body.contact)
    db.add(s)
    await db.commit()
    return {"id": s.id, "business_name": s.business_name}


@router.get("/sponsors")
async def list_sponsors(db: Db, admin: User = Depends(require_admin)):
    rows = (await db.execute(select(Sponsor))).scalars().all()
    return [
        {"id": s.id, "business_name": s.business_name, "billing_status": s.billing_status, "billable_checkins": s.billable_checkins}
        for s in rows
    ]


@router.post("/sponsors/{sponsor_id}/missions", response_model=MissionOut, status_code=201)
async def sponsored_mission(sponsor_id: int, body: SponsoredMissionIn, db: Db, admin: User = Depends(require_admin)):
    """Sponsored missions are public, always labelled "Sponsored", billed per completed check-in."""
    if await db.get(Sponsor, sponsor_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sponsor not found")
    await ensure_safe_location(db, body.lat, body.lng)
    m = Mission(
        owner_id=admin.id,
        title=body.title,
        description=body.description,
        type="timed" if body.deadline else "side",
        category="explore",
        location=point_wkt(body.lat, body.lng),
        place_name=body.place_name,
        radius_m=body.radius_m,
        deadline=body.deadline,
        status="active",
        visibility="public",
        review_status="approved",
        source="sponsor",
        sponsor_id=sponsor_id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, admin)


@router.post("/districts", status_code=201)
async def add_district(body: GeoJsonAreaIn, db: Db, admin: User = Depends(require_admin)):
    if body.geometry.get("type") != "Polygon":
        raise HTTPException(422, "District must be a GeoJSON Polygon")
    d = District(name=body.name, boundary=geojson_to_geog(body.geometry))
    db.add(d)
    await db.commit()
    return {"id": d.id, "name": d.name}


@router.post("/restricted-zones", status_code=201)
async def add_zone(body: GeoJsonAreaIn, db: Db, admin: User = Depends(require_admin)):
    z = RestrictedZone(kind=body.kind, name=body.name, area=geojson_to_geog(body.geometry))
    db.add(z)
    await db.commit()
    return {"id": z.id}


@uploads.get("/uploads/{key:path}")
async def serve_upload(key: str, sig: str):
    """Local-dev file serving with a signed, expiring URL (S3 serves directly in production)."""
    if settings.s3_bucket or ".." in key or not storage.verify_local_signature(key, sig):
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    from pathlib import Path

    path = Path(settings.local_upload_dir) / key
    if not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return FileResponse(path)
