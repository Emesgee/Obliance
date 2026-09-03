"""Row-Level Security tenant context — ADR-0002, ported from bidflow ADR-0004.

Postgres enforces two levels of isolation with policies that read three
per-transaction GUCs:

    app.current_org_id   level 1: organization_id must match (tenant_isolation)
    app.current_user_id  level 2: fortrolig contracts need owner/manager/access
    app.current_role     level 2: 'auditor' reads everything in its org

This module bridges the app to those GUCs:

  * ``_ctx`` — a ContextVar holding the active TenantContext for the current
    request / job (thread- and async-safe).
  * an SQLAlchemy ``after_begin`` listener that, at the start of EVERY
    transaction, sets the GUCs transaction-locally (``set_config(..., true)``)
    from the ContextVar. Transaction-local means the values reset at
    commit/rollback — they can never leak onto a pooled connection reused by
    another tenant, and they are re-applied across the multiple commits a
    background job performs.
  * ``tenant(...)`` — a context manager that installs a TenantContext.

Guard (ADR-0002 §Systemkontekst): an empty user id means *system* and sees the
whole organization. That is only legitimate in worker processes, so
``tenant()`` refuses an empty user unless ``system=True`` is passed explicitly.
In a request, a missing user is a bug — it raises, it never falls back.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session, SessionTransaction


class RlsContextError(RuntimeError):
    """Raised when a request-scoped context is created without a user."""


@dataclass(frozen=True, slots=True)
class TenantContext:
    org_id: str
    user_id: str | None = None  # None → system context (worker only)
    role: str | None = None  # member_role value, e.g. 'auditor'

    @property
    def is_system(self) -> bool:
        return self.user_id is None


_ctx: ContextVar[TenantContext | None] = ContextVar("tenant_ctx", default=None)

_SET_GUCS = text(
    "SELECT set_config('app.current_org_id', :org, true), "
    "set_config('app.current_user_id', :user, true), "
    "set_config('app.current_role', :role, true)"
)


def current() -> TenantContext | None:
    return _ctx.get()


def _apply(conn: Connection, ctx: TenantContext) -> None:
    conn.execute(
        _SET_GUCS,
        {"org": ctx.org_id, "user": ctx.user_id or "", "role": ctx.role or ""},
    )


_installed = False


def register_rls() -> None:
    """Install the after_begin listener on all Sessions. Idempotent."""
    global _installed
    if _installed:
        return

    @event.listens_for(Session, "after_begin")
    def _on_begin(
        session: Session, transaction: SessionTransaction, connection: Connection
    ) -> None:
        ctx = _ctx.get()
        if ctx is not None:
            _apply(connection, ctx)

    _installed = True


@contextmanager
def tenant(
    org_id: Any,
    *,
    user_id: Any | None = None,
    role: str | None = None,
    system: bool = False,
    session: Session | None = None,
) -> Generator[TenantContext, None, None]:
    """Scope the enclosed block to one organization (and one user, or system).

    ``system=True`` is the only way to get an all-seeing context; it must not
    be reachable from a request handler. Pass ``session`` to also apply the
    GUCs to a transaction that is already open.
    """
    if user_id is None and not system:
        raise RlsContextError(
            "tenant context without user_id — pass user_id, or system=True in a worker"
        )
    ctx = TenantContext(
        org_id=str(org_id),
        user_id=None if system else str(user_id),
        role=role,
    )
    token = _ctx.set(ctx)
    try:
        if session is not None and session.in_transaction():
            _apply(session.connection(), ctx)
        yield ctx
    finally:
        _ctx.reset(token)
