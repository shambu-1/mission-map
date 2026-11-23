"""Import a mission location from a shared Google Maps link."""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

import httpx

from app.services.geo import valid_latlng

# Prefer !3d!4d (the place itself), then @lat,lng (the camera), then query params.
PATTERNS = [
    r"!3d(-?\d+(?:\.\d+)?)!4d(-?\d+(?:\.\d+)?)",
    r"@(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)",
    r"[?&](?:q|query|destination|ll|center)=(-?\d+(?:\.\d+)?)(?:,|%2C)\s*(-?\d+(?:\.\d+)?)",
]

SHORT_LINK_HOSTS = {"maps.app.goo.gl", "goo.gl"}

# SSRF protection: only these hosts may be fetched / redirected through.
ALLOWED_HOSTS = {
    "maps.app.goo.gl",
    "goo.gl",
    "google.com",
    "www.google.com",
    "maps.google.com",
    "google.co.in",
    "www.google.co.in",
    "maps.google.co.in",
    "consent.google.com",
}


@dataclass
class ParsedLink:
    lat: float | None
    lng: float | None
    place_name: str | None
    resolved_url: str


class LinkError(ValueError):
    pass


def host_allowed(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and (parsed.hostname or "").lower() in ALLOWED_HOSTS


def is_short_link(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return host == "maps.app.goo.gl" or (host == "goo.gl" and parsed.path.startswith("/maps"))


def extract_coords(url: str) -> tuple[float, float] | None:
    text = unquote(url)
    for pattern in PATTERNS:
        if m := re.search(pattern, text):
            lat, lng = float(m[1]), float(m[2])
            if valid_latlng(lat, lng):
                return lat, lng
    return None


def extract_place_name(url: str) -> str | None:
    """`/maps/place/Cubbon+Park/@...` → 'Cubbon Park'."""
    if m := re.search(r"/maps/place/([^/@?]+)", url):
        return unquote(m[1].replace("+", " ")).strip() or None
    if m := re.search(r"[?&]q=([^&]+)", url):
        value = unquote(m[1].replace("+", " ")).strip()
        if not re.fullmatch(r"-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?", value):
            return value
    return None


async def resolve_short_link(url: str, client: httpx.AsyncClient | None = None) -> str:
    """Follow redirects by hand (max 5 hops, 3 s timeout), refusing any non-Google host."""
    own_client = client is None
    client = client or httpx.AsyncClient(follow_redirects=False, timeout=3)
    try:
        current = url
        for _ in range(5):
            if not host_allowed(current):
                raise LinkError("Redirect left Google Maps; link refused")
            resp = await client.head(current)
            if resp.status_code in (405, 404):
                resp = await client.get(current)
            location = resp.headers.get("location")
            if resp.is_redirect and location:
                current = str(httpx.URL(current).join(location))
                continue
            return current
        raise LinkError("Too many redirects")
    except httpx.HTTPError as exc:
        raise LinkError(f"Could not open link: {exc.__class__.__name__}") from exc
    finally:
        if own_client:
            await client.aclose()


async def parse_maps_link(url: str, client: httpx.AsyncClient | None = None) -> ParsedLink:
    url = url.strip()
    if not host_allowed(url):
        raise LinkError("Only Google Maps links are supported")
    resolved = await resolve_short_link(url, client) if is_short_link(url) else url
    coords = extract_coords(resolved)
    name = extract_place_name(resolved)
    if coords:
        return ParsedLink(coords[0], coords[1], name, resolved)
    # Fallback: name only, the user drops the pin manually.
    return ParsedLink(None, None, name, resolved)
