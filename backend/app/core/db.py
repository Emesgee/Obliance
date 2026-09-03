"""Engine + session factory. The RLS listener is registered here, once, so every
session created anywhere in the app applies the tenant GUCs (app.core.rls)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.rls import register_rls

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)

register_rls()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """One unit of work. Commit on success, rollback on error, always close."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency. Callers must already be inside a tenant context
    (app.core.rls.tenant) — a session outside one sees no customer rows."""
    with session_scope() as s:
        yield s
