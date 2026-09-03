"""Settings — 12-factor, read from environment (backend/.env locally).

Model ids do NOT live here: they belong in app/llm/config.py (ADR-0009, gate G-04).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # App connects as the non-superuser app role (obliance_app) — RLS applies (ADR-0002).
    database_url: str = Field(
        default="postgresql+psycopg://obliance_app:obliance_app@127.0.0.1:5432/obliance"
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")


settings = Settings()
