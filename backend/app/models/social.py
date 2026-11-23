from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Friendship(Base):
    __tablename__ = "friendships"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    friend_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(10), default="pending")  # pending / accepted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Squad(Base):
    __tablename__ = "squads"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SquadMember(Base):
    __tablename__ = "squad_members"

    squad_id: Mapped[int] = mapped_column(ForeignKey("squads.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), default="member")  # leader / member


class CoopCheckin(Base):
    """Per-member check-ins for squad co-op missions."""

    __tablename__ = "coop_checkins"

    mission_id: Mapped[int] = mapped_column(ForeignKey("missions.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    checked_in_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Sponsor(Base):
    __tablename__ = "sponsors"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_name: Mapped[str] = mapped_column(String(120))
    contact: Mapped[str] = mapped_column(String(255))
    billing_status: Mapped[str] = mapped_column(String(16), default="trial")
    billable_checkins: Mapped[int] = mapped_column(Integer, default=0)
