# Mission Map

Turn your real city into a GTA-style game map: pin tasks (missions) to real places, go there, check in with GPS, earn XP and unlock more of the game.

Design doc: [Mission Map — Technical Design Document](https://claude.ai/artifact/Dj4L6CVRvf8FSivRZ48Q67)

**Core loop:** add a mission → travel ("Set waypoint" opens Google Maps) → GPS check-in inside the geofence → XP, cash, streak → level up and unlock more.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Angular 20 (standalone components, signals) as a PWA |
| Map | MapLibre GL JS with OpenStreetMap data (OpenFreeMap vector styles, OSM raster fallback) |
| UI | Tailwind CSS 4 + daisyUI 5 (custom dark HUD theme) |
| Backend | Python 3.12, FastAPI, SQLAlchemy 2 (async), Pydantic 2 |
| Database | PostgreSQL 16 + PostGIS, h3 for fog of war |
| Storage | S3-compatible bucket for proof photos (local disk in dev) |

All game rules (check-in, XP, unlocks, anti-cheat) run on the server.

## Project layout

```
backend/
  app/
    main.py            FastAPI app, routers, startup (tables + unlock seed + nightly job)
    config.py          Settings (env vars / .env)
    models/            users, missions, stories, completions, checkin_attempts, explored_cells,
                       districts, district_claims, unlocks, friendships, squads, sponsors, ...
    routers/           auth, me, missions, stories, world (daily/explore/map/districts), social, admin
    services/
      checkin.py       Geofence check-in: accuracy, distance (PostGIS), dwell, proof, co-op, friend verify
      progression.py   XP formula, levels (100 × L^1.5), unlock table, streaks, slacker stars
      rewards.py       Completion: XP, cash, stats, streak, story chapters, district claims, sponsor billing
      anticheat.py     Impossible travel, suspicious accuracy, rate limit, flag → XP freeze
      maps_link.py     Google Maps link import (full + short links, SSRF allow-list)
      fog.py           H3 res-9 fog of war
      daily.py         Daily random events from OpenStreetMap POIs (Overpass)
      jobs.py          Nightly tick: expire missions, streak reset, slacker stars
      storage.py       Proof photos: EXIF stripped, S3 signed URLs
  scripts/setup_db.py  One-time local DB setup: user, database, PostGIS
  tests/               Rule unit tests + end-to-end API tests (real PostGIS)
frontend/
  src/app/
    core/              API client, auth (JWT + rotating refresh), GPS/location service, models
    pages/             map, mission form, missions, stories, social (crew), profile (stats/shop)
    shared/            HUD, mission sheet, place picker, "Mission passed" overlay
docker-compose.yml     PostGIS + API
```

## Local setup (no Docker)

You need:

- **PostgreSQL 14+** with the **PostGIS** extension (spatial queries)
- **Python 3.12+**
- **Node.js 20+** (22 recommended)

### 1. Make sure PostGIS is installed

PostGIS is an add-on for PostgreSQL and is often not installed with it.

| OS | How |
|---|---|
| Windows | Open **Application Stack Builder** (installed with PostgreSQL) → choose your server → *Spatial Extensions* → **PostGIS 3.x** → install |
| macOS | `brew install postgis` (Postgres.app already includes it) |
| Ubuntu/Debian | `sudo apt install postgresql-16-postgis-3` (match your PostgreSQL version) |

The setup script in step 3 tells you if PostGIS is still missing.

### 2. Clone and install the backend

```bash
git clone https://github.com/shambu-1/mission-map.git
cd mission-map/backend

python -m venv .venv
# Windows (PowerShell):  .venv\Scripts\Activate.ps1
# macOS / Linux:         source .venv/bin/activate

pip install -r requirements.txt
```

### 3. Create the database (one time)

```bash
python scripts/setup_db.py
```

It asks for the password of your `postgres` admin user (the one you chose when installing PostgreSQL), then:

- creates the user `missionmap` (password `missionmap`)
- creates the database `missionmap`
- enables PostGIS in it

It's safe to run again. Options: `--admin-user`, `--host`, `--port` (default `postgres@localhost:5432`).

### 4. Configure (optional)

The defaults already point at `postgresql+asyncpg://missionmap:missionmap@localhost:5432/missionmap`, so this works with no config. To change anything (port, password, JWT secret, S3 for photos):

```bash
cp .env.example .env        # Windows: copy .env.example .env
```

### 5. Start the API

```bash
uvicorn app.main:app --reload
```

- API: http://localhost:8000 — interactive docs at http://localhost:8000/docs
- Tables are created automatically on first start.

### 6. Start the frontend (new terminal)

```bash
cd mission-map/frontend
npm install
npm start
```

Open **http://localhost:4200**, create a player and allow location access. Requests to `/api` are proxied to the API on port 8000 (`proxy.conf.json`).

### 7. Run the tests (optional)

```bash
cd backend
pytest -q
```

The API tests use the same `missionmap` database.

### Troubleshooting

| Problem | Fix |
|---|---|
| `PostGIS isn't enabled in the database` on API start | Install PostGIS (step 1), then run `python scripts/setup_db.py` again |
| `Can't reach PostgreSQL` | Start the PostgreSQL service; check the port in `.env` |
| `password authentication failed` | Run the setup script again, or fix `DATABASE_URL` in `backend/.env` |
| Changed a model and get column errors | No migrations yet: drop and recreate the DB — `DROP DATABASE missionmap;` as `postgres`, then `python scripts/setup_db.py` |
| Map shows no location | Geolocation needs `localhost` or HTTPS, and the browser must be allowed to use location |
| Testing on your phone | Run `npx ng serve --host 0.0.0.0 --ssl` and open `https://<your-laptop-ip>:4200` (accept the certificate warning) |

## Run with Docker (alternative)

```bash
docker compose up --build        # PostGIS + API on http://localhost:8000
cd frontend && npm install && npm start
```

## Becoming an admin

Admin endpoints (`/api/v1/admin/...`) review public missions, unfreeze flagged players, add sponsors, sponsored missions, districts and restricted zones.

```sql
UPDATE users SET is_admin = true WHERE username = 'your_username';
```

Add a district (GeoJSON polygon, for example a ward boundary):

```bash
curl -X POST localhost:8000/api/v1/admin/districts -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"name":"Shivajinagar","geometry":{"type":"Polygon","coordinates":[[[77.58,12.96],[77.61,12.96],[77.61,12.99],[77.58,12.99],[77.58,12.96]]]}}'
```

Restricted zones (railway tracks, military areas, water bodies) use `/admin/restricted-zones` with `kind` and any GeoJSON geometry. Missions can't be placed inside them.

## What's implemented (by design-doc phase)

**Phase 1 — MVP (single player)**
- Register / login, JWT (access 15 min, refresh 30 days, rotated on every refresh), Argon2 passwords
- Map with your live location, mission markers coloured by type (main gold, side blue, daily green, timed red), pulsing ring inside the geofence, geofence circle for the selected mission
- Create / edit / abandon missions: tap the map, search (OpenStreetMap Nominatim), or paste a Google Maps link (full or `maps.app.goo.gl` short link)
- "Set waypoint" → `https://www.google.com/maps/dir/?api=1&destination={lat},{lng}&travelmode=...` using your travel-mode setting
- Geofence check-in (radius 50–500 m, accuracy ≤ 75 m, accuracy bonus up to 30 m), mission states
- XP, levels, unlock table (slots 3 → 5 → 8), profile and stats screen, mini-map HUD (nearest 3 missions)

**Phase 2 — Game depth**
- Main story missions with chapters (chapter N+1 unlocks after N)
- Timed missions (+50% XP before the deadline), home missions (self-confirmed)
- Dwell time (stay N minutes), photo/note proof (EXIF stripped, max 5 MB), manual "I'm here" with proof when GPS is weak
- Daily random events within 2 km from OpenStreetMap POIs, unexplored cells and weakest stat first, expire at midnight IST
- Streaks (×1.1 / ×1.25 / ×1.5), slacker level (wanted stars in reverse), in-game cash
- Fog of war on an H3 grid (resolution 9), personal heatmap, "Mission passed" screen with sound

**Phase 3 — Social**
- Friends (username or invite link), send missions to friends' inbox (accept/decline, slot-limited)
- Squads (2–6) and co-op missions that complete when every member checks in before the deadline
- Friend-verified missions (friend confirms within 24 h)
- Friends-only weekly XP leaderboard; friends see completed missions, never live location
- Districts: 10 missions in a zone claims it (badge + colour on the map)

**Phase 4 — Growth (backend ready, minimal UI)**
- Public missions (level 20+), admin review, reporting (3 reports hides a mission)
- Sponsored missions (labelled "Sponsored", billed per completed check-in)
- Shop: map skins (night, retro, neon), marker icons, extra slots; `is_premium` flag adds slots and all skins

**Safety, privacy, anti-cheat**
- Foreground-only location, batched every 2 min; no background tracking
- Map and check-in lock above 20 km/h; night-time warning for public missions (22:00–06:00)
- Impossible travel (> 250 km/h between accepted points), 0 m accuracy or repeated identical coordinates are flagged; 3 flags in a week freeze XP until an admin reviews
- Rate limits on auth, check-in (30/hour) and link import; SSRF allow-list for Google redirects
- Account deletion removes all location rows and photos (DPDP Act 2023)

## Not done yet

- Capacitor Android/iOS wrapper (needed for `isFromMockProvider` mock-location checks and push notifications)
- Payments for Premium and sponsor billing (counts are tracked; no payment provider is wired up)
- Alembic migrations, Redis-backed rate limiter for multiple API instances
- Seeding districts from real ward boundaries and restricted zones from OSM (admin endpoints accept GeoJSON)
- PWA icons are the Angular defaults

## Deploy (free tiers)

Backend on Render / Fly.io / Cloud Run (Dockerfile in `backend/`), database on Neon or Supabase (enable PostGIS), frontend on Vercel / Netlify (`npx ng build`, output in `frontend/dist/frontend/browser`). Serve the API under the same origin at `/api` or change `frontend/src/app/core/api.ts`.
