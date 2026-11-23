from datetime import date, datetime, timedelta, timezone

import pytest

from app.services import progression as p
from app.services.anticheat import PrevPoint, impossible_travel, suspicious_accuracy
from app.services.geo import haversine_m, waypoint_url
from app.services.maps_link import extract_coords, extract_place_name, host_allowed, is_short_link


# ---------- progression ----------
def test_xp_to_next_formula():
    assert p.xp_to_next(1) == 100
    assert p.xp_to_next(2) == 283
    assert p.xp_to_next(4) == 800


def test_level_from_xp():
    assert p.level_from_xp(0) == 1
    assert p.level_from_xp(99) == 1
    assert p.level_from_xp(100) == 2
    assert p.level_from_xp(100 + 283) == 3


def test_unlocks_and_slots():
    assert p.slot_limit(1) == 3
    assert p.slot_limit(3) == 5
    assert p.slot_limit(10) == 8
    assert p.slot_limit(10, is_premium=True) == 10
    assert not p.can_use_type(1, "main")
    assert p.can_use_type(3, "main")
    assert p.unlocks_for_level(5).get("daily_events") == "true"
    new = p.new_unlocks_between(2, 5)
    assert {u["feature_key"] for u in new} >= {"mission_type:main", "daily_events"}


@pytest.mark.parametrize("days,mult", [(0, 1.0), (2, 1.0), (3, 1.1), (7, 1.25), (29, 1.25), (30, 1.5)])
def test_streak_multiplier(days, mult):
    assert p.streak_multiplier(days) == mult


def test_next_streak():
    today = date(2026, 11, 23)
    assert p.next_streak(0, None, today) == 1
    assert p.next_streak(4, today - timedelta(days=1), today) == 5
    assert p.next_streak(4, today - timedelta(days=2), today) == 1
    assert p.next_streak(4, today, today) == 4


def test_calculate_xp():
    # side 50 × 1.1 streak × 1.5 timed = 82.5 → 82, + 10 proof + 20 new cell
    b = p.calculate_xp("side", 3, in_time=True, proof_attached=True, first_visit_to_cell=True)
    assert b.total == round(50 * 1.1 * 1.5) + 10 + 20
    assert p.calculate_xp("home", 0, False, False, False).total == 15


def test_slacker_stars():
    assert p.slacker_stars_after_day(0, True, False) == 1
    assert p.slacker_stars_after_day(5, True, False) == 5
    assert p.slacker_stars_after_day(3, True, True) == 0
    assert p.slacker_stars_after_day(2, False, False) == 2


# ---------- geo / Google Maps ----------
def test_waypoint_url():
    assert waypoint_url(12.97, 77.59, "walking") == (
        "https://www.google.com/maps/dir/?api=1&destination=12.97,77.59&travelmode=walking"
    )
    assert waypoint_url(1, 2, "rocket").endswith("travelmode=driving")


def test_haversine():
    assert 1100 < haversine_m(12.9716, 77.5946, 12.9816, 77.5946) < 1120


def test_extract_coords_prefers_place_over_camera():
    url = "https://www.google.com/maps/place/Cubbon+Park/@12.9700,77.5900,15z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d12.9763!4d77.5929"
    assert extract_coords(url) == (12.9763, 77.5929)
    assert extract_place_name(url) == "Cubbon Park"


def test_extract_coords_variants():
    assert extract_coords("https://www.google.com/maps/@9.4981,76.3388,14z") == (9.4981, 76.3388)
    assert extract_coords("https://maps.google.com/?q=9.49,76.33") == (9.49, 76.33)
    assert extract_coords("https://www.google.com/maps/search/?api=1&query=12.9%2C77.5") == (12.9, 77.5)
    assert extract_coords("https://www.google.com/maps/@200.0,10.0,14z") is None
    assert extract_coords("https://www.google.com/maps/place/Somewhere") is None


def test_host_allow_list():
    assert host_allowed("https://maps.app.goo.gl/abc")
    assert is_short_link("https://maps.app.goo.gl/abc")
    assert not host_allowed("http://169.254.169.254/latest/meta-data")
    assert not host_allowed("https://evil.example.com/maps/@1,2")
    assert not host_allowed("file:///etc/passwd")


