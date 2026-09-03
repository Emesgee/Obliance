"""RQ worker entry point — `python -m app.jobs.worker`.

Runs the forking Worker with the built-in scheduler (ADR-0010 §1: one scheduler
in the worker container, agent definitions decide cadence). Root logging is
configured before anything else so job output reaches `docker logs`
(bidflow ADR-0026's lesson: an unobservable worker is a dead worker).
"""

from __future__ import annotations

import logging

from redis import Redis
from rq import Queue, Worker

from app.core.config import settings

QUEUES = ["default", "ingest", "agents", "notifications"]


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )
    conn = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=conn) for name in QUEUES]
    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
