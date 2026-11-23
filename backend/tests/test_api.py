"""End-to-end API tests against a real PostgreSQL + PostGIS database.

Run with: DATABASE_URL=postgresql+asyncpg://... pytest tests/test_api.py
Skipped automatically if the database isn't reachable.
"""

import io
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import text

from app.db import SessionLocal, engine, init_db
from app.limiter import limiter
from app.main import app, seed_unlocks
from app.services import jobs

pytestmark = pytest.mark.asyncio(loop_scope="module")

HOME = (12.9716, 77.5946)  # Bengaluru
CAFE = (12.9750, 77.6050)  # ~1.2 km away
PARK = (12.9800, 77.5900)  # ~1 km away
API = "/api/v1"


@pytest.fixture(scope="module")
async def client():
    try:
        await init_db()
        await seed_unlocks()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"database not available: {exc}")
    limiter.enabled = False  # many registrations from one test client
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    limiter.enabled = True
    await engine.dispose()


# ---------------- helpers ----------------
async def register(client, name=None):
    name = name or f"u{uuid.uuid4().hex[:8]}"
    r = await client.post(f"{API}/auth/register", json={"username": name, "email": f"{name}@x.com", "password": "secret123"})
    assert r.status_code == 201, r.text
    return name, {"Authorization": f"Bearer {r.json()['access_token']}"}, r.json()["refresh_token"]


async def sql(statement: str, **params):
    async with engine.begin() as conn:
        return await conn.execute(text(statement), params)


async def user_id(username: str) -> int:
    return (await sql("SELECT id FROM users WHERE username = :u", u=username)).scalar()


async def set_level(username: str, xp: int):
    from app.services.progression import level_from_xp

    await sql("UPDATE users SET xp = :xp, level = :lvl WHERE username = :u", xp=xp, lvl=level_from_xp(xp), u=username)


async def open_checkin(mission_id: int):
    """Skip the anti-farming wait by backdating the mission's activation."""
    await sql(
        "UPDATE missions SET activated_at = now() - interval '2 hours', created_at = now() - interval '2 hours' WHERE id = :id",
        id=mission_id,
    )


async def create(client, h, **body):
    body.setdefault("title", "mission")
    r = await client.post(f"{API}/missions", headers=h, json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def checkin(client, h, mid, lat=None, lng=None, acc=10, **extra):
    body = {"lat": lat, "lng": lng, "accuracy_m": acc, **extra} if lat is not None else extra
    return (await client.post(f"{API}/missions/{mid}/checkin", headers=h, json=body)).json()


def jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="JPEG")
    return buf.getvalue()


# ---------------- core loop ----------------
async def test_core_loop(client):
    _, h, refresh = await register(client)
    me = (await client.get(f"{API}/me", headers=h)).json()
    assert me["level"] == 1 and me["slots_total"] == 3

    m = await create(client, h, title="Try the new biryani place", lat=CAFE[0], lng=CAFE[1], origin_lat=HOME[0], origin_lng=HOME[1])
    assert "travelmode=driving" in m["waypoint_url"] and m["checkin_opens_at"]

    near = (await client.get(f"{API}/missions?lat={HOME[0]}&lng={HOME[1]}&radius=5000", headers=h)).json()
    assert near[0]["id"] == m["id"] and near[0]["distance_m"] > 1000

    # Fix 2: check-in doesn't open right after creating
    r = await checkin(client, h, m["id"], CAFE[0], CAFE[1])
    assert r["status"] == "rejected" and "opens in" in r["reason"]
    await open_checkin(m["id"])

    r = await checkin(client, h, m["id"], HOME[0], HOME[1])
    assert r["status"] == "rejected"  # too far
    r = await checkin(client, h, m["id"], CAFE[0], CAFE[1], acc=120)
    assert "GPS too weak" in r["reason"]

    r = await checkin(client, h, m["id"], CAFE[0] + 0.0002, CAFE[1], acc=12, timestamp=datetime.now(timezone.utc).isoformat())
    assert r["status"] == "completed", r
    assert r["xp_awarded"] == 50 + 20 and r["streak_days"] == 1

    me = (await client.get(f"{API}/me", headers=h)).json()
    assert me["xp"] == 70 and me["completed_missions"] == 1

    r = await client.post(f"{API}/explore", headers=h, json={"points": [{"lat": HOME[0], "lng": HOME[1]}]})
    assert r.json()["new_cells"] == 1
    fc = (await client.get(f"{API}/map/explored?bbox=77.58,12.96,77.62,12.99", headers=h)).json()
    assert len(fc["features"]) == 2

    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": refresh})).status_code == 200
    assert (await client.post(f"{API}/auth/refresh", json={"refresh_token": refresh})).status_code == 401


