"""End-to-end API test against a real PostgreSQL + PostGIS database.

Run with: DATABASE_URL=postgresql+asyncpg://... pytest tests/test_api.py
Skipped automatically if the database isn't reachable.
"""

import io
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.db import engine, init_db
from app.main import app, seed_unlocks

pytestmark = pytest.mark.asyncio(loop_scope="module")

HOME = (12.9716, 77.5946)  # Bengaluru
CAFE = (12.9750, 77.6050)  # ~1.2 km away


@pytest.fixture(scope="module")
async def client():
    try:
        await init_db()
        await seed_unlocks()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database not available: {exc}")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    await engine.dispose()


async def register(client, name=None):
    name = name or f"u{uuid.uuid4().hex[:8]}"
    r = await client.post("/api/v1/auth/register", json={"username": name, "email": f"{name}@x.com", "password": "secret123"})
    assert r.status_code == 201, r.text
    return name, {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["refresh_token"]


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="JPEG")
    return buf.getvalue()


async def test_core_loop(client):
    _, h, refresh = await register(client)

    me = (await client.get("/api/v1/me", headers=h)).json()
    assert me["level"] == 1 and me["slots_total"] == 3

    # Create a side mission at the café
    r = await client.post(
        "/api/v1/missions",
        headers=h,
        json={"title": "Try the new biryani place", "lat": CAFE[0], "lng": CAFE[1], "category": "explore"},
    )
    assert r.status_code == 201, r.text
    m = r.json()
    assert "travelmode=driving" in m["waypoint_url"]

    # Missions near me
    near = (await client.get(f"/api/v1/missions?lat={HOME[0]}&lng={HOME[1]}&radius=5000", headers=h)).json()
    assert near[0]["id"] == m["id"] and near[0]["distance_m"] > 1000

    # Check in from home → rejected, too far
    r = await client.post(f"/api/v1/missions/{m['id']}/checkin", headers=h, json={"lat": HOME[0], "lng": HOME[1], "accuracy_m": 10})
    assert r.json()["status"] == "rejected"

    # Weak GPS → rejected
    r = await client.post(f"/api/v1/missions/{m['id']}/checkin", headers=h, json={"lat": CAFE[0], "lng": CAFE[1], "accuracy_m": 120})
    assert "GPS too weak" in r.json()["reason"]

    # At the café — the previous attempt was 1.2 km away seconds ago, so wait it out in the timestamp
    later = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    r = await client.post(
        f"/api/v1/missions/{m['id']}/checkin",
        headers=h,
        json={"lat": CAFE[0] + 0.0002, "lng": CAFE[1], "accuracy_m": 12, "timestamp": later},
    )
    body = r.json()
    assert body["status"] == "completed", body
    assert body["xp_awarded"] == 50 + 20  # base + first visit to cell
    assert body["level_up"] is False and body["streak_days"] == 1

    me = (await client.get("/api/v1/me", headers=h)).json()
    assert me["xp"] == 70 and me["stats"]["explorer"] >= 1 and me["completed_missions"] == 1

    # Fog of war
    r = await client.post("/api/v1/explore", headers=h, json={"points": [{"lat": HOME[0], "lng": HOME[1]}]})
    assert r.json()["new_cells"] == 1
    fc = (await client.get(f"/api/v1/map/explored?bbox=77.58,12.96,77.62,12.99", headers=h)).json()
    assert len(fc["features"]) == 2

    # Refresh token rotation
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 200
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401


async def test_home_proof_and_slots(client):
    _, h, _ = await register(client)
    r = await client.post("/api/v1/missions", headers=h, json={"title": "Read 20 pages", "type": "home", "category": "habit", "proof_required": True})
    m = r.json()
    r = await client.post(f"/api/v1/missions/{m['id']}/checkin", headers=h, json={})
    assert r.json()["status"] == "rejected"
    r = await client.post(
        f"/api/v1/missions/{m['id']}/proof", headers=h, files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")}, data={"note": "done"}
    )
    assert r.status_code == 200 and r.json()["has_proof"]
    r = await client.post(f"/api/v1/missions/{m['id']}/checkin", headers=h, json={})
    assert r.json()["status"] == "completed" and r.json()["xp_awarded"] == 15 + 10

    # Slot limit: 3 at level 1
    for i in range(3):
        assert (await client.post("/api/v1/missions", headers=h, json={"title": f"t{i}", "type": "home"})).status_code == 201
    assert (await client.post("/api/v1/missions", headers=h, json={"title": "too many", "type": "home"})).status_code == 409

    # Main story locked at level 1
    r = await client.post("/api/v1/stories", headers=h, json={"title": "Get fit", "chapters": [{"title": "Gym"}]})
    assert r.status_code == 403


async def test_dwell_and_timed(client):
    _, h, _ = await register(client)
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    r = await client.post(
        "/api/v1/missions",
        headers=h,
        json={"title": "Gym 30 min", "type": "timed", "category": "fitness", "lat": HOME[0], "lng": HOME[1], "dwell_min": 30, "deadline": deadline},
    )
    m = r.json()
    r = await client.post(f"/api/v1/missions/{m['id']}/checkin", headers=h, json={"lat": HOME[0], "lng": HOME[1], "accuracy_m": 8})
    assert r.json()["status"] == "in_progress" and r.json()["dwell_remaining_min"] == 30


async def test_friends_and_inbox(client):
    a, ha, _ = await register(client)
    b, hb, _ = await register(client)
    assert (await client.post("/api/v1/friends", headers=ha, json={"username": b})).json()["status"] == "pending"
    assert (await client.post("/api/v1/friends", headers=hb, json={"username": a})).json()["status"] == "accepted"
    friends = (await client.get("/api/v1/friends", headers=ha)).json()
    assert friends[0]["username"] == b and friends[0]["status"] == "accepted"
    # Sending missions unlocks at level 8
    fid = friends[0]["user_id"]
    r = await client.post(f"/api/v1/friends/{fid}/missions", headers=ha, json={"title": "Try this café", "lat": CAFE[0], "lng": CAFE[1]})
    assert r.status_code == 403
    board = (await client.get("/api/v1/leaderboard", headers=ha)).json()
    assert {row["username"] for row in board} == {a, b}


async def test_import_link(client):
    _, h, _ = await register(client)
    r = await client.post(
        "/api/v1/missions/import-link",
        headers=h,
        json={"url": "https://www.google.com/maps/place/Cubbon+Park/@12.97,77.59,15z/data=!3d12.9763!4d77.5929"},
    )
    assert r.json() == {"lat": 12.9763, "lng": 77.5929, "place_name": "Cubbon Park", "needs_manual_pin": False}
    r = await client.post("/api/v1/missions/import-link", headers=h, json={"url": "http://169.254.169.254/"})
    assert r.status_code == 422
