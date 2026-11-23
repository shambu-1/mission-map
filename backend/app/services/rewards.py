"""Everything that happens when a mission is completed: XP, cash, streak, stats, unlocks, districts."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Completion, District, DistrictClaim, Mission, Sponsor, Story, User
from app.services import progression
from app.services.fog import cell_of, mark_explored
from app.services.geo import point_wkt

DISTRICT_CLAIM_THRESHOLD = 10
IST = ZoneInfo("Asia/Kolkata")  # streak days follow the local calendar


@dataclass
class RewardResult:
    xp_awarded: int
    cash_awarded: int
    level: int
    level_up: bool
    new_unlocks: list[dict] = field(default_factory=list)
    streak_days: int = 0
    district_claimed: str | None = None


async def complete_mission(
    db: AsyncSession,
    mission: Mission,
    user: User,
    lat: float | None,
    lng: float | None,
    accuracy_m: float | None,
    now: datetime | None = None,
    note: str | None = None,
    verified_by: int | None = None,
) -> RewardResult:
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(IST).date()

    # Streak first (the multiplier uses the streak including today)
    user.streak_days = progression.next_streak(user.streak_days, user.last_completion_date, today)
    user.last_completion_date = today
    user.slacker_stars = 0

    # "First visit" bonus = first mission completed in this H3 cell (passive exploring
    # while the app is open clears fog but doesn't use up the bonus).
    first_cell = False
    cell = None
    if lat is not None and lng is not None:
        cell = cell_of(lat, lng)
        done_here = await db.scalar(
            select(Completion.id).where(Completion.user_id == user.id, Completion.h3_index == cell).limit(1)
        )
        first_cell = done_here is None
        await mark_explored(db, user.id, {cell})

    in_time = mission.type == "timed" or mission.deadline is not None
    in_time = in_time and progression.has_deadline_bonus(mission.type, mission.deadline, now)
    breakdown = progression.calculate_xp(
        mission.type, user.streak_days, in_time, mission.proof_key is not None, first_cell
    )
    xp = 0 if user.xp_frozen else breakdown.total
    cash = 0 if user.xp_frozen else progression.CASH_PER_MISSION.get(mission.type, 5)

    old_level = user.level
    user.xp += xp
    user.cash += cash
    user.level = progression.level_from_xp(user.xp)

    # Stats
    stat_attr = progression.CATEGORY_TO_STAT.get(mission.category)
    if stat_attr:
        setattr(user, stat_attr, getattr(user, stat_attr) + 1)
    if first_cell and stat_attr != "stat_explorer":
        user.stat_explorer += 1
    if mission.squad_id is not None and stat_attr != "stat_social":
        user.stat_social += 1
    user.stat_discipline = max(user.stat_discipline, user.streak_days)

    db.add(
        Completion(
            mission_id=mission.id,
            user_id=user.id,
            location=point_wkt(lat, lng) if lat is not None else None,
            accuracy_m=accuracy_m,
            h3_index=cell,
            proof_url=mission.proof_key,
            note=note or mission.proof_note,
            xp_awarded=xp,
            verified_by=verified_by,
            completed_at=now,
        )
    )

    # Sponsored missions are billed per completed check-in
    if mission.sponsor_id:
        sponsor = await db.get(Sponsor, mission.sponsor_id)
        if sponsor:
            sponsor.billable_checkins += 1

    district_claimed = None
    if lat is not None:
        district_claimed = await _progress_district(db, user.id, lat, lng, now)

    return RewardResult(
        xp_awarded=xp,
        cash_awarded=cash,
        level=user.level,
        level_up=user.level > old_level,
        new_unlocks=progression.new_unlocks_between(old_level, user.level),
        streak_days=user.streak_days,
        district_claimed=district_claimed,
    )


async def finish_mission_state(db: AsyncSession, mission: Mission) -> None:
    """Mark completed and, for main story chapters, unlock the next chapter."""
    mission.status = "completed"
    mission.dwell_started_at = None
    if mission.story_id is None or mission.chapter_no is None:
        return
    next_chapter = await db.scalar(
        select(Mission).where(
            Mission.story_id == mission.story_id, Mission.chapter_no == mission.chapter_no + 1
        )
    )
    if next_chapter:
        next_chapter.status = "active"
    else:
        story = await db.get(Story, mission.story_id)
        if story:
            story.status = "completed"


async def _progress_district(db: AsyncSession, user_id: int, lat: float, lng: float, now: datetime) -> str | None:
    district = await db.scalar(
        select(District).where(func.ST_Covers(District.boundary, func.ST_GeogFromText(point_wkt(lat, lng))))
    )
    if district is None:
        return None
    claim = await db.get(DistrictClaim, (user_id, district.id))
    if claim is None:
        claim = DistrictClaim(user_id=user_id, district_id=district.id, missions_done=0)
        db.add(claim)
    claim.missions_done += 1
    if claim.claimed_at is None and claim.missions_done >= DISTRICT_CLAIM_THRESHOLD:
        claim.claimed_at = now
        return district.name
    return None
