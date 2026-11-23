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
| Database | Plain PostgreSQL 14+ (no extensions) — Shapely for polygons, h3 for fog of war |
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
      checkin.py       Geofence check-in: accuracy, distance (haversine), dwell, proof, co-op, friend verify
      progression.py   XP formula, levels (100 × L^1.5), unlock table, streaks, slacker stars
      rewards.py       Completion: XP, cash, stats, streak, story chapters, district claims, sponsor billing
      anticheat.py     Impossible travel, suspicious accuracy, rate limit, flag → XP freeze
      maps_link.py     Google Maps link import (full + short links, SSRF allow-list)
      fog.py           H3 res-9 fog of war
      geo.py           Haversine distance, bounding boxes, Google Maps links
      areas.py         Districts and restricted zones: GeoJSON in JSONB + Shapely point-in-polygon
      farming.py       Anti-farming rules (distance, wait, daily caps, cooldowns)
      daily.py         Daily random events from OpenStreetMap POIs (Overpass)
      jobs.py          Nightly tick: expire missions, streak reset, slacker stars
      storage.py       Proof photos: EXIF stripped, S3 signed URLs
  scripts/setup_db.py  One-time local DB setup: user + database (--reset to recreate)
  tests/               Rule unit tests + end-to-end API tests (real PostgreSQL)
frontend/
  src/app/
    core/              API client, auth (JWT + rotating refresh), GPS/location service, models
    pages/             map, mission form, missions, stories, social (crew), profile (stats/shop)
    shared/            HUD, mission sheet, place picker, "Mission passed" overlay
docker-compose.yml     PostgreSQL + API
```

## Local setup (no Docker)

You need:

- **PostgreSQL 14+** — a plain install is enough, no extensions (PostGIS is **not** needed)
- **Python 3.12+**
- **Node.js 20+** (22 recommended)

### 1. Check PostgreSQL is running

You should know the password of the `postgres` admin user (set when you installed PostgreSQL).
On Windows it runs as the service *postgresql-x64-16*; on macOS `brew services start postgresql@16`; on Linux `sudo systemctl start postgresql`.

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

It's safe to run again. Options: `--admin-user`, `--host`, `--port` (default `postgres@localhost:5432`), and `--reset` to drop and recreate the database (deletes its data).

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
| `The database was created by an older version of the app` | Run `python scripts/setup_db.py --reset` (recreates the DB; dev data is lost) |
| `Can't reach PostgreSQL` | Start the PostgreSQL service; check the port in `.env` |
| `password authentication failed` | Run the setup script again, or fix `DATABASE_URL` in `backend/.env` |
| Pulled a change and get column / table errors | No migrations yet: `python scripts/setup_db.py --reset` |
| Map shows no location | Geolocation needs `localhost` or HTTPS, and the browser must be allowed to use location |
| Testing on your phone | Run `npx ng serve --host 0.0.0.0 --ssl` and open `https://<your-laptop-ip>:4200` (accept the certificate warning) |

## Run with Docker (alternative)

```bash
docker compose up --build        # PostgreSQL + API on http://localhost:8000
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

## How location works without PostGIS

| Need | How |
|---|---|
| Distance, geofence check-in | Haversine formula in Python (error well under 1 m at these distances) |
| Missions near me / daily-event places | Indexed `lat`/`lng` bounding-box filter in SQL, then exact haversine distance and sort |
| Districts, restricted zones | GeoJSON stored in a `JSONB` column with its bounding box; SQL prefilters by box, Shapely tests point-in-polygon |
| Fog of war | H3 hexagon IDs (`h3` library) stored as text |

Coordinates are WGS84 (`lat`, `lng`); GeoJSON uses `[lng, lat]` order. If you later move to a host with PostGIS, these can switch back to spatial columns without changing the API.

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
- All check-in timing uses the **server clock**; phone timestamps more than 60 s off are rejected
- Impossible travel (> 250 km/h between accepted points), 0 m accuracy or repeated identical coordinates are flagged; 3 flags in a week freeze XP until an admin reviews
- Manual "I'm here" (weak GPS) needs a **photo**, must be within 300 m and still runs dwell time; a text note never counts as proof
- Rate limits on auth, check-in (30/hour), link import and explore — per user when signed in, per IP otherwise; SSRF allow-list for Google redirects
- Account deletion removes all location rows and photos (DPDP Act 2023)

**Anti-farming (missions you make yourself)**

| Rule | Default (`backend/app/config.py`, overridable via env) |
|---|---|
| New missions must be away from where you are | 200 m (`MIN_MISSION_DISTANCE_M`) — checked against the phone's position and your last check-in |
| Wait before check-in opens (after create, accept, move, or chapter unlock) | 30 min (`MIN_MINUTES_BEFORE_CHECKIN`) |
| Daily XP cap from your own missions | 500 XP (`DAILY_SELF_XP_CAP`) |
| Home missions that earn XP per day | 3 (`DAILY_HOME_MISSIONS`) |
| No XP for another own mission in the same hex | 24 h (`SAME_CELL_COOLDOWN_H`) |
| Story chapters | max 10, places at least 200 m apart |
| Fog of war | max 300 new hexes a day; points must be recent, accurate (≤ 100 m) and reachable at a realistic speed |

Capped completions still complete the mission (and keep your streak) but give no XP or stats. Daily events and sponsored missions aren't capped.

**Moderation and billing**
- One report per player per public mission; reports from 3 different players hide it until an admin re-approves. Sponsored missions stay visible and go to admin review instead
- A public or sponsored mission can be taken once per player; the sponsor is billed once per player
- Missions sent to friends or squads go through the same validation (no story/chapter/verifier links, no past deadlines)

**Deployment safety**
- `ENVIRONMENT=production` refuses to start unless `JWT_SECRET` is a random string of 32+ characters
- The nightly job (expire missions, streaks, slacker stars) records each day in `job_runs`, so restarts or several API instances never run it twice
- Behind a host's proxy, run uvicorn with `--proxy-headers` and set `FORWARDED_ALLOW_IPS` to the proxy (the Docker image does this); with more than one API instance set `RATE_LIMIT_STORAGE_URI=redis://...`

## Not done yet

- Capacitor Android/iOS wrapper (needed for `isFromMockProvider` mock-location checks and push notifications)
- Payments for Premium and sponsor billing (counts are tracked; no payment provider is wired up)
- Alembic migrations (after pulling a change that adds tables or columns, run `python scripts/setup_db.py --reset`)
- Seeding districts from real ward boundaries and restricted zones from OSM (admin endpoints accept GeoJSON)
- PWA icons are the Angular defaults

## Deploy (free tiers)

Backend on Render / Fly.io / Cloud Run (Dockerfile in `backend/`), database on any PostgreSQL (Neon, Supabase, RDS…), frontend on Vercel / Netlify (`npx ng build`, output in `frontend/dist/frontend/browser`). Serve the API under the same origin at `/api` or change `frontend/src/app/core/api.ts`.
