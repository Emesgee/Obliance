"""Domain events — the smallest thing that lets ADR-0006's version switch have
listeners (ADR-0004 expire_suggestions, ADR-0005 re-resolution, re-extraction).

Two ways to emit:

    emit(name, **payload)                  now, in-process, best-effort
    emit_after_commit(session, name, ...)  queued on the Session; delivered by an
                                           after_commit hook — so a listener that
                                           opens its own transaction (a job) sees
                                           the rows the event is about

ADR-0006 §3 asks for a persisted outbox so a worker crash cannot lose a switch;
that replaces the after_commit delivery when the worker exists (ADR-0010) —
subscribers keep the same signature.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

Handler = Callable[..., None]
_subscribers: dict[str, list[Handler]] = defaultdict(list)

DOCUMENT_VERSION_CHANGED = "document_version_changed"

_PENDING = "pending_events"


def subscribe(name: str, handler: Handler) -> None:
    _subscribers[name].append(handler)


def emit(name: str, **payload: Any) -> None:
    """Best-effort fan-out: a failing subscriber is logged, never propagates —
    the primary write (bidflow ADR-0054) has already committed."""
    for h in list(_subscribers.get(name, [])):
        try:
            h(**payload)
        except Exception:
            log.exception("event %s: subscriber %s failed", name, getattr(h, "__name__", h))


def emit_after_commit(session: Session, name: str, **payload: Any) -> None:
    pending: list[tuple[str, dict[str, Any]]] = session.info.setdefault(_PENDING, [])
    pending.append((name, payload))


@event.listens_for(Session, "after_commit")
def _deliver(session: Session) -> None:
    pending: list[tuple[str, dict[str, Any]]] = session.info.pop(_PENDING, [])
    for name, payload in pending:
        emit(name, **payload)


@event.listens_for(Session, "after_rollback")
def _discard(session: Session) -> None:
    session.info.pop(_PENDING, None)


def clear() -> None:
    """Tests only."""
    _subscribers.clear()