# ---------- anti-cheat ----------
def test_impossible_travel():
    t0 = datetime(2026, 11, 23, 10, tzinfo=timezone.utc)
    prev = PrevPoint(12.97, 77.59, t0)
    # Bengaluru → Alappuzha (~450 km) in 10 minutes
    assert impossible_travel(prev, 9.49, 76.33, t0 + timedelta(minutes=10))
    # 1 km in 10 minutes is fine
    assert not impossible_travel(prev, 12.979, 77.59, t0 + timedelta(minutes=10))
    assert not impossible_travel(None, 1, 1, t0)


def test_suspicious_accuracy():
    t = datetime.now(timezone.utc)
    assert suspicious_accuracy(0, [], 1, 1)
    recent = [PrevPoint(1.5, 2.5, t), PrevPoint(1.5, 2.5, t)]
    assert suspicious_accuracy(10, recent, 1.5, 2.5)
    assert suspicious_accuracy(10, recent, 1.6, 2.5) is None


# ---------- fix 8: deployment safety ----------
def test_production_requires_strong_secret():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(environment="production", jwt_secret="short")
    with pytest.raises(ValidationError):
        Settings(environment="production")  # default dev secret
    assert Settings(environment="production", jwt_secret="x" * 40).jwt_secret == "x" * 40
    assert Settings(environment="development").environment == "development"


def test_rate_limit_key_is_per_user():
    from starlette.requests import Request

    from app.limiter import user_or_ip
    from app.security import create_access_token

    def req(headers):
        return Request({"type": "http", "headers": headers, "client": ("10.0.0.1", 1234)})

    token = create_access_token(42)
    assert user_or_ip(req([(b"authorization", f"Bearer {token}".encode())])) == "user:42"
    assert user_or_ip(req([])) == "ip:10.0.0.1"
    assert user_or_ip(req([(b"authorization", b"Bearer garbage")])) == "ip:10.0.0.1"


# ---------- fix 6: explore point sanitising ----------
def test_sanitize_points():
    from app.services.fog import ExplorePoint, sanitize_points

    now = datetime(2026, 11, 23, 12, tzinfo=timezone.utc)
    pts = [
        ExplorePoint(12.97, 77.59, now - timedelta(minutes=4)),
        ExplorePoint(12.975, 77.59, now - timedelta(minutes=2)),  # 550 m in 2 min: ok
        ExplorePoint(28.61, 77.20, now - timedelta(minutes=1)),  # Delhi: impossible
        ExplorePoint(12.97, 77.59, now - timedelta(hours=3)),  # too old
        ExplorePoint(12.97, 77.59, now + timedelta(hours=1)),  # future
    ]
    kept = sanitize_points(pts, None, now)
    assert [(p.lat, p.lng) for p in kept] == [(12.97, 77.59), (12.975, 77.59)]
    # A previous point far away makes the batch's first point impossible
    last = ExplorePoint(9.49, 76.33, now - timedelta(minutes=10))
    assert sanitize_points(pts[:1], last, now) == []


# ---------- geometry without PostGIS ----------
def test_bbox_around_contains_circle():
    from app.services.geo import bbox_around, haversine_m

    min_lat, max_lat, min_lng, max_lng = bbox_around(12.97, 77.59, 2000)
    assert haversine_m(12.97, 77.59, max_lat, 77.59) == pytest.approx(2000, rel=0.01)
    assert haversine_m(12.97, 77.59, 12.97, max_lng) == pytest.approx(2000, rel=0.01)
    assert min_lat < 12.97 < max_lat and min_lng < 77.59 < max_lng


def test_parse_geometry():
    from app.services.areas import AreaError, bbox_columns, parse_geometry

    sq = {"type": "Polygon", "coordinates": [[[77.5, 12.9], [77.6, 12.9], [77.6, 13.0], [77.5, 13.0], [77.5, 12.9]]]}
    assert bbox_columns(parse_geometry(sq, polygon_only=True)) == {"min_lng": 77.5, "min_lat": 12.9, "max_lng": 77.6, "max_lat": 13.0}
    with pytest.raises(AreaError):
        parse_geometry({"type": "LineString", "coordinates": [[77.5, 12.9], [77.6, 13.0]]}, polygon_only=True)
    with pytest.raises(AreaError):
        parse_geometry({"type": "Point", "coordinates": [500, 12.9]})
    with pytest.raises(AreaError):
        parse_geometry({"type": "Nope"})
