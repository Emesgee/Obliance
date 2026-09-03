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

    # ---- documents (ADR-0005/0006, storage facade from bidflow ADR-0007) ---------
    # `local` = filesystem under storage_root (dev/test/CI). `s3` = Hetzner Object
    # Storage (ADR-0007 §3) — refused at startup until implemented.
    storage_backend: Literal["local", "s3"] = "local"
    storage_root: str = "./storage"
    max_upload_mb: int = 50
    # Ingest runs inline in dev/test (no Redis on the Shadow PC); in staging/prod it
    # is a worker job (ADR-0010). Like bidflow's JOBS_SYNC — but derived from the
    # environment, so it cannot leak into prod (bidflow ADR-0026's incident).
    ingest_sync: bool | None = None
    # LibreOffice for docx/xlsx → PDF (bidflow ADR-0049). Auto-located if unset.
    soffice_path: str | None = None
    # Gate G-04 has nothing to do with this: no model is involved in ingest.

    @property
    def ingest_runs_inline(self) -> bool:
        if self.ingest_sync is not None:
            return self.ingest_sync
        return self.app_env in ("dev", "test")

    @model_validator(mode="after")
    def _no_dev_secret_outside_dev(self) -> Settings:
        if self.app_env in ("staging", "prod") and self.secret_key == _DEV_SECRET:
            raise ValueError("SECRET_KEY must be set outside dev/test (ADR-0024)")
        if self.app_env in ("staging", "prod") and self.storage_backend == "local":
            raise ValueError("STORAGE_BACKEND=local is dev-only (ADR-0007 §3, bidflow 0083)")
        return self


settings = Settings()
