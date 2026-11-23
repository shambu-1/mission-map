from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App settings, read from environment variables or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Mission Map API"
    database_url: str = "postgresql+asyncpg://missionmap:missionmap@localhost:5432/missionmap"
    cors_origins: list[str] = ["http://localhost:4200"]

    # Auth (doc: access 15 min, refresh 30 days)
    jwt_secret: str = "dev-only-secret-change-me-in-production-please"
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
