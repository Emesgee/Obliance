"""Background jobs — the seam ADR-0010's worker plugs into.

    enqueue(fn, *args, **kwargs)

    sync    run now, in the caller's thread          (test — assertions see the result)
    thread  run in a daemon thread                   (dev on a PC without Redis)
    rq      enqueue on Redis for the worker container (staging/prod — not wired yet)

The mode is derived from APP_ENV (settings.jobs_mode_effective) so an inline mode
cannot leak into production the way bidflow's JOBS_SYNC once did (ADR-0026).
Jobs must be self-contained: they open their own Session in a system tenant
context (app.core.rls.tenant(..., system=True)) and record their own outcome —
an exception here is logged, never raised into the caller.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)


def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    try:
        fn(*args, **kwargs)
    except Exception:
        log.exception("job %s failed", getattr(fn, "__name__", fn))


def enqueue(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
    mode = settings.jobs_mode_effective
    if mode == "sync":
        _run(fn, *args, **kwargs)
    elif mode == "thread":
        threading.Thread(target=_run, args=(fn, *args), kwargs=kwargs, daemon=True).start()
    else:
        # ADR-0010: RQ queue + scheduler in the worker container. Until it exists a
        # prod deploy must fail loudly here rather than silently run nothing.
        raise NotImplementedError("JOBS_MODE=rq: worker queue not wired yet (ADR-0010)")