# ---------------- fix 1: phone clock ----------------
async def test_phone_timestamp_cannot_fake_speed(client):
    _, h, _ = await register(client)
    a = await create(client, h, title="A", lat=CAFE[0], lng=CAFE[1])
    far = (13.3409, 77.1010)  # ~65 km away
    b = await create(client, h, title="B", lat=far[0], lng=far[1])
    await open_checkin(a["id"])
    await open_checkin(b["id"])
    assert (await checkin(client, h, a["id"], CAFE[0], CAFE[1]))["status"] == "completed"

    # A timestamp a day ahead is rejected outright
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    r = await checkin(client, h, b["id"], far[0], far[1], timestamp=tomorrow)
    assert r["status"] == "rejected" and "clock" in r["reason"]

    # Without a timestamp, speed uses the server clock → 65 km in seconds is impossible
    r = await checkin(client, h, b["id"], far[0], far[1])
    assert r["status"] == "rejected" and r["reason"] == "Location check failed"

    # A slightly-off phone clock (30 s) is fine
    c = await create(client, h, title="C", lat=PARK[0], lng=PARK[1])
    await open_checkin(c["id"])
    await sql("UPDATE checkin_attempts SET created_at = now() - interval '1 hour' WHERE mission_id = :id", id=a["id"])
    skew = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert (await checkin(client, h, c["id"], PARK[0], PARK[1], timestamp=skew))["status"] == "completed"


# ---------------- fix 2: farming ----------------
async def test_cannot_create_mission_at_your_feet(client):
    _, h, _ = await register(client)
    r = await client.post(f"{API}/missions", headers=h, json={"title": "here", "lat": HOME[0], "lng": HOME[1], "origin_lat": HOME[0] + 0.0005, "origin_lng": HOME[1]})
    assert r.status_code == 422 and "200 m" in r.json()["detail"]


async def test_story_chapters_must_be_spread_out(client):
    name, h, _ = await register(client)
    await set_level(name, 500)  # level 3
    same = [{"title": f"ch{i}", "lat": CAFE[0], "lng": CAFE[1]} for i in range(3)]
    r = await client.post(f"{API}/stories", headers=h, json={"title": "Farm", "chapters": same})
    assert r.status_code == 422 and "apart" in r.json()["detail"]
    many = [{"title": f"ch{i}"} for i in range(11)]
    assert (await client.post(f"{API}/stories", headers=h, json={"title": "Long", "chapters": many})).status_code == 422


async def test_daily_caps_and_same_cell_cooldown(client):
    _, h, _ = await register(client)
    # Home missions: only 3 a day earn XP
    xps = []
    for i in range(3):
        m = await create(client, h, title=f"read {i}", type="home", category="habit")
        await open_checkin(m["id"])
        xps.append((await checkin(client, h, m["id"]))["xp_awarded"])
    m = await create(client, h, title="read 4", type="home", category="habit")
    await open_checkin(m["id"])
    r = await checkin(client, h, m["id"])
    assert xps == [15, 15, 15] and r["status"] == "completed" and r["xp_awarded"] == 0 and "home missions" in r["reason"]

    # Same hex within 24 h: no XP the second time
    a = await create(client, h, title="cafe 1", lat=CAFE[0], lng=CAFE[1])
    await open_checkin(a["id"])
    assert (await checkin(client, h, a["id"], CAFE[0], CAFE[1]))["xp_awarded"] > 0
    # Standing at the café, a new mission there is refused...
    r = await client.post(f"{API}/missions", headers=h, json={"title": "cafe 2", "lat": CAFE[0] + 0.0003, "lng": CAFE[1]})
    assert r.status_code == 422
    # ...created later from elsewhere it's allowed, but earns no XP in the same hex within 24 h
    await sql("UPDATE checkin_attempts SET created_at = now() - interval '3 hours' WHERE mission_id = :id", id=a["id"])
    b = await create(client, h, title="cafe 2", lat=CAFE[0] + 0.0003, lng=CAFE[1])
    await open_checkin(b["id"])
    r = await checkin(client, h, b["id"], CAFE[0] + 0.0003, CAFE[1])
    assert r["status"] == "completed" and r["xp_awarded"] == 0 and "already earned XP here" in r["reason"]


