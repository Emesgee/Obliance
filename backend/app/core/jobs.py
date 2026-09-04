"""Background jobs — the seam ADR-0010's worker plugs into.

    enqueue(fn, *args, **kwargs)

    sync    run now, in the caller's thread          (test — assertions see the result)
    thread  run in a daemon thread                   (dev on a PC without Redis)
    rq      enqueue on Redis for the worker container (staging/prod)

The mode is derived from APP_ENV (settings.jobs_mode_effective) so an inline mode
cannot leak into production the way bidflow's JOBS_SYNC once did (ADR-0026).
Jobs must be self-contained: they open their own Session in a system tenant
context (app.core.rls.tenant(..., system=True)) and record their own outcome —
an exception here is logged, never raised into the caller.

RQ jobs are addressed by import path, so `fn` must be a module-level function.
Retry(max=2, interval=[60, 120]) covers transient failures before the run row
exists (a DB blip at start); once a run row exists the job never raises, and a
third failure is a `fejlet` row rather than a fourth attempt (ADR-0010 §4).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from app.core.config import settings

log = logging.getLogger(__name__)

QUEUE = "agents"


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
        from redis import Redis
        from rq import Queue, Retry

        q = Queue(QUEUE, connection=Redis.from_url(settings.redis_url))
        q.enqueue(
            fn,
            *args,
            retry=Retry(max=2, interval=[60, 120]),
            job_timeout=settings.job_timeout_seconds,
            result_ttl=24 * 3600,
            failure_ttl=7 * 24 * 3600,
            **kwargs,
        )
