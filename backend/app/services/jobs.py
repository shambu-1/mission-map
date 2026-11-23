"""Nightly maintenance: expire timed/daily missions, update streaks and slacker stars."""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import exists, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import JobRun, Mission, User
from app.services import progression
from app.services.daily import IST

log = logging.getLogger(__name__)

JOB_NAME = "daily_tick"


async def run_daily_tick(for_day: date) -> bool:
    """Close out `for_day` (an IST calendar date). Returns False if it already ran.

    The run is claimed by inserting a (job, date) row in the same transaction as the
    updates, so restarts (e.g. uvicorn --reload) and multiple API instances can't run it
    twice — slacker stars aren't idempotent.
    """
    now = datetime.now(timezone.utc)
    async with SessionLocal() as db:
        claimed = await db.scalar(
            insert(JobRun)
            .values(job=JOB_NAME, run_date=for_day)
            .on_conflict_do_nothing()
            .returning(JobRun.run_date)
        )
        if claimed is None:
            await db.rollback()
            return False
        # Expire missions past their deadline
        await db.execute(
            update(Mission)
            .where(Mission.deadline < now, Mission.status.in_(("active", "in_progress")), Mission.type == "daily")
            .values(status="expired")
        )
        await db.execute(
            update(Mission)
            .where(Mission.deadline < now, Mission.status.in_(("active", "in_progress")), Mission.type != "daily")
            .values(status="failed")
        )
        # Friend verification window (24 h) lapsed → back to active
        await db.execute(
            update(Mission)
            .where(
                Mission.verification_requested_at < now - timedelta(hours=24),
                Mission.status == "in_progress",
            )
            .values(status="active", verification_requested_at=None)
        )

        users = (await db.execute(select(User))).scalars().all()
        for user in users:
            if user.created_at and user.created_at.astimezone(IST).date() > for_day:
                continue  # joined after that day
            had_active = await db.scalar(
                select(exists().where(Mission.owner_id == user.id, Mission.status.in_(("active", "in_progress"))))
            )
            completed = user.last_completion_date == for_day
            user.slacker_stars = progression.slacker_stars_after_day(user.slacker_stars, bool(had_active), completed)
            # Streak breaks if the last completion was before for_day
            if user.last_completion_date is None or user.last_completion_date < for_day:
                user.streak_days = 0
        await db.commit()
    log.info("Daily tick done for %s", for_day)
    return True


async def scheduler_loop() -> None:
    """Runs inside the API process; checks every 10 minutes whether a new IST day has started."""
    while True:
        try:
            yesterday = datetime.now(IST).date() - timedelta(days=1)
            await run_daily_tick(yesterday)  # no-op if any instance already ran it
        except Exception:  # noqa: BLE001 — keep the loop alive
            log.exception("daily tick failed")
        await asyncio.sleep(600)

