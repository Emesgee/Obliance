"""The scheduler — one process owns the calendar (ADR-0010 §1).

    plan(at)  → what fires this minute: one (agent, org) per scheduled definition
                whose cron (or the org's override) matches, smallest org first (§5)
    tick(at)  → enqueue those; write `sprunget_over · disabled` rows for paused
                agents (§2) so their silence is visible; enqueue the daily alert
                sweep (§7)
    loop()    → tick once per minute; with Redis, a SET NX claim per minute keeps
                worker replicas from planning the same minute twice

The calendar is Europe/Copenhagen (settings.scheduler_timezone): "kl. 02:00" in a
definition means Danish night, DST included.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.agents.definitions import ALERT_CRON, DEFINITIONS
from app.core import jobs
from app.core.config import settings
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import (
    AgentSetting,
    AgentTrigger,
    Contract,
    Organization,
)
from app.jobs import alerts, cron, runs

log = logging.getLogger(__name__)


def tz() -> ZoneInfo:
    return ZoneInfo(settings.scheduler_timezone)


@dataclass(frozen=True, slots=True)
class Planned:
    agent_key: str
    org_id: uuid.UUID
    enabled: bool
    cron: str
    contracts: int


def _org_sizes() -> list[tuple[uuid.UUID, int, dict[str, AgentSetting]]]:
    with SessionLocal() as s:
        org_ids = list(s.scalars(select(Organization.id)).all())
    out = []
    for oid in org_ids:
        with tenant(oid, system=True), SessionLocal() as s:
            n = s.scalar(
                select(func.count())
                .select_from(Contract)
                .where(Contract.status.in_(runs.MONITORED))
            )
            st = {x.agent_key: x for x in s.scalars(select(AgentSetting)).all()}
        out.append((oid, int(n or 0), st))
    out.sort(key=lambda t: (t[1], str(t[0])))  # fewest contracts first (§5)
    return out


def plan(at: datetime) -> list[Planned]:
    """`at` is a tz-aware local time; only its minute matters."""
    planned: list[Planned] = []
    for oid, n, st in _org_sizes():
        for d in DEFINITIONS:
            if not d.scheduled or d.cadence is None:
                continue
            setting = st.get(d.key)
            expr = d.cadence
            if setting is not None and setting.schedule_override:
                try:
                    cron.validate(setting.schedule_override)
                    expr = setting.schedule_override
                except cron.CronError:
                    log.error(
                        "agent=%s org=%s invalid schedule_override %r — using default %r",
                        d.key,
                        oid,
                        setting.schedule_override,
                        d.cadence,
                    )
            if cron.matches(expr, at):
                enabled = setting.enabled if setting is not None else True
                planned.append(Planned(d.key, oid, enabled, expr, n))
    return planned


def tick(at: datetime | None = None) -> list[Planned]:
    at = at or datetime.now(tz())
    planned = plan(at)
    for p in planned:
        if not p.enabled:
            runs.skip_row(
                agent_key=p.agent_key,
                org_id=p.org_id,
                trigger=AgentTrigger.schedule,
                reason="disabled",
            )
            continue
        log.info("schedule agent=%s org=%s contracts=%d", p.agent_key, p.org_id, p.contracts)
        jobs.enqueue(
            runs.run_org, agent_key=p.agent_key, org_id=p.org_id, trigger=AgentTrigger.schedule
        )
    if cron.matches(ALERT_CRON, at):
        jobs.enqueue(alerts.log_all)
    return planned


def _claim(minute: datetime) -> bool:
    """True if this process should plan `minute`. Only one claim succeeds per
    minute across replicas when Redis is in play; without it (dev) always True."""
    if settings.jobs_mode_effective != "rq":
        return True
    from redis import Redis

    key = f"obliance:scheduler:{minute.isoformat()}"
    return bool(Redis.from_url(settings.redis_url).set(key, "1", nx=True, ex=3600))


def loop(stop: threading.Event | None = None) -> None:
    stop = stop or threading.Event()
    log.info(
        "scheduler started (tz=%s, jobs=%s)",
        settings.scheduler_timezone,
        settings.jobs_mode_effective,
    )
    last: datetime | None = None
    while not stop.is_set():
        now = datetime.now(tz())
        minute = now.replace(second=0, microsecond=0)
        if minute != last:
            last = minute
            try:
                if _claim(minute):
                    tick(minute)
            except Exception:  # noqa: BLE001 — the scheduler must survive a bad minute
                log.exception("scheduler tick failed at %s", minute)
        # sleep to the next minute boundary, waking early on stop
        stop.wait(max(1.0, 60 - now.second - now.microsecond / 1e6))
    log.info("scheduler stopped")


def main() -> None:
    """`python -m app.jobs.scheduler [--once]` — the loop, or one tick for a dry check."""
    import sys

    logging.basicConfig(
        level=settings.log_level, format="%(asctime)s %(name)s [%(levelname)s] %(message)s"
    )
    if "--once" in sys.argv:
        for p in tick():
            print(p)
        return
    loop()


if __name__ == "__main__":
    main()
