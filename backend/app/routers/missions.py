from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import and_, false, func, or_, select

from app.config import settings
from app.deps import CurrentUser, Db
from app.limiter import limiter
from app.models import Friendship, Mission, SquadMember, Story, User
from app.schemas import (
    CheckinIn,
    CheckinOut,
    ImportLinkIn,
    ImportLinkOut,
    InboxAction,
    MissionIn,
    MissionOut,
    MissionUpdate,
    ReportIn,
)
from app.services import checkin as checkin_service
from app.services import progression, rewards, storage
from app.services.geo import point_wkt
from app.services.maps_link import LinkError, parse_maps_link
from app.services.missions import (
    ACTIVE_STATUSES,
    distance_expr,
    ensure_free_slot,
    ensure_safe_location,
    to_out,
)

router = APIRouter(prefix="/missions", tags=["missions"])

REPORTS_TO_HIDE = 3


async def friend_ids(db, user_id: int) -> list[int]:
    rows = await db.execute(
        select(Friendship.user_id, Friendship.friend_id).where(
            Friendship.status == "accepted", or_(Friendship.user_id == user_id, Friendship.friend_id == user_id)
        )
    )
    return [b if a == user_id else a for a, b in rows.all()]


async def squad_ids(db, user_id: int) -> list[int]:
    return list((await db.execute(select(SquadMember.squad_id).where(SquadMember.user_id == user_id))).scalars())


