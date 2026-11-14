"""All settings come from environment variables (12-factor style)."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://dispatch:dispatch@localhost:5432/dispatch"
    redis_url: str = "redis://localhost:6379/0"

    # Dispatch searches outward in rings: 2 km first, then 5, 10, 25.
    search_radii_km: list[float] = [2, 5, 10, 25]
    # How many nearest candidates to try per ring before widening.
    candidates_per_ring: int = 10
    # A driver lock only lives this long, so a crashed worker can't block a driver forever.
    driver_lock_ttl_s: int = 10
    # If a driver hasn't sent a location ping within this window, treat them as gone.
    driver_heartbeat_ttl_s: int = 30
    # How often to persist a driver's live location from Redis into Postgres.
    location_persist_interval_s: float = 30.0

    # Background job that retries PENDING orders.
    enable_sweeper: bool = True
    sweeper_interval_s: float = 5.0


settings = Settings()
