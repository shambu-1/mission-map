"""Geofence check-in — all decisions happen on the server."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import CheckinAttempt, CoopCheckin, Mission, SquadMember, User
from app.schemas import CheckinIn, CheckinOut
from app.services import anticheat, farming, rewards
from app.services.geo import haversine_m

# "I'm here" fallback (weak GPS + photo proof): still has to be close to the place
MANUAL_MAX_DISTANCE_M = 300


def _reject(reason: str, distance: float | None = None) -> CheckinOut:
    return CheckinOut(status="rejected", reason=reason, distance_m=distance)


async def _log(db, mission, user, body: CheckinIn, result: str, reason: str | None, speed_mps=None):
    db.add(
        CheckinAttempt(
            mission_id=mission.id,
            user_id=user.id,
            lat=body.lat,
            lng=body.lng,
            accuracy_m=body.accuracy_m,
            speed_mps=speed_mps,
            result=result,
            reason=reason,
        )
    )


async def _complete(db, mission, user, body: CheckinIn, now, note=None) -> CheckinOut:
    reward = await rewards.complete_mission(db, mission, user, body.lat, body.lng, body.accuracy_m, now, note=note)
    await rewards.finish_mission_state(db, mission, now)
    reason = "XP frozen pending review" if user.xp_frozen else reward.note
    return CheckinOut(
        status="completed",
        xp_awarded=reward.xp_awarded,
        cash_awarded=reward.cash_awarded,
        level=reward.level,
        level_up=reward.level_up,
        new_unlocks=reward.new_unlocks,
        streak_days=reward.streak_days,
        district_claimed=reward.district_claimed,
        reason=reason,
    )


def clock_skew_s(ts: datetime | None, now: datetime) -> float:
    """Seconds between the phone's timestamp and the server clock (0 if none sent)."""
    if ts is None:
        return 0.0
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return abs((ts - now).total_seconds())


