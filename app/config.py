"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # Google API
    google_places_api_key: str

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/places_db"
    database_url_sync: str = "postgresql://postgres:postgres@localhost:5432/places_db"

    # Rate Limiting
    max_requests_per_second: int = 5
    pagination_delay_seconds: float = 2.0

    # Enrichment
    enrichment_timeout: int = 10
    enrichment_max_retries: int = 3
    respect_robots_txt: bool = True

    # Server
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False
    log_level: str = "INFO"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


@lru_cache()
def get_settings() -> Settings:
    return Settings()
