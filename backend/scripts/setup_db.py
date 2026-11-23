"""One-time local database setup for Mission Map (no Docker, no extensions).

Creates the `missionmap` user and database on your local PostgreSQL. Works with a plain
PostgreSQL install — PostGIS is NOT needed.

Usage (from the backend folder, with the virtualenv active):
    python scripts/setup_db.py
    python scripts/setup_db.py --admin-user postgres --host localhost --port 5432
    python scripts/setup_db.py --reset      # drop and recreate the database (deletes its data)

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
        version = await admin.fetchval("SHOW server_version")
        print(f"• PostgreSQL {version.split()[0]}")

        if not await admin.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_USER):
            await admin.execute(f"CREATE ROLE {APP_USER} LOGIN PASSWORD '{APP_PASSWORD}'")
            print(f"✓ Created user '{APP_USER}'")
        else:
            print(f"• User '{APP_USER}' already exists")

        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", APP_DB)
        if exists and args.reset:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = $1 AND pid <> pg_backend_pid()",
                APP_DB,
            )
            await admin.execute(f"DROP DATABASE {APP_DB}")
            print(f"✓ Dropped database '{APP_DB}'")
            exists = False
        if not exists:
            await admin.execute(f"CREATE DATABASE {APP_DB} OWNER {APP_USER}")
            print(f"✓ Created database '{APP_DB}'")
        else:
            print(f"• Database '{APP_DB}' already exists (use --reset to recreate it)")
    finally:
        await admin.close()

    print("\nAll set. Start the API with:  uvicorn app.main:app --reload")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--admin-password", default=None, help="or set PGPASSWORD, or type it when asked")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--reset", action="store_true", help="drop and recreate the database (deletes all its data)")
    sys.exit(asyncio.run(main(parser.parse_args())))