async def load_mission(db, mission_id: int, user: User, for_write: bool = False) -> Mission:
    m = await db.get(Mission, mission_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")
    if m.owner_id == user.id:
        return m
    if m.squad_id and m.squad_id in await squad_ids(db, user.id) and not for_write:
        return m
    if not for_write:
        if m.visibility == "public" and m.review_status == "approved":
            return m
        if m.visibility == "friends" and m.owner_id in await friend_ids(db, user.id):
            return m
    raise HTTPException(status.HTTP_404_NOT_FOUND, "Mission not found")


async def validate_new_mission(db, user: User, body: MissionIn) -> None:
    if not progression.can_use_type(user.level, body.type):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{body.type.title()} missions are locked at your level")
    if body.type == "home":
        if body.lat is not None:
            raise HTTPException(422, "Home missions have no location")
    else:
        if body.lat is None or body.lng is None:
            raise HTTPException(422, "Pick a place on the map")
        await ensure_safe_location(db, body.lat, body.lng)
    if body.type == "timed" and body.deadline is None:
        raise HTTPException(422, "Timed missions need a deadline")
    if body.deadline and body.deadline <= datetime.now(timezone.utc):
        raise HTTPException(422, "Deadline must be in the future")
    if body.visibility == "public" and progression.unlocks_for_level(user.level).get("public_missions") != "true":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Public missions unlock at level 20")
    if body.type == "main" and body.story_id is None:
        raise HTTPException(422, "Main missions belong to a story — use /stories")


def build_mission(owner_id: int, body: MissionIn, **extra) -> Mission:
    return Mission(
        owner_id=owner_id,
        title=body.title,
        description=body.description,
        type=body.type,
        category=body.category,
        location=point_wkt(body.lat, body.lng) if body.lat is not None else None,
        place_name=body.place_name,
        radius_m=body.radius_m,
        dwell_min=body.dwell_min,
        proof_required=body.proof_required,
        deadline=body.deadline,
        story_id=body.story_id,
        chapter_no=body.chapter_no,
        visibility=body.visibility,
        verifier_id=body.verifier_id,
        review_status="pending" if body.visibility == "public" else "approved",
        **extra,
    )


@router.get("", response_model=list[MissionOut])
async def list_missions(
    user: CurrentUser,
    db: Db,
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius: int = Query(5000, ge=100, le=50000),
    include_done: bool = False,
):
    """Missions near a point (map view): mine + friends' shared + approved public + sponsored + squad."""
    statuses = ACTIVE_STATUSES + (("completed", "failed", "expired") if include_done else ())
    friends = await friend_ids(db, user.id)
    squads = await squad_ids(db, user.id)

    visible = or_(
        Mission.owner_id == user.id,
        Mission.squad_id.in_(squads) if squads else false(),
        and_(Mission.visibility == "friends", Mission.owner_id.in_(friends)) if friends else false(),
        and_(Mission.visibility == "public", Mission.review_status == "approved", Mission.report_count < REPORTS_TO_HIDE),
    )
    q = select(Mission).where(visible, Mission.status.in_(statuses))
    if lat is not None and lng is not None:
        dist = distance_expr(lat, lng)
        here = select(Mission, dist.label("d")).where(
            visible,
            Mission.status.in_(statuses),
            or_(
                Mission.location.is_(None),
                func.ST_DWithin(Mission.location, func.ST_GeogFromText(point_wkt(lat, lng)), radius),
            ),
        ).order_by(dist.nulls_last())
        rows = (await db.execute(here)).all()
        return [to_out(m, user, d) for m, d in rows]
    rows = (await db.execute(q.order_by(Mission.created_at.desc()))).scalars().all()
    return [to_out(m, user) for m in rows]


@router.get("/inbox", response_model=list[MissionOut])
async def inbox(user: CurrentUser, db: Db):
    """Missions sent by friends, waiting to be accepted or declined."""
    rows = (
        await db.execute(
            select(Mission).where(Mission.owner_id == user.id, Mission.status == "draft", Mission.source == "friend")
        )
    ).scalars().all()
    return [to_out(m, user) for m in rows]


@router.post("/inbox/{mission_id}", response_model=MissionOut | None)
async def answer_inbox(mission_id: int, body: InboxAction, user: CurrentUser, db: Db):
    m = await load_mission(db, mission_id, user, for_write=True)
    if m.status != "draft" or m.source != "friend":
        raise HTTPException(status.HTTP_409_CONFLICT, "Not an inbox mission")
    if not body.accept:  # declining has no penalty
        await db.delete(m)
        await db.commit()
        return None
    await ensure_free_slot(db, user)
    m.status = "active"
    await db.commit()
    return to_out(m, user)


@router.post("/import-link", response_model=ImportLinkOut)
@limiter.limit("30/hour")
async def import_link(request: Request, body: ImportLinkIn, user: CurrentUser):
    try:
        parsed = await parse_maps_link(body.url)
    except LinkError as exc:
        raise HTTPException(422, str(exc))
    return ImportLinkOut(
        lat=parsed.lat, lng=parsed.lng, place_name=parsed.place_name, needs_manual_pin=parsed.lat is None
    )


@router.get("/{mission_id}", response_model=MissionOut)
async def get_mission(mission_id: int, user: CurrentUser, db: Db):
    return to_out(await load_mission(db, mission_id, user), user)


@router.post("", response_model=MissionOut, status_code=201)
async def create_mission(body: MissionIn, user: CurrentUser, db: Db):
    await validate_new_mission(db, user, body)
    if body.story_id is not None:
        story = await db.get(Story, body.story_id)
        if story is None or story.owner_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Story not found")
    if body.verifier_id is not None and body.verifier_id not in await friend_ids(db, user.id):
        raise HTTPException(422, "Verifier must be a friend")
    await ensure_free_slot(db, user)
    m = build_mission(user.id, body, source="self", status="active")
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)


@router.patch("/{mission_id}", response_model=MissionOut)
async def update_mission(mission_id: int, body: MissionUpdate, user: CurrentUser, db: Db):
    m = await load_mission(db, mission_id, user, for_write=True)
    if m.status not in ("draft", "active"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"Can't edit a {m.status} mission")
    data = body.model_dump(exclude_unset=True)
    lat, lng = data.pop("lat", None), data.pop("lng", None)
    if (lat is None) != (lng is None):
        raise HTTPException(422, "Send both lat and lng")
    if lat is not None:
        if m.type == "home":
            raise HTTPException(422, "Home missions have no location")
        await ensure_safe_location(db, lat, lng)
        m.location = point_wkt(lat, lng)
    if data.get("visibility") == "public":
        if progression.unlocks_for_level(user.level).get("public_missions") != "true":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Public missions unlock at level 20")
        m.review_status = "pending"
    for k, v in data.items():
        setattr(m, k, v)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)


