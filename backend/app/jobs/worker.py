"""RQ worker entry point — `python -m app.jobs.worker`.

One process per worker container: the scheduler loop in a thread (ADR-0010 §1;
with several replicas a per-minute Redis claim keeps the calendar single) and
the forking RQ Worker on the queues. Root logging is configured before anything
else so job output reaches `docker logs` (bidflow ADR-0026's lesson: an
unobservable worker is a dead worker).
"""

from __future__ import annotations

import logging
import threading

from redis import Redis
from rq import Queue, Worker

from app.core.config import settings
from app.jobs import scheduler

QUEUES = ["agents", "ingest", "notifications", "default"]


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
    )
    from app import agents

    agents.register()  # event listeners, so jobs that emit events behave as in the API
    conn = Redis.from_url(settings.redis_url)
    queues = [Queue(name, connection=conn) for name in QUEUES]
    threading.Thread(target=scheduler.loop, name="scheduler", daemon=True).start()
    worker = Worker(queues, connection=conn)
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
