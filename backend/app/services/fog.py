"""Fog of war on an H3 grid (resolution 9 ≈ 0.1 km² per cell)."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import h3
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ExploredCell


def cell_of(lat: float, lng: float) -> str:
    return h3.latlng_to_cell(lat, lng, settings.h3_resolution)


def cell_boundary(cell: str) -> list[list[float]]:
    """GeoJSON ring [[lng, lat], ...] closed."""
    ring = [[lng, lat] for lat, lng in h3.cell_to_boundary(cell)]
    ring.append(ring[0])
    return ring


async def mark_explored(db: AsyncSession, user_id: int, cells: set[str], limit: int | None = None) -> int:
    """Insert cells, ignore ones already explored. At most `limit` new cells. Returns number of new cells."""
    if not cells or limit == 0:
        return 0
    existing = set(
        (
            await db.execute(
                select(ExploredCell.h3_index).where(
                    ExploredCell.user_id == user_id, ExploredCell.h3_index.in_(cells)
                )
            )
        ).scalars()
    )
    new = cells - existing
    if limit is not None and len(new) > limit:
        new = set(sorted(new)[:limit])
    if new:
        stmt = insert(ExploredCell).values([{"user_id": user_id, "h3_index": c} for c in new])
        await db.execute(stmt.on_conflict_do_nothing())
    return len(new)


def cells_within(cells: set[str], min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> list[str]:
    """Filter the player's own cells to the visible box (by cell centre).

    Work is bounded by how many cells the player has explored, never by the size of the
    box — filling an arbitrary box with hexagons could mean tens of millions of cells.
    """
    out = []
    for c in cells:
        lat, lng = h3.cell_to_latlng(c)
        if min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
            out.append(c)
    return out


@dataclass
class ExplorePoint:
    lat: float
    lng: float
    at: datetime
    accuracy_m: float | None = None


def sanitize_points(
    points: list[ExplorePoint], last: ExplorePoint | None, now: datetime
) -> list[ExplorePoint]:
    """Keep only believable points: recent, accurate enough, and reachable from the
    previous accepted point without impossible speed."""
    from app.services.geo import haversine_m

    oldest = now - timedelta(minutes=settings.explore_max_point_age_min)
    newest = now + timedelta(seconds=settings.max_clock_skew_s)
    candidates = sorted(
        (p for p in points if oldest <= p.at <= newest and (p.accuracy_m is None or p.accuracy_m <= 100)),
        key=lambda p: p.at,
    )
    kept: list[ExplorePoint] = []
    prev = last if last is not None and last.at >= now - timedelta(hours=6) else None
    for p in candidates:
        if prev is not None:
            dist = haversine_m(prev.lat, prev.lng, p.lat, p.lng)
            seconds = (p.at - prev.at).total_seconds()
            if dist >= 200 and (seconds <= 0 or dist / seconds * 3.6 > settings.max_travel_speed_kmh):
                continue
        kept.append(p)
        prev = p
    return kept


async def new_cells_today(db: AsyncSession, user_id: int, day_start: datetime) -> int:
    return await db.scalar(
        select(func.count()).select_from(ExploredCell).where(
            ExploredCell.user_id == user_id, ExploredCell.first_seen_at >= day_start
        )
    ) or 0
