"""Alerting (ADR-0010 §7) — the four conditions, evaluated per organisation:

    failed_3x     the last three finished runs all failed
    stale_48h     a scheduled, enabled agent has had no `ok` run for 48 hours
    budget        a run in the last 24 hours was stopped by the daily budget
    batch_stuck   a batch run still `koerer` after 24 hours

`evaluate` is what the dashboard and the agent page show; `log_all` is the daily
sweep the scheduler enqueues — it writes one ERROR line per alert with agent,
org and run id, which is what journald in the worker container collects.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.definitions import DEFINITIONS
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import AgentRun, AgentRunStatus, AgentSetting, Organization

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Alert:
    agent_key: str
    code: str
    message: str
    run_id: uuid.UUID | None = None


def evaluate(s: Session, org_id: uuid.UUID, now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now(UTC)
    settings_by_key = {x.agent_key: x for x in s.scalars(select(AgentSetting)).all()}
    out: list[Alert] = []
    for d in DEFINITIONS:
        base = select(AgentRun).where(
            AgentRun.organization_id == org_id, AgentRun.agent_key == d.key
        )
        finished = list(
            s.scalars(
                base.where(AgentRun.status != AgentRunStatus.koerer)
                .order_by(AgentRun.started_at.desc())
                .limit(3)
            ).all()
        )
        if len(finished) == 3 and all(r.status == AgentRunStatus.fejlet for r in finished):
            out.append(Alert(d.key, "failed_3x", "Tre fejlede kørsler i træk", finished[0].id))
        setting = settings_by_key.get(d.key)
        enabled = setting.enabled if setting is not None else True
        if d.scheduled and enabled and finished:
            last_ok = s.scalars(
                base.where(AgentRun.status == AgentRunStatus.ok)
                .order_by(AgentRun.started_at.desc())
                .limit(1)
            ).first()
            if last_ok is None or last_ok.started_at < now - timedelta(hours=48):
                out.append(Alert(d.key, "stale_48h", "Ingen vellykket kørsel i 48 timer"))
        budget = s.scalars(
            base.where(
                AgentRun.status == AgentRunStatus.sprunget_over,
                AgentRun.started_at >= now - timedelta(hours=24),
                AgentRun.error_context["reason"].astext == "budget",
            ).limit(1)
        ).first()
        if budget is not None:
            out.append(Alert(d.key, "budget", "Døgnbudgettet stoppede kørslen", budget.id))
        stuck = s.scalars(
            base.where(
                AgentRun.status == AgentRunStatus.koerer,
                AgentRun.batch_id.is_not(None),
                AgentRun.started_at < now - timedelta(hours=24),
            ).limit(1)
        ).first()
        if stuck is not None:
            out.append(Alert(d.key, "batch_stuck", "Batch ikke afsluttet efter 24 timer", stuck.id))
    return out


def log_all() -> int:
    """The daily sweep. Returns the number of alerts written."""
    with SessionLocal() as s:
        org_ids = list(s.scalars(select(Organization.id)).all())
    n = 0
    for oid in org_ids:
        with tenant(oid, system=True), SessionLocal() as s:
            for a in evaluate(s, oid):
                n += 1
                log.error(
                    "ALERT %s agent=%s org=%s run=%s: %s",
                    a.code,
                    a.agent_key,
                    oid,
                    a.run_id,
                    a.message,
                )
    return n
