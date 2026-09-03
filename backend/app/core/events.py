"""Domain events — the smallest thing that lets ADR-0006's version switch have
listeners (ADR-0004 expire_suggestions, ADR-0005 re-resolution, re-extraction).

In-process and synchronous for now. ADR-0006 §3 asks for a persisted outbox so a
worker crash cannot lose a switch; that replaces `emit` when the worker exists
(ADR-0010) — subscribers keep the same signature.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

Handler = Callable[..., None]
_subscribers: dict[str, list[Handler]] = defaultdict(list)

DOCUMENT_VERSION_CHANGED = "document_version_changed"


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


def clear() -> None:
    """Tests only."""
    _subscribers.clear()
