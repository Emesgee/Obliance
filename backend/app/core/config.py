"""Settings — 12-factor, read from environment (backend/.env locally).

Model ids do NOT live here: they belong in app/llm/config.py (ADR-0009, gate G-04).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEV_SECRET = "dev-only-secret-change-me-in-prod-0123456789abcdef"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["dev", "test", "staging", "prod"] = "dev"
    log_level: str = "INFO"

    # App connects as the non-superuser app role (obliance_app) — RLS applies (ADR-0002).
    database_url: str = Field(
        default="postgresql+psycopg://obliance_app:obliance_app@127.0.0.1:5432/obliance"
    )
    redis_url: str = Field(default="redis://127.0.0.1:6379/0")

    # ---- auth (ADR-0024) -------------------------------------------------------
    # Signs stateless access tokens. Rotating it logs everyone out — intended.
    secret_key: str = _DEV_SECRET
    jwt_ttl_minutes: int = 8 * 60
    password_min_length: int = 12  # NIST / bidflow ADR-0006

    # Rate limiting (bidflow ADR-0009: `limits` directly). memory:// in dev/test,
    # redis://… in prod so limits hold across api replicas.
    ratelimit_enabled: bool = True
    ratelimit_storage_uri: str = "memory://"
    ratelimit_login: str = "10 per minute"

    @model_validator(mode="after")
    def _no_dev_secret_outside_dev(self) -> Settings:
        if self.app_env in ("staging", "prod") and self.secret_key == _DEV_SECRET:
            raise ValueError("SECRET_KEY must be set outside dev/test (ADR-0024)")
        return self


settings = Settings()
