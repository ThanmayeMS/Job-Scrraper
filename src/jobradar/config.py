"""Application configuration — all settings are read from the environment / .env.

Nothing sensitive is ever hard-coded. `get_settings()` is cached so the .env
file is parsed once per process.
"""

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # App
    app_name: str = "JobRadar"
    environment: str = "development"
    debug: bool = True
    cors_origins: str = "*"  # comma-separated origins, or "*" for all

    # Database
    database_url: str = "postgresql+psycopg://jobradar:jobradar@localhost:5432/jobradar"

    # Redis / Celery
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # Auth
    jwt_secret_key: str = "change-me-please-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24

    # OpenAI / LLM gateway
    openai_api_key: str = ""
    openai_base_url: str | None = None
    # Portkey (OpenAI-compatible gateway). If portkey_api_key is set it takes priority.
    portkey_api_key: str = ""
    portkey_virtual_key: str = ""
    scoring_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # Matching
    cosine_threshold: float = 0.55
    max_llm_scores_per_run: int = 150

    # Scraper
    selenium_headless: bool = True

    # Background tasks: None = auto (inline unless ENVIRONMENT=production).
    # Set true to run scraping/matching inside the web process (no Redis/worker needed).
    run_tasks_inline: bool | None = None

    @field_validator("database_url", mode="before")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        # Managed hosts (Render/Heroku/etc.) hand out postgres:// URLs — coerce to psycopg.
        if isinstance(v, str) and "://" in v:
            scheme, rest = v.split("://", 1)
            if scheme in ("postgres", "postgresql"):
                return "postgresql+psycopg://" + rest
        return v

    @property
    def tasks_eager(self) -> bool:
        if self.run_tasks_inline is not None:
            return self.run_tasks_inline
        return self.environment.lower() != "production"

    @property
    def broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
