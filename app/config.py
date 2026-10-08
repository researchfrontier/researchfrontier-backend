from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """App configuration. Every field can be overridden by an env var prefixed
    with ``RF_`` (e.g. ``RF_DATABASE_URL``) or an entry in a local ``.env``."""

    model_config = SettingsConfigDict(env_prefix="RF_", env_file=".env", extra="ignore")

    # --- Database ---
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/researchfrontier"
    )

    # --- CORS (comma-separated list of exact origins) ---
    cors_origins: str = "http://localhost:5173,https://researchfrontier.github.io"

    # --- External data sources ---
    openalex_base: str = "https://api.openalex.org"
    # Free key (recommended since Feb 2026) raises the daily credit budget 10x.
    openalex_api_key: str | None = None
    # Polite-pool contact for OpenAlex/Crossref etiquette. Set to YOUR email only.
    contact_email: str | None = None

    # --- Demo behaviour ---
    # When true, "last N days" windows are measured from the most recent paper in
    # the DB (so the bundled seed always shows content). Set false in production
    # where live ingestion keeps dates current.
    demo_relative_dates: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
