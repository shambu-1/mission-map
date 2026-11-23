"""Fog of war on an H3 grid (resolution 9 ≈ 0.1 km² per cell)."""

import h3
from sqlalchemy import select
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


async def is_new_cell(db: AsyncSession, user_id: int, cell: str) -> bool:
    found = await db.scalar(
        select(ExploredCell.h3_index).where(ExploredCell.user_id == user_id, ExploredCell.h3_index == cell)
    )
    return found is None


async def mark_explored(db: AsyncSession, user_id: int, cells: set[str]) -> int:
    """Insert cells, ignore ones already explored. Returns number of new cells."""
    if not cells:
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
    if new:
        stmt = insert(ExploredCell).values([{"user_id": user_id, "h3_index": c} for c in new])
        await db.execute(stmt.on_conflict_do_nothing())
    return len(new)


def cells_in_bbox(min_lng: float, min_lat: float, max_lng: float, max_lat: float) -> set[str]:
    poly = h3.LatLngPoly([(min_lat, min_lng), (min_lat, max_lng), (max_lat, max_lng), (max_lat, min_lng)])
    return set(h3.polygon_to_cells(poly, settings.h3_resolution))