async def test_daily_self_xp_cap(client):
    name, h, _ = await register(client)
    uid = await user_id(name)
    # Pretend 480 XP was already earned today from own missions
    seed = await create(client, h, title="seed", type="home")
    await sql(
        "INSERT INTO completions (mission_id, user_id, xp_awarded, completed_at) VALUES (:m, :u, 480, now())",
        m=seed["id"], u=uid,
    )
    m = await create(client, h, title="park", lat=PARK[0], lng=PARK[1])
    await open_checkin(m["id"])
    r = await checkin(client, h, m["id"], PARK[0], PARK[1])
    assert r["xp_awarded"] == 20 and "cap" in r["reason"]


# ---------------- fix 3: manual check-in and proof ----------------
async def test_manual_checkin_keeps_dwell_and_note_is_not_proof(client):
    _, h, _ = await register(client)
    m = await create(client, h, title="Gym 30 min", category="fitness", lat=PARK[0], lng=PARK[1], dwell_min=30, proof_required=True)
    await open_checkin(m["id"])

    # A note alone is not a photo proof
    r = await client.post(f"{API}/missions/{m['id']}/proof", headers=h, data={"note": "I was there"})
    assert r.status_code == 200 and r.json()["has_proof"] is False and r.json()["has_note"] is True
    r = await checkin(client, h, m["id"], PARK[0], PARK[1], acc=200, manual=True)
    assert r["status"] == "rejected" and "photo" in r["reason"]

    await client.post(f"{API}/missions/{m['id']}/proof", headers=h, files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
    # Manual check-in 500 m away is refused (limit 300 m)
    r = await checkin(client, h, m["id"], PARK[0] + 0.0045, PARK[1], acc=200, manual=True)
    assert r["status"] == "rejected"
    # Close by: dwell still applies
    r = await checkin(client, h, m["id"], PARK[0] + 0.001, PARK[1], acc=200, manual=True)
    assert r["status"] == "in_progress" and r["dwell_remaining_min"] == 30


async def test_home_photo_proof(client):
    _, h, _ = await register(client)
    m = await create(client, h, title="Read 20 pages", type="home", category="habit", proof_required=True)
    await open_checkin(m["id"])
    assert (await checkin(client, h, m["id"]))["status"] == "rejected"
    await client.post(f"{API}/missions/{m['id']}/proof", headers=h, files={"file": ("p.jpg", jpeg_bytes(), "image/jpeg")})
    r = await checkin(client, h, m["id"])
    assert r["status"] == "completed" and r["xp_awarded"] == 15 + 10


# ---------------- fix 4: nightly job runs once per day ----------------
async def test_nightly_job_is_idempotent(client):
    name, h, _ = await register(client)
    await create(client, h, title="pending", type="home")
    day = date(2000, 1, 1) + timedelta(days=uuid.uuid4().int % 9000)  # a day no other test uses
    await sql("UPDATE users SET created_at = '1999-01-01', slacker_stars = 0 WHERE username = :u", u=name)
    assert await jobs.run_daily_tick(day) is True
    assert await jobs.run_daily_tick(day) is False  # restart / second instance: no-op
    stars = (await sql("SELECT slacker_stars FROM users WHERE username = :u", u=name)).scalar()
    assert stars == 1


# ---------------- fix 5: explored area size ----------------
async def test_explored_rejects_huge_area(client):
    _, h, _ = await register(client)
    r = await client.get(f"{API}/map/explored?bbox=-180,-85,180,85", headers=h)
    assert r.status_code == 422
    r = await client.get(f"{API}/map/explored?bbox=77.5,12.9,77.9,13.3", headers=h)
    assert r.status_code == 200


# ---------------- fix 6: reports, take, explore ----------------
async def test_reports_are_one_per_user_and_sponsored_stay_visible(client):
    admin, ha, _ = await register(client)
    await sql("UPDATE users SET is_admin = true WHERE username = :u", u=admin)
    sid = (await client.post(f"{API}/admin/sponsors", headers=ha, json={"business_name": "Cafe X", "contact": "x@x.com"})).json()["id"]
    sm = (await client.post(f"{API}/admin/sponsors/{sid}/missions", headers=ha, json={"title": "Free coffee", "lat": CAFE[0], "lng": CAFE[1]})).json()

    _, h1, _ = await register(client)
    assert (await client.post(f"{API}/missions/{sm['id']}/report", headers=h1, json={"reason": "spam spam"})).status_code == 202
    assert (await client.post(f"{API}/missions/{sm['id']}/report", headers=h1, json={"reason": "again"})).status_code == 409
    for _ in range(2):
        _, hx, _ = await register(client)
        await client.post(f"{API}/missions/{sm['id']}/report", headers=hx, json={"reason": "spam spam"})
    near = (await client.get(f"{API}/missions?lat={CAFE[0]}&lng={CAFE[1]}", headers=h1)).json()
    assert any(m["id"] == sm["id"] for m in near)  # sponsored: still visible
    review = (await client.get(f"{API}/admin/review", headers=ha)).json()
    assert any(m["id"] == sm["id"] for m in review)  # but sent to admin review

    # Take once; sponsor billed once
    _, hp, _ = await register(client)
    copy = (await client.post(f"{API}/missions/{sm['id']}/take", headers=hp)).json()
    assert (await client.post(f"{API}/missions/{sm['id']}/take", headers=hp)).status_code == 409
    assert (await checkin(client, hp, copy["id"], CAFE[0], CAFE[1]))["status"] == "completed"
    sponsors = (await client.get(f"{API}/admin/sponsors", headers=ha)).json()
    assert next(s for s in sponsors if s["id"] == sid)["billable_checkins"] == 1


async def test_explore_drops_fake_points_and_caps(client):
    _, h, _ = await register(client)
    now = datetime.now(timezone.utc)
    pts = [
        {"lat": HOME[0], "lng": HOME[1], "timestamp": now.isoformat()},
        {"lat": 28.61, "lng": 77.20, "timestamp": (now + timedelta(seconds=5)).isoformat()},  # Delhi 5 s later
        {"lat": HOME[0], "lng": HOME[1], "timestamp": (now - timedelta(days=2)).isoformat()},  # stale
        {"lat": HOME[0] + 0.01, "lng": HOME[1], "accuracy_m": 500},  # inaccurate
    ]
    r = (await client.post(f"{API}/explore", headers=h, json={"points": pts})).json()
    assert r["new_cells"] == 1


# ---------------- fix 7: assigned missions are validated ----------------
async def test_friend_and_squad_missions_are_validated(client):
    a, ha, _ = await register(client)
    b, hb, _ = await register(client)
    await set_level(a, 20000)
    await set_level(b, 20000)
    await client.post(f"{API}/friends", headers=ha, json={"username": b})
    await client.post(f"{API}/friends", headers=hb, json={"username": a})
    fid = await user_id(b)

    # Someone else's story can't be attached
    story = (await client.post(f"{API}/stories", headers=hb, json={"title": "B's story", "chapters": [{"title": "1"}, {"title": "2"}]})).json()
    r = await client.post(
        f"{API}/friends/{fid}/missions", headers=ha,
        json={"title": "sneaky", "lat": CAFE[0], "lng": CAFE[1], "story_id": story["id"], "chapter_no": 1},
    )
    assert r.status_code == 201 and r.json()["story_id"] is None and r.json()["chapter_no"] is None

    # Past deadlines are refused
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    r = await client.post(f"{API}/friends/{fid}/missions", headers=ha, json={"title": "late", "type": "timed", "lat": CAFE[0], "lng": CAFE[1], "deadline": past})
    assert r.status_code == 422
    sq = (await client.post(f"{API}/squads", headers=ha, json={"name": "Crew", "member_usernames": [b]})).json()
    r = await client.post(f"{API}/squads/{sq['id']}/missions", headers=ha, json={"title": "trek", "lat": PARK[0], "lng": PARK[1], "deadline": past})
    assert r.status_code == 422


async def test_dwell_and_timed(client):
    _, h, _ = await register(client)
    deadline = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    m = await create(client, h, title="Gym 30 min", type="timed", category="fitness", lat=HOME[0], lng=HOME[1], dwell_min=30, deadline=deadline)
    await open_checkin(m["id"])
    r = await checkin(client, h, m["id"], HOME[0], HOME[1], acc=8)
    assert r["status"] == "in_progress" and r["dwell_remaining_min"] == 30


async def test_import_link(client):
    _, h, _ = await register(client)
    r = await client.post(
        f"{API}/missions/import-link", headers=h,
        json={"url": "https://www.google.com/maps/place/Cubbon+Park/@12.97,77.59,15z/data=!3d12.9763!4d77.5929"},
    )
    assert r.json() == {"lat": 12.9763, "lng": 77.5929, "place_name": "Cubbon Park", "needs_manual_pin": False}
    assert (await client.post(f"{API}/missions/import-link", headers=h, json={"url": "http://169.254.169.254/"})).status_code == 422


async def test_slots(client):
    _, h, _ = await register(client)
    for i in range(3):
        await create(client, h, title=f"t{i}", type="home")
    assert (await client.post(f"{API}/missions", headers=h, json={"title": "too many", "type": "home"})).status_code == 409
    r = await client.post(f"{API}/stories", headers=h, json={"title": "Get fit", "chapters": [{"title": "Gym"}]})
    assert r.status_code == 403  # main story locked at level 1
