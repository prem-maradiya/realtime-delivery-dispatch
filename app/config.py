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

    # --- Auth ---
    # Override JWT_SECRET and ADMIN_PASSWORD in any real deployment.
    jwt_secret: str = "dev-only-change-me-0123456789abcdef"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60
    admin_username: str = "admin"
    admin_password: str = "admin12345"
    # PBKDF2 work factor (OWASP 2023 recommends 600k for SHA-256). Tests lower it for speed.
    password_hash_iterations: int = 600_000

    # Background job that retries PENDING orders.
    enable_sweeper: bool = True
    sweeper_interval_s: float = 5.0


settings = Settings()
