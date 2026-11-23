from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))

    # Progression (level is derived from XP but cached here)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(Integer, default=0)
    cash: Mapped[int] = mapped_column(Integer, default=0)
    streak_days: Mapped[int] = mapped_column(Integer, default=0)
    last_completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    slacker_stars: Mapped[int] = mapped_column(Integer, default=0)

    # GTA-style stats, each fed by a mission category
    stat_stamina: Mapped[int] = mapped_column(Integer, default=0)
    stat_explorer: Mapped[int] = mapped_column(Integer, default=0)
    stat_social: Mapped[int] = mapped_column(Integer, default=0)
    stat_discipline: Mapped[int] = mapped_column(Integer, default=0)

    # Settings
    travel_mode: Mapped[str] = mapped_column(String(16), default="driving")
    map_skin: Mapped[str] = mapped_column(String(16), default="default")
    owned_skins: Mapped[str] = mapped_column(String(64), default="default")  # comma-separated
    marker_icon: Mapped[str] = mapped_column(String(16), default="classic")
    extra_slots: Mapped[int] = mapped_column(Integer, default=0)  # bought with in-game cash
    is_premium: Mapped[bool] = mapped_column(default=False)
    is_admin: Mapped[bool] = mapped_column(default=False)

    # Anti-cheat: XP frozen until reviewed
    xp_frozen: Mapped[bool] = mapped_column(default=False)

    # Refresh-token rotation: bump to invalidate all issued refresh tokens
    token_version: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
