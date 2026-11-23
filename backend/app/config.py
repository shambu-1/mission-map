import logging
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "dev-only-secret-change-me-in-production-please"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    """App settings, read from environment variables or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Mission Map API"
    # "development" or "production". Production refuses to start with an unsafe JWT secret.
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://missionmap:missionmap@localhost:5432/missionmap"
    cors_origins: list[str] = ["http://localhost:4200"]

    # Auth (doc: access 15 min, refresh 30 days)
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    refresh_token_days: int = 30

    # Check-in rules
    default_radius_m: int = 100
    min_radius_m: int = 50
    max_radius_m: int = 500
    max_gps_accuracy_m: float = 75.0
    accuracy_bonus_cap_m: float = 30.0
    max_travel_speed_kmh: float = 250.0
    checkin_attempts_per_hour: int = 30
    flags_per_week_to_freeze: int = 3
    max_clock_skew_s: int = 60  # phone timestamps further than this from server time are rejected

    # Anti-farming
    min_minutes_before_checkin: int = 30  # wait between creating/activating a mission and checking in
    min_mission_distance_m: int = 200  # new missions must be this far from where you are
    daily_self_xp_cap: int = 500  # XP per day from missions you made yourself
    daily_home_missions: int = 3  # home missions that earn XP per day
    same_cell_cooldown_h: int = 24  # no XP for another self-made mission in the same hex within this window
    max_story_chapters: int = 10

    # Explore (fog of war) limits
    explore_new_cells_per_day: int = 300
    explore_max_point_age_min: int = 30
    max_explored_bbox_deg: float = 0.5  # ~55 km; larger map areas are rejected

    # Rate limiting. Use e.g. "redis://localhost:6379" when running more than one API instance.
    rate_limit_storage_uri: str = "memory://"

    # Fog of war
    h3_resolution: int = 9

    # Daily events
    daily_radius_m: int = 2000
    overpass_url: str = "https://overpass-api.de/api/interpreter"

    # Proof photo storage. If s3_bucket is empty, files are stored on local disk.
    s3_bucket: str = ""
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    local_upload_dir: str = "uploads"
    max_upload_mb: int = 5

    # Safety
    driving_lock_kmh: float = 20.0


    @model_validator(mode="after")
    def _check_secret(self):
        unsafe = self.jwt_secret == DEV_JWT_SECRET or len(self.jwt_secret) < MIN_SECRET_LENGTH
        if unsafe and self.environment.lower() == "production":
            raise ValueError(
                f"JWT_SECRET must be set to a random string of at least {MIN_SECRET_LENGTH} characters in production"
            )
        if unsafe:
            logging.getLogger(__name__).warning("Using a development JWT secret — set JWT_SECRET before deploying")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
