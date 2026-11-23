"""Polygon areas (districts, restricted zones) without PostGIS.

Polygons are stored as GeoJSON in a JSONB column together with their bounding box.
Lookups prefilter by bounding box in SQL (indexed), then test the point with Shapely.
"""

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import District, RestrictedZone


class AreaError(ValueError):
    pass


def parse_geometry(geojson: dict, polygon_only: bool = False) -> BaseGeometry:
    try:
        geom = shape(geojson)
    except Exception as exc:  # noqa: BLE001 — shapely raises several types
        raise AreaError(f"Invalid GeoJSON geometry: {exc}") from exc
    if polygon_only and geom.geom_type != "Polygon":
        raise AreaError("Must be a GeoJSON Polygon")
    if geom.is_empty or not geom.is_valid:
        raise AreaError("Geometry is empty or invalid (self-intersecting?)")
    min_lng, min_lat, max_lng, max_lat = geom.bounds
    if not (-180 <= min_lng <= max_lng <= 180 and -90 <= min_lat <= max_lat <= 90):
        raise AreaError("Coordinates out of range — GeoJSON uses [longitude, latitude]")
    return geom


def bbox_columns(geom: BaseGeometry) -> dict:
    min_lng, min_lat, max_lng, max_lat = geom.bounds
    return {"min_lng": min_lng, "min_lat": min_lat, "max_lng": max_lng, "max_lat": max_lat}


def _covers_point(model):
    return lambda lat, lng: select(model).where(
        model.min_lat <= lat, model.max_lat >= lat, model.min_lng <= lng, model.max_lng >= lng
    )


async def district_at(db: AsyncSession, lat: float, lng: float) -> District | None:
    p = Point(lng, lat)
    for d in (await db.execute(_covers_point(District)(lat, lng))).scalars():
        if shape(d.boundary).covers(p):
            return d
    return None


async def restricted_zone_at(db: AsyncSession, lat: float, lng: float) -> RestrictedZone | None:
    p = Point(lng, lat)
    for z in (await db.execute(_covers_point(RestrictedZone)(lat, lng))).scalars():
        if shape(z.area).intersects(p):
            return z
    return None


def overlaps_bbox(model, min_lng: float, min_lat: float, max_lng: float, max_lat: float):
    """SQL condition: the row's bounding box overlaps the given box."""
    return (model.min_lng <= max_lng) & (model.max_lng >= min_lng) & (model.min_lat <= max_lat) & (model.max_lat >= min_lat)
