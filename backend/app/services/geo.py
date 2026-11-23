import math
from urllib.parse import quote

from geoalchemy2.shape import to_shape

EARTH_RADIUS_M = 6_371_000

TRAVEL_MODES = ("driving", "walking", "two-wheeler", "transit")


def point_wkt(lat: float, lng: float) -> str:
    return f"SRID=4326;POINT({lng} {lat})"


def latlng_of(geog) -> tuple[float, float] | None:
    if geog is None:
        return None
    p = to_shape(geog)
    return p.y, p.x


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def valid_latlng(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180


def waypoint_url(lat: float, lng: float, travel_mode: str = "driving") -> str:
    """Google Maps "Set waypoint" link — free URL scheme, no API key needed."""
    mode = travel_mode if travel_mode in TRAVEL_MODES else "driving"
    return f"https://www.google.com/maps/dir/?api=1&destination={lat},{lng}&travelmode={quote(mode)}"


def view_url(lat: float, lng: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    """bbox = 'minLng,minLat,maxLng,maxLat'."""
    parts = [float(x) for x in bbox.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minLng,minLat,maxLng,maxLat")
    min_lng, min_lat, max_lng, max_lat = parts
    if not (valid_latlng(min_lat, min_lng) and valid_latlng(max_lat, max_lng)):
        raise ValueError("bbox out of range")
    return min_lng, min_lat, max_lng, max_lat
