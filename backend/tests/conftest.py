"""Pytest fixtures — real Postgres, two roles.

RLS cannot be tested on SQLite and is silently void for superusers/owners that
bypass it, so the suite uses:

  MIGRATE_DATABASE_URL  obliance_migrator (schema owner) — resets schema, runs alembic,
                        truncates between tests (TRUNCATE is not subject to RLS)
  TEST_DATABASE_URL     obliance_app (non-superuser, not owner) — what every test
                        query runs as, so FORCE ROW LEVEL SECURITY applies

Local: docker compose -f infra/compose.yml up -d postgres  (roles + test DB are
created by infra/postgres/init/01-roles.sql on first init).
"""

from __future__ import annotations

import os
import tempfile
import uuid
from collections.abc import Callable, Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

MIGRATE_URL = os.environ.setdefault(
    "MIGRATE_DATABASE_URL",
    "postgresql+psycopg://obliance_migrator:obliance_migrator@127.0.0.1:5432/obliance_test",
)
TEST_URL = os.environ.setdefault(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://obliance_app:obliance_app@127.0.0.1:5432/obliance_test",
)


def _guard_test_database(url: str, name: str) -> None:
    """The suite DROPS SCHEMA and TRUNCATEs. It must never point at a database whose
    name does not end in `_test` — a sourced backend/.env once leaked the dev URL in
    here and wiped the development data (2026-09-04)."""
    db = url.rsplit("/", 1)[-1].split("?")[0]
    if not db.endswith("_test"):
        raise SystemExit(
            f"{name} points at database '{db}', which is not a *_test database. "
            "Refusing to run: the suite drops and truncates everything it touches."
        )


_guard_test_database(MIGRATE_URL, "MIGRATE_DATABASE_URL")
_guard_test_database(TEST_URL, "TEST_DATABASE_URL")
os.environ["DATABASE_URL"] = TEST_URL
os.environ.setdefault("APP_ENV", "test")
# Auth (ADR-0024): a fixed test secret, and a login limit generous enough that the
# suite never trips it; the dedicated rate-limit test tightens it at runtime.
os.environ.setdefault("SECRET_KEY", "test-secret-not-for-anything-else-0123456789")
os.environ.setdefault("RATELIMIT_LOGIN", "1000 per minute")
# Documents (ADR-0006): uploads land in a throwaway directory, never in the repo.
os.environ.setdefault("STORAGE_ROOT", tempfile.mkdtemp(prefix="obliance-storage-"))
# AI (ADR-0008): no provider is ever called from the suite; tests script a FakeProvider.
os.environ.setdefault("LLM_BACKEND", "fake")

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def migrator_engine() -> Engine:
    return create_engine(MIGRATE_URL, future=True, isolation_level="AUTOCOMMIT")


@pytest.fixture(scope="session")
def migrated_schema(migrator_engine: Engine) -> None:
    """Fresh schema + all migrations, once per session, as the owner role.
    Not autouse: unit tests must run without a database."""
    with migrator_engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # obliance_app / obliance_worker need USAGE again after the schema is recreated; the
        # migration re-grants table privileges.
        conn.execute(text("GRANT USAGE ON SCHEMA public TO obliance_app, obliance_worker"))
    _run_migrations()


@pytest.fixture(scope="session")
def app_engine(migrated_schema: None) -> Engine:
    # Import after env vars are set so app.core.config reads the test URL, and
    # so app.core.db registers the RLS listener on Session. Depending on
    # migrated_schema guarantees migrations ran before any DB test.
    from app.core.db import engine

    return engine


@pytest.fixture
def Session_(
    app_engine: Engine, migrator_engine: Engine
) -> Generator[sessionmaker[Session], None, None]:
    """Session factory bound to the app role; truncates all data tables after the
    test (TRUNCATE is not subject to RLS — runs as the owner). Only DB tests
    request this fixture, so unit tests never touch Postgres."""
    yield sessionmaker(bind=app_engine, expire_on_commit=False, class_=Session)
    with migrator_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname='public' "
                "AND tablename NOT IN ('alembic_version', 'role_permissions')"  # seeded reference data
            )
        ).fetchall()
        tables = ", ".join(f'"{r[0]}"' for r in rows)
        if tables:
            conn.execute(text(f"TRUNCATE {tables} CASCADE"))


# ---- seeding helpers (identity tables have no RLS; contracts are seeded in
# system context, which is the legitimate all-seeing context — ADR-0002) --------


@pytest.fixture
def make_org(Session_: sessionmaker[Session]) -> Callable[[str], uuid.UUID]:
    def _make(name: str) -> uuid.UUID:
        from app.domain.models import Organization

        with Session_() as s:
            org = Organization(
                name=name, slug=name.lower().replace(" ", "-") + "-" + uuid.uuid4().hex[:6]
            )
            s.add(org)
            s.commit()
            return org.id

    return _make


@pytest.fixture
def client(app_engine: Engine) -> Generator[TestClient, None, None]:
    """HTTP client over the real app. Depends on app_engine so migrations ran."""
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def make_user(Session_: sessionmaker[Session]) -> Callable[..., uuid.UUID]:
    def _make(
        org_id: uuid.UUID, email: str, role: str, *, password: str | None = None
    ) -> uuid.UUID:
        from app.core.security import hash_password
        from app.domain.models import MemberRole, OrganizationMember, Profile

        with Session_() as s:
            p = Profile(
                email=email,
                name=email.split("@")[0],
                password_hash=hash_password(password) if password else None,
            )
            s.add(p)
            s.flush()
            s.add(
                OrganizationMember(organization_id=org_id, profile_id=p.id, role=MemberRole(role))
            )
            s.commit()
            return p.id

    return _make


@pytest.fixture
def make_contract(
    Session_: sessionmaker[Session],
) -> Callable[..., uuid.UUID]:
    def _make(
        org_id: uuid.UUID,
        reference: str,
        *,
        confidentiality: str = "intern",
        owner_id: uuid.UUID | None = None,
        manager_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        from app.core.rls import tenant
        from app.domain.models import Confidentiality, Contract

        with tenant(org_id, system=True), Session_() as s:
            c = Contract(
                organization_id=org_id,
                reference=reference,
                name=f"Kontrakt {reference}",
                confidentiality=Confidentiality(confidentiality),
                owner_id=owner_id,
                manager_id=manager_id,
            )
            s.add(c)
            s.commit()
            return c.id

    return _make
