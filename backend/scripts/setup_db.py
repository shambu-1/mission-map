"""One-time local database setup for Mission Map (no Docker).

Creates the `missionmap` user and database on your local PostgreSQL and enables PostGIS.

Usage (from the backend folder, with the virtualenv active):
    python scripts/setup_db.py
    python scripts/setup_db.py --admin-user postgres --host localhost --port 5432

You'll be asked for the password of your PostgreSQL admin user (the one you set when
installing PostgreSQL). Safe to run more than once.
"""

import argparse
import asyncio
import getpass
import os
import sys

import asyncpg

APP_USER = "missionmap"
APP_PASSWORD = "missionmap"
APP_DB = "missionmap"

POSTGIS_HELP = """
PostGIS is not installed for this PostgreSQL server. Install it, then run this script again:

  Windows : open "Application Stack Builder" (installed with PostgreSQL) →
            pick your PostgreSQL 16 server → Spatial Extensions → PostGIS 3.x → install.
  macOS   : brew install postgis        (Postgres.app already includes PostGIS)
  Ubuntu  : sudo apt install postgresql-16-postgis-3   (match your PostgreSQL version)
"""


async def main(args: argparse.Namespace) -> int:
    password = args.admin_password or os.environ.get("PGPASSWORD") or getpass.getpass(
        f"Password for PostgreSQL user '{args.admin_user}': "
    )
    common = {"host": args.host, "port": args.port, "user": args.admin_user, "password": password}

    try:
        admin = await asyncpg.connect(database="postgres", **common)
    except (OSError, asyncpg.PostgresError) as exc:
        print(f"✗ Could not connect to PostgreSQL at {args.host}:{args.port} as '{args.admin_user}': {exc}")
        print("  Check that PostgreSQL is running and the password is right.")
        return 1

    try:
        if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_USER):
            await admin.execute(f"CREATE ROLE {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}'")
            print(f"✓ Created user '{APP_USER}'")
        else:
            print(f"• User '{APP_USER}' already exists")

        if not await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", APP_DB):
            await admin.execute(f"CREATE DATABASE {APP_DB} OWNER {APP_USER}")
            print(f"✓ Created database '{APP_DB}'")
        else:
            print(f"• Database '{APP_DB}' already exists")

        available = await admin.fetchval("SELECT 1 FROM pg_available_extensions WHERE name = 'postgis'")
    finally:
        await admin.close()

    if not available:
        print(POSTGIS_HELP)
        return 1

    # Enable PostGIS inside the app database (needs the admin/superuser).
    db = await asyncpg.connect(database=APP_DB, **common)
    try:
        await db.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        version = await db.fetchval("SELECT postgis_version()")
        await db.execute(f"GRANT ALL ON SCHEMA public TO {APP_USER}")
    finally:
        await db.close()
    print(f"✓ PostGIS enabled ({version.split()[0]})")
    print("\nAll set. Start the API with:  uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--admin-password", default=None, help="or set PGPASSWORD, or type it when asked")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    sys.exit(asyncio.run(main(parser.parse_args())))
