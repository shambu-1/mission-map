from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile, status
from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.deps import CurrentUser, Db
from app.limiter import limiter
from app.models import Friendship, Mission, MissionReport, SquadMember, Story, User
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
from app.services import farming, progression, rewards, storage
from app.services.geo import haversine_m
from app.services.maps_link import LinkError, parse_maps_link
from app.services.missions import (
    ACTIVE_STATUSES,
    ensure_free_slot,
    ensure_safe_location,
    to_out,
    within_bbox,
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


def ensure_future_deadline(deadline: datetime | None) -> None:
    if deadline is not None:
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        if deadline <= datetime.now(timezone.utc):
            raise HTTPException(422, "Deadline must be in the future")


async def ensure_not_at_feet(db, user_id: int, lat: float, lng: float, origin_lat, origin_lng) -> None:
    """Anti-farming: a new mission can't be placed where the player is standing."""
    origin = (origin_lat, origin_lng) if origin_lat is not None and origin_lng is not None else None
    if await farming.too_close_to_player(db, user_id, lat, lng, origin):
        raise HTTPException(
            422, f"Pick a place at least {settings.min_mission_distance_m} m from where you are now"
        )


async def validate_place_and_time(db, body: MissionIn) -> None:
    """Rules every new mission follows, whoever creates it."""
    if body.type == "home":
        if body.lat is not None:
            raise HTTPException(422, "Home missions have no location")
    else:
        if body.lat is None or body.lng is None:
            raise HTTPException(422, "Pick a place on the map")
        await ensure_safe_location(db, body.lat, body.lng)
    if body.type == "timed" and body.deadline is None:
        raise HTTPException(422, "Timed missions need a deadline")
    ensure_future_deadline(body.deadline)


async def validate_new_mission(db, user: User, body: MissionIn) -> None:
    if not progression.can_use_type(user.level, body.type):
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"{body.type.title()} missions are locked at your level")
    await validate_place_and_time(db, body)
    if body.lat is not None:
        await ensure_not_at_feet(db, user.id, body.lat, body.lng, body.origin_lat, body.origin_lng)
    if body.visibility == "public" and progression.unlocks_for_level(user.level).get("public_missions") != "true":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Public missions unlock at level 20")
    if body.type == "main" and body.story_id is None:
        raise HTTPException(422, "Main missions belong to a story — use /stories")


async def validate_assigned_mission(db, body: MissionIn, allowed_types: tuple[str, ...]) -> MissionIn:
    """Missions made for someone else (friend inbox, squad co-op): same place/time rules,
    and none of the owner-only links (story, chapter, verifier) or public visibility."""
    if body.type not in allowed_types:
        raise HTTPException(422, f"Allowed types: {', '.join(allowed_types)}")
    await validate_place_and_time(db, body)
    return body.model_copy(
        update={"story_id": None, "chapter_no": None, "verifier_id": None, "visibility": "private"}
    )


def build_mission(owner_id: int, body: MissionIn, **extra) -> Mission:
    return Mission(
        owner_id=owner_id,
        title=body.title,
        description=body.description,
        type=body.type,
        category=body.category,
        lat=body.lat,
        lng=body.lng,
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


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


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
        and_(
            Mission.visibility == "public",
            Mission.review_status == "approved",
            or_(Mission.report_count < REPORTS_TO_HIDE, Mission.sponsor_id.is_not(None)),
        ),
    )
    q = select(Mission).where(visible, Mission.status.in_(statuses))
    if lat is not None and lng is not None:
        # Indexed bounding-box prefilter in SQL, then exact distance + sort in Python
        here = select(Mission).where(
            visible,
            Mission.status.in_(statuses),
            or_(Mission.lat.is_(None), within_bbox(lat, lng, radius)),
        )
        found = []
        for m in (await db.execute(here)).scalars():
            d = haversine_m(lat, lng, m.lat, m.lng) if m.lat is not None else None
            if d is None or d <= radius:
                found.append((m, d))
        found.sort(key=lambda md: (md[1] is None, md[1] or 0))
        return [to_out(m, user, d) for m, d in found]
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
    m.activated_at = now_utc()
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
    m = build_mission(user.id, body, source="self", status="active", activated_at=now_utc())
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
        await ensure_not_at_feet(db, user.id, lat, lng, data.pop("origin_lat", None), data.pop("origin_lng", None))
        m.lat, m.lng = lat, lng
        m.activated_at = now_utc()  # moving a mission restarts the check-in wait
        m.dwell_started_at = None
    data.pop("origin_lat", None)
    data.pop("origin_lng", None)
    if "deadline" in data:
        ensure_future_deadline(data["deadline"])
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
    """Photo (max 5 MB, EXIF stripped) and/or a short note, stored with the completion.
    Only a photo counts as proof (proof_required, +10 XP bonus); a note is just a note."""
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
    await rewards.finish_mission_state(db, m, now_utc())
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
    """Copy a public (or sponsored) mission into your own list — once per player."""
    src = await load_mission(db, mission_id, user)
    if src.visibility != "public" or src.source_mission_id is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only public missions can be taken")
    if src.owner_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "This is your own mission")
    taken = await db.scalar(
        select(Mission.id).where(Mission.owner_id == user.id, Mission.source_mission_id == src.id).limit(1)
    )
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already taken this mission")
    await ensure_free_slot(db, user)
    m = Mission(
        owner_id=user.id,
        title=src.title,
        description=src.description,
        type="side" if src.type in ("main", "daily") else src.type,
        category=src.category,
        lat=src.lat,
        lng=src.lng,
        place_name=src.place_name,
        radius_m=src.radius_m,
        dwell_min=src.dwell_min,
        proof_required=src.proof_required,
        deadline=src.deadline,
        status="active",
        activated_at=now_utc(),
        source="sponsor" if src.sponsor_id else "self",
        sponsor_id=src.sponsor_id,
        source_mission_id=src.id,
        assigned_by=src.owner_id,
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)


@router.post("/{mission_id}/report", status_code=202)
async def report(mission_id: int, body: ReportIn, user: CurrentUser, db: Db):
    """Any user can report a public mission once. Reports from 3 different players hide it
    until re-reviewed; sponsored missions are sent to admin review but stay visible."""
    m = await load_mission(db, mission_id, user)
    if m.visibility != "public":
        raise HTTPException(status.HTTP_409_CONFLICT, "Only public missions can be reported")
    if m.owner_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "You can't report your own mission")
    inserted = await db.scalar(
        pg_insert(MissionReport)
        .values(mission_id=m.id, user_id=user.id, reason=body.reason)
        .on_conflict_do_nothing()
        .returning(MissionReport.user_id)
    )
    if inserted is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "You've already reported this mission")
    m.report_count = await db.scalar(
        select(func.count()).select_from(MissionReport).where(MissionReport.mission_id == m.id)
    )
    if m.report_count >= REPORTS_TO_HIDE and m.sponsor_id is None:
        m.review_status = "pending"
    await db.commit()
    return {"ok": True, "flagged_for_review": m.report_count >= REPORTS_TO_HIDE}