async def checkin(db: AsyncSession, mission: Mission, user: User, body: CheckinIn) -> CheckinOut:
    # All timing uses the server clock. The phone's timestamp is only sanity-checked:
    # trusting it would let a player fake travel speed (e.g. a timestamp a day ahead).
    now = datetime.now(timezone.utc)
    if clock_skew_s(body.timestamp, now) > settings.max_clock_skew_s:
        return _reject("Your phone's clock is off — turn on automatic date & time and try again")

    if mission.status not in ("active", "in_progress"):
        return _reject(f"Mission is {mission.status}")

    if mission.deadline and now > mission.deadline:
        mission.status = "expired" if mission.type == "daily" else "failed"
        return _reject("Mission deadline has passed")

    # Anti-farming: a mission can't be checked in right after it was created
    opens = farming.checkin_opens_at(mission)
    if opens and now < opens:
        minutes = int((opens - now).total_seconds() // 60) + 1
        return _reject(f"Check-in opens in {minutes} min — new missions need a short wait")

    # Home missions: no location, self-confirmed
    if mission.lat is None or mission.lng is None:
        if mission.proof_required and not mission.proof_key:
            return _reject("Upload proof first")
        return await _complete(db, mission, user, CheckinIn(), now)

    if body.lat is None or body.lng is None or body.accuracy_m is None:
        return _reject("Location is required for this mission")

    if await anticheat.attempts_last_hour(db, user.id) >= settings.checkin_attempts_per_hour:
        return _reject("Too many check-in attempts, try again later")

    recent = await anticheat.recent_points(db, user.id)
    prev = recent[0] if recent else None
    speed = anticheat.speed_kmh(prev, body.lat, body.lng, now) if prev else None

    # Anti-cheat (speed between server-side times of accepted points)
    flag_reason = None
    if anticheat.impossible_travel(prev, body.lat, body.lng, now):
        flag_reason = "impossible travel speed"
    elif reason := anticheat.suspicious_accuracy(body.accuracy_m, recent, body.lat, body.lng):
        flag_reason = reason
    if flag_reason:
        await _log(db, mission, user, body, "flagged", flag_reason, speed / 3.6 if speed else None)
        await db.flush()
        if await anticheat.flags_last_week(db, user.id) >= settings.flags_per_week_to_freeze:
            user.xp_frozen = True
        return _reject("Location check failed")

    # Safety: don't play while driving (only meaningful for recent, nearby samples)
    if speed is not None and prev and (now - prev.at) < timedelta(minutes=5) and speed > settings.driving_lock_kmh:
        await _log(db, mission, user, body, "rejected", "moving too fast", speed / 3.6)
        return _reject("Looks like you're moving fast — don't play while driving")

    distance = haversine_m(mission.lat, mission.lng, body.lat, body.lng)

    # Manual "I'm here" fallback when GPS is weak: replaces only the GPS-accuracy check.
    # It needs a photo, must be within MANUAL_MAX_DISTANCE_M, and still runs dwell time.
    note = None
    if body.manual:
        if not mission.proof_key:
            return _reject("Manual check-in needs a photo proof first", distance)
        if distance > MANUAL_MAX_DISTANCE_M:
            await _log(db, mission, user, body, "rejected", "manual too far")
            return _reject(f"{int(distance)} m away", distance)
        note = "manual check-in"
    elif body.accuracy_m > settings.max_gps_accuracy_m:
        await _log(db, mission, user, body, "rejected", "weak gps")
        return _reject("GPS too weak, move to open sky", distance)
    elif distance > mission.radius_m + min(body.accuracy_m, settings.accuracy_bonus_cap_m):
        await _log(db, mission, user, body, "rejected", "outside geofence")
        if mission.dwell_started_at:  # left the geofence → dwell resets
            mission.dwell_started_at = None
            mission.status = "active"
        return _reject(f"{int(distance)} m away", distance)

    # Inside the geofence
    if mission.dwell_min:
        if mission.dwell_started_at is None:
            mission.dwell_started_at = now
            mission.status = "in_progress"
            await _log(db, mission, user, body, "in_progress", "dwell started")
            return CheckinOut(status="in_progress", distance_m=distance, dwell_remaining_min=float(mission.dwell_min))
        elapsed = (now - mission.dwell_started_at).total_seconds() / 60
        if elapsed < mission.dwell_min:
            await _log(db, mission, user, body, "in_progress", "dwell running")
            return CheckinOut(
                status="in_progress", distance_m=distance, dwell_remaining_min=round(mission.dwell_min - elapsed, 1)
            )

    if mission.proof_required and not mission.proof_key:
        mission.status = "in_progress"
        await _log(db, mission, user, body, "in_progress", "waiting for proof")
        return CheckinOut(status="in_progress", reason="You're here — upload the photo proof to finish", distance_m=distance)

    await _log(db, mission, user, body, "accepted", note)
    return await _finish_or_wait(db, mission, user, body, now, distance, note=note)


async def _finish_or_wait(db, mission: Mission, user: User, body: CheckinIn, now, distance, note=None) -> CheckinOut:
    # Friend-verified missions wait for the friend's confirmation (24 h)
    if mission.verifier_id:
        mission.status = "in_progress"
        mission.verification_requested_at = now
        return CheckinOut(status="in_progress", reason="Waiting for your friend to verify", distance_m=distance)

    # Squad co-op: completes when every member checks in within the window
    if mission.squad_id:
        return await _coop_checkin(db, mission, user, body, now, distance)

    out = await _complete(db, mission, user, body, now, note)
    out.distance_m = distance
    return out


async def _coop_checkin(db, mission: Mission, user: User, body: CheckinIn, now, distance) -> CheckinOut:
    if await db.get(CoopCheckin, (mission.id, user.id)) is None:
        db.add(CoopCheckin(mission_id=mission.id, user_id=user.id, checked_in_at=now))
        await db.flush()
    members = (await db.execute(select(SquadMember.user_id).where(SquadMember.squad_id == mission.squad_id))).scalars().all()
    done = set(
        (await db.execute(select(CoopCheckin.user_id).where(CoopCheckin.mission_id == mission.id))).scalars().all()
    )
    waiting = [m for m in members if m not in done]
    if waiting:
        mission.status = "in_progress"
        return CheckinOut(
            status="in_progress",
            reason=f"Checked in — waiting for {len(waiting)} squad member(s)",
            distance_m=distance,
        )
    # Everyone is in: award every member
    my_result = None
    for member_id in members:
        member = await db.get(User, member_id)
        reward = await rewards.complete_mission(db, mission, member, body.lat, body.lng, None, now, note="squad co-op")
        if member_id == user.id:
            my_result = reward
    await rewards.finish_mission_state(db, mission, now)
    return CheckinOut(
        status="completed",
        distance_m=distance,
        xp_awarded=my_result.xp_awarded,
        cash_awarded=my_result.cash_awarded,
        level=my_result.level,
        level_up=my_result.level_up,
        new_unlocks=my_result.new_unlocks,
        streak_days=my_result.streak_days,
        district_claimed=my_result.district_claimed,
    )