@router.delete("/{mission_id}", status_code=204)
async def abandon_mission(mission_id: int, user: CurrentUser, db: Db):
    m = await load_mission(db, mission_id, user, for_write=True)
    if m.status in ("completed",):
        raise HTTPException(status.HTTP_409_CONFLICT, "Completed missions stay in your history")
    m.status = "abandoned"
    m.dwell_started_at = None
    await db.commit()


@router.post("/{mission_id}/checkin", response_model=CheckinOut)
@limiter.limit("30/hour")
async def checkin(request: Request, mission_id: int, body: CheckinIn, user: CurrentUser, db: Db):
    m = await load_mission(db, mission_id, user)
    if m.owner_id != user.id and not m.squad_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Take this mission first")
    result = await checkin_service.checkin(db, m, user, body)
    await db.commit()
    return result


@router.post("/{mission_id}/proof", response_model=MissionOut)
async def upload_proof(
    mission_id: int,
    user: CurrentUser,
    db: Db,
    file: UploadFile | None = File(None),
    note: str | None = Form(None, max_length=1000),
):
    """Photo (max 5 MB, EXIF stripped) and/or a short note, stored with the completion."""
    m = await load_mission(db, mission_id, user, for_write=True)
    if m.status not in ACTIVE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Mission is {m.status}")
    if file is None and not note:
        raise HTTPException(422, "Send a photo or a note")
    if file is not None:
        data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
        try:
            m.proof_key = storage.save_proof(user.id, data)
        except storage.UploadError as exc:
            raise HTTPException(422, str(exc))
    if note:
        m.proof_note = note
        if file is None and m.proof_key is None:
            m.proof_key = "note-only"
    await db.commit()
    return to_out(m, user)


@router.post("/{mission_id}/verify", response_model=MissionOut)
async def verify(mission_id: int, user: CurrentUser, db: Db):
    """Friend-verified (co-op): the chosen friend confirms within 24 h."""
    m = await db.get(Mission, mission_id)
    if m is None or m.verifier_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Nothing to verify")
    if m.verification_requested_at is None or m.status != "in_progress":
        raise HTTPException(status.HTTP_409_CONFLICT, "Mission hasn't been checked in yet")
    if datetime.now(timezone.utc) - m.verification_requested_at > timedelta(hours=24):
        m.status, m.verification_requested_at = "active", None
        await db.commit()
        raise HTTPException(status.HTTP_410_GONE, "Verification window (24 h) has passed")
    owner = await db.get(User, m.owner_id)
    await rewards.complete_mission(db, m, owner, None, None, None, verified_by=user.id)
    await rewards.finish_mission_state(db, m)
    await db.commit()
    return to_out(m, user)


@router.get("/to-verify/list", response_model=list[MissionOut])
async def to_verify(user: CurrentUser, db: Db):
    rows = (
        await db.execute(
            select(Mission).where(
                Mission.verifier_id == user.id,
                Mission.status == "in_progress",
                Mission.verification_requested_at.is_not(None),
            )
        )
    ).scalars().all()
    return [to_out(m, user) for m in rows]


@router.post("/{mission_id}/take", response_model=MissionOut, status_code=201)
async def take_public(mission_id: int, user: CurrentUser, db: Db):
    """Copy a public (or sponsored) mission into your own list."""
    src = await load_mission(db, mission_id, user)
    if src.visibility != "public":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only public missions can be taken")
    await ensure_free_slot(db, user)
    m = Mission(
        owner_id=user.id,
        title=src.title,
        description=src.description,
        type="side" if src.type in ("main", "daily") else src.type,
        category=src.category,
        location=src.location,
        place_name=src.place_name,
        radius_m=src.radius_m,
        dwell_min=src.dwell_min,
        proof_required=src.proof_required,
        deadline=src.deadline,
        status="active",
        source="sponsor" if src.sponsor_id else "self",
        sponsor_id=src.sponsor_id,
        assigned_by=src.owner_id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)


@router.post("/{mission_id}/report", status_code=202)
async def report(mission_id: int, body: ReportIn, user: CurrentUser, db: Db):
    """Any user can report a public mission; 3 reports hide it until re-reviewed."""
    m = await load_mission(db, mission_id, user)
    if m.visibility != "public":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only public missions can be reported")
    m.report_count += 1
    if m.report_count >= REPORTS_TO_HIDE:
        m.review_status = "pending"
    await db.commit()
    return {"ok": True}
