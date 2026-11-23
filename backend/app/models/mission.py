from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# Enumerations kept as plain strings (validated in Pydantic schemas)
MISSION_TYPES = ("main", "side", "daily", "timed", "home")
MISSION_CATEGORIES = ("fitness", "explore", "social", "habit")
MISSION_STATUSES = ("draft", "active", "in_progress", "completed", "failed", "expired", "abandoned")
VISIBILITIES = ("private", "friends", "public")
SOURCES = ("self", "system", "friend", "squad", "sponsor")


class Story(Base):
    """Groups main-story chapters."""

    __tablename__ = "stories"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active / completed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    type: Mapped[str] = mapped_column(String(8), default="side")
    category: Mapped[str] = mapped_column(String(16), default="explore")

    # Home missions have no location. Plain lat/lng (WGS84) — indexed for bounding-box searches.
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    place_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    radius_m: Mapped[int] = mapped_column(Integer, default=100)
    dwell_min: Mapped[int] = mapped_column(Integer, default=0)
    proof_required: Mapped[bool] = mapped_column(Boolean, default=False)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)

    story_id: Mapped[int | None] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), nullable=True)
    chapter_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility: Mapped[str] = mapped_column(String(8), default="private")

    # Who assigned it (self / system / friend / squad / sponsor)
    source: Mapped[str] = mapped_column(String(8), default="self")
    assigned_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    squad_id: Mapped[int | None] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), nullable=True)
    sponsor_id: Mapped[int | None] = mapped_column(ForeignKey("sponsors.id", ondelete="SET NULL"), nullable=True)
    review_status: Mapped[str] = mapped_column(String(10), default="approved")  # public missions: pending/approved/rejected
    report_count: Mapped[int] = mapped_column(Integer, default=0)

    # When the mission became checkable (creation, inbox accept, or previous chapter done)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Copy of a public/sponsored mission → the original
    source_mission_id: Mapped[int | None] = mapped_column(
        ForeignKey("missions.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Dwell tracking
    dwell_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Proof uploaded before check-in. proof_key is a photo (storage key) — only a photo
    # satisfies proof_required and earns the proof bonus; proof_note is an optional text note.
    proof_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proof_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Friend-verified (co-op): friend must confirm within 24 h
    verifier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    verification_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


Index("missions_lat_lng_idx", Mission.lat, Mission.lng)


class Completion(Base):
    """One row per successful check-in."""

    __tablename__ = "completions"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    h3_index: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    proof_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    xp_awarded: Mapped[int] = mapped_column(Integer, default=0)
    verified_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class CheckinAttempt(Base):
    """Audit trail for anti-cheat."""

    __tablename__ = "checkin_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    accuracy_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed_mps: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[str] = mapped_column(String(16))  # accepted / in_progress / rejected / flagged
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class MissionReport(Base):
    """One report per user per public mission."""

    __tablename__ = "mission_reports"

    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
