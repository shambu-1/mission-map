from datetime import date, datetime

from geoalchemy2 import Geography
from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ExploredCell(Base):
    """Drives fog of war. PK (user_id, h3_index)."""

    __tablename__ = "explored_cells"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    h3_index: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class District(Base):
    """City zones, seeded from ward boundaries or custom polygons."""

    __tablename__ = "districts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    boundary = mapped_column(Geography("POLYGON", srid=4326, spatial_index=True))


class DistrictClaim(Base):
    __tablename__ = "district_claims"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    district_id: Mapped[int] = mapped_column(ForeignKey("districts.id", ondelete="CASCADE"), primary_key=True)
    missions_done: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Unlock(Base):
    """Config table for the level → unlock table."""

    __tablename__ = "unlocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[int] = mapped_column(Integer, index=True)
    feature_key: Mapped[str] = mapped_column(String(48))
    value: Mapped[str] = mapped_column(String(64), default="true")
    label: Mapped[str] = mapped_column(String(120), default="")


class RestrictedZone(Base):
    """OSM-derived exclusion layer: railway tracks, military areas, water bodies."""

    __tablename__ = "restricted_zones"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    area = mapped_column(Geography("GEOMETRY", srid=4326, spatial_index=True))


class Poi(Base):
    """Cached OpenStreetMap points of interest used by the daily mission generator."""

    __tablename__ = "pois"

    id: Mapped[int] = mapped_column(primary_key=True)
    osm_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32))  # park / library / lake / viewpoint / ...
    location = mapped_column(Geography("POINT", srid=4326, spatial_index=True))


class JobRun(Base):
    """One row per (job, day). Inserting the row claims the run, so restarts and
    multiple API instances never run the nightly job twice for the same day."""

    __tablename__ = "job_runs"

    job: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_date: Mapped[date] = mapped_column(Date, primary_key=True)
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
