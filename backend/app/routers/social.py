"""Friends, sending missions, squads and co-op, friend leaderboards (Phase 3)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, delete, func, or_, select

from app.deps import CurrentUser, Db, require_unlock
from app.models import Completion, Friendship, Mission, Squad, SquadMember, User
from app.schemas import FriendOut, FriendRequestIn, MissionOut, SendMissionIn, SquadIn, SquadMissionIn, SquadOut
from app.services import progression
from app.services.missions import active_slots_used, to_out
from app.routers.missions import build_mission, friend_ids, validate_assigned_mission

router = APIRouter(tags=["social"])

MAX_SQUAD = 6


async def weekly_xp(db, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    since = datetime.now(timezone.utc) - timedelta(days=7)
    rows = await db.execute(
        select(Completion.user_id, func.coalesce(func.sum(Completion.xp_awarded), 0))
        .where(Completion.user_id.in_(user_ids), Completion.completed_at >= since)
        .group_by(Completion.user_id)
    )
    return dict(rows.all())


# ---------------- Friends ----------------
@router.get("/friends", response_model=list[FriendOut])
async def list_friends(user: CurrentUser, db: Db):
    rows = (
        await db.execute(
            select(Friendship).where(or_(Friendship.user_id == user.id, Friendship.friend_id == user.id))
        )
    ).scalars().all()
    other_ids = [f.friend_id if f.user_id == user.id else f.user_id for f in rows]
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(other_ids)))).scalars()} if other_ids else {}
    xp = await weekly_xp(db, other_ids)
    out = []
    for f in rows:
        other = f.friend_id if f.user_id == user.id else f.user_id
        u = users.get(other)
        if not u:
            continue
        direction = "mutual" if f.status == "accepted" else ("outgoing" if f.user_id == user.id else "incoming")
        out.append(FriendOut(user_id=u.id, username=u.username, level=u.level, status=f.status, direction=direction, weekly_xp=xp.get(u.id, 0)))
    return out


@router.post("/friends", response_model=FriendOut, status_code=201)
async def add_friend(body: FriendRequestIn, user: CurrentUser, db: Db):
    """Add by username (an invite link just pre-fills the username)."""
    other = await db.scalar(select(User).where(User.username == body.username.lower().strip()))
    if other is None or other.id == user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    existing = await db.scalar(
        select(Friendship).where(
            or_(
                and_(Friendship.user_id == user.id, Friendship.friend_id == other.id),
                and_(Friendship.user_id == other.id, Friendship.friend_id == user.id),
            )
        )
    )
    if existing:
        # They already asked us → accept
        if existing.user_id == other.id and existing.status == "pending":
            existing.status = "accepted"
            await db.commit()
            return FriendOut(user_id=other.id, username=other.username, level=other.level, status="accepted", direction="mutual")
        raise HTTPException(status.HTTP_409_CONFLICT, "Already friends or request pending")
    db.add(Friendship(user_id=user.id, friend_id=other.id, status="pending"))
    await db.commit()
    return FriendOut(user_id=other.id, username=other.username, level=other.level, status="pending", direction="outgoing")


@router.post("/friends/{friend_id}/accept", response_model=FriendOut)
async def accept_friend(friend_id: int, user: CurrentUser, db: Db):
    f = await db.get(Friendship, (friend_id, user.id))
    if f is None or f.status != "pending":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No pending request")
    f.status = "accepted"
    await db.commit()
    other = await db.get(User, friend_id)
    return FriendOut(user_id=other.id, username=other.username, level=other.level, status="accepted", direction="mutual")


@router.delete("/friends/{friend_id}", status_code=204)
async def remove_friend(friend_id: int, user: CurrentUser, db: Db):
    await db.execute(
        delete(Friendship).where(
            or_(
                and_(Friendship.user_id == user.id, Friendship.friend_id == friend_id),
                and_(Friendship.user_id == friend_id, Friendship.friend_id == user.id),
            )
        )
    )
    await db.commit()


@router.get("/friends/{friend_id}/profile")
async def friend_profile(friend_id: int, user: CurrentUser, db: Db):
    """Friends see completed missions and district badges — never live location or raw paths."""
    if friend_id not in await friend_ids(db, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a friend")
    friend = await db.get(User, friend_id)
    done = (
        await db.execute(
            select(Mission.title, Mission.type, Completion.completed_at)
            .join(Completion, Completion.mission_id == Mission.id)
            .where(Completion.user_id == friend_id, Mission.visibility != "private")
            .order_by(Completion.completed_at.desc())
            .limit(20)
        )
    ).all()
    return {
        "username": friend.username,
        "level": friend.level,
        "streak_days": friend.streak_days,
        "completed": [{"title": t, "type": ty, "completed_at": at} for t, ty, at in done],
    }


@router.post("/friends/{friend_id}/missions", response_model=MissionOut, status_code=201)
async def send_mission(
    friend_id: int, body: SendMissionIn, db: Db, user: User = Depends(require_unlock("send_to_friends"))
):
    """Send a friend a mission ("try this café"). Lands in their inbox to accept or decline."""
    if friend_id not in await friend_ids(db, user.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not a friend")
    friend = await db.get(User, friend_id)
    body = await validate_assigned_mission(db, body, ("side", "timed", "home"))
    # Friends can't exceed the receiver's free slots (counting what's already in the inbox)
    pending = await db.scalar(
        select(func.count()).select_from(Mission).where(
            Mission.owner_id == friend_id, Mission.status == "draft", Mission.source == "friend"
        )
    )
    free = progression.slot_limit(friend.level, friend.is_premium) + friend.extra_slots - await active_slots_used(db, friend_id)
    if (pending or 0) >= free:
        raise HTTPException(status.HTTP_409_CONFLICT, f"{friend.username} has no free mission slots")
    m = build_mission(friend_id, body, source="friend", status="draft", assigned_by=user.id)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)


@router.get("/leaderboard")
async def leaderboard(user: CurrentUser, db: Db):
    """Friends and self only, weekly XP. No global board."""
    ids = await friend_ids(db, user.id) + [user.id]
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    xp = await weekly_xp(db, ids)
    board = sorted(
        ({"user_id": u.id, "username": u.username, "level": u.level, "weekly_xp": xp.get(u.id, 0)} for u in users),
        key=lambda r: r["weekly_xp"],
        reverse=True,
    )
    return board


# ---------------- Squads ----------------
async def squad_out(db, squad: Squad, user_id: int) -> SquadOut:
    rows = (
        await db.execute(
            select(SquadMember, User.username, User.level)
            .join(User, User.id == SquadMember.user_id)
            .where(SquadMember.squad_id == squad.id)
        )
    ).all()
    members = [{"user_id": m.user_id, "username": name, "level": lvl, "role": m.role} for m, name, lvl in rows]
    my_role = next((m["role"] for m in members if m["user_id"] == user_id), "none")
    return SquadOut(id=squad.id, name=squad.name, members=members, my_role=my_role)


@router.get("/squads", response_model=list[SquadOut])
async def list_squads(user: CurrentUser, db: Db):
    squads = (
        await db.execute(
            select(Squad).join(SquadMember, SquadMember.squad_id == Squad.id).where(SquadMember.user_id == user.id)
        )
    ).scalars().all()
    return [await squad_out(db, s, user.id) for s in squads]


@router.post("/squads", response_model=SquadOut, status_code=201)
async def create_squad(body: SquadIn, db: Db, user: User = Depends(require_unlock("squads"))):
    """2–6 players; members must be friends."""
    friends = set(await friend_ids(db, user.id))
    members = []
    for name in body.member_usernames:
        u = await db.scalar(select(User).where(User.username == name.lower()))
        if u is None or u.id not in friends:
            raise HTTPException(422, f"{name} isn't your friend")
        members.append(u.id)
    if len(set(members)) + 1 > MAX_SQUAD:
        raise HTTPException(422, f"Squads have at most {MAX_SQUAD} players")
    squad = Squad(name=body.name)
    db.add(squad)
    await db.flush()
    db.add(SquadMember(squad_id=squad.id, user_id=user.id, role="leader"))
    for uid in set(members):
        db.add(SquadMember(squad_id=squad.id, user_id=uid, role="member"))
    await db.commit()
    return await squad_out(db, squad, user.id)


@router.delete("/squads/{squad_id}/leave", status_code=204)
async def leave_squad(squad_id: int, user: CurrentUser, db: Db):
    await db.execute(delete(SquadMember).where(SquadMember.squad_id == squad_id, SquadMember.user_id == user.id))
    left = await db.scalar(select(func.count()).select_from(SquadMember).where(SquadMember.squad_id == squad_id))
    if not left:
        squad = await db.get(Squad, squad_id)
        if squad:
            await db.delete(squad)
    await db.commit()


@router.get("/squads/{squad_id}/missions", response_model=list[MissionOut])
async def squad_missions(squad_id: int, user: CurrentUser, db: Db):
    if await db.get(SquadMember, (squad_id, user.id)) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Squad not found")
    rows = (await db.execute(select(Mission).where(Mission.squad_id == squad_id).order_by(Mission.created_at.desc()))).scalars().all()
    return [to_out(m, user) for m in rows]


@router.post("/squads/{squad_id}/missions", response_model=MissionOut, status_code=201)
async def create_squad_mission(squad_id: int, body: SquadMissionIn, user: CurrentUser, db: Db):
    """Co-op mission: completes when every member checks in within the time window (deadline)."""
    member = await db.get(SquadMember, (squad_id, user.id))
    if member is None or member.role != "leader":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the squad leader can create squad missions")
    if body.lat is None or body.lng is None:
        raise HTTPException(422, "Co-op missions need a place")
    if body.deadline is None:
        raise HTTPException(422, "Co-op missions need a time window (deadline)")
    body = await validate_assigned_mission(db, body, ("side", "timed"))
    m = build_mission(user.id, body, source="squad", status="active", squad_id=squad_id)
    m.category = "social"
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return to_out(m, user)
