"""Settings — 12-factor, read from environment (backend/.env locally).

Model ids do NOT live here: they belong in app/llm/config.py (ADR-0009, gate G-04).
"""

from __future__ import annotations

from decimal import Decimal
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

    # ---- AI layer (ADR-0008 backend + residency, ADR-0014 pricing, ADR-0010 budget) ----
    # `anthropic` (default, ZDR + inference_geo pinned) · `vertex_eu` (EU inference,
    # Google as processor) · `fake` (tests/dev without a key — refused outside dev/test).
    llm_backend: Literal["anthropic", "vertex_eu", "fake"] = "anthropic"
    anthropic_api_key: str | None = None
    llm_inference_geo: str = "us"  # ADR-0008 afklaring 2: a documented answer to "where?"
    vertex_project_id: str | None = None
    vertex_region: str = "europe-west1"
    llm_timeout_seconds: float = 180
    dkk_per_usd: Decimal = Decimal("6.90")  # stored on every usage row (ADR-0014 §2)
    llm_daily_budget_dkk: Decimal = Decimal("500")  # per org, hard stop (ADR-0010 §7)

    # Background jobs: sync (test) · thread (dev, no Redis) · rq (staging/prod).
    jobs_mode: Literal["sync", "thread", "rq"] | None = None
    job_timeout_seconds: int = 5400  # bidflow ADR-0026: 600 was too short for a real night
    # Scheduler (ADR-0010 §1/§5): cadences are read in this zone; a nightly org run
    # handles at most this many contracts and continues from a cursor the next night.
    scheduler_timezone: str = "Europe/Copenhagen"
    agent_contracts_per_run: int = 500

    @property
    def jobs_mode_effective(self) -> str:
        if self.jobs_mode is not None:
            return self.jobs_mode
        return {"test": "sync", "dev": "thread"}.get(self.app_env, "rq")

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
        if self.app_env in ("staging", "prod") and self.llm_backend == "fake":
            raise ValueError("LLM_BACKEND=fake is dev/test-only (ADR-0008)")
        return self


settings = Settings()
