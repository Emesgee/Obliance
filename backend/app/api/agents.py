"""AI-agenter (ADR-0010 §2, afklaring 2–3) — the administration screen's API.

GET  /api/agents                     definitions + on/off + last run + alerts   [agenter]
PUT  /api/agents/{key}/settings      pause / resume / schedule override         [agenter]
GET  /api/agents/{key}/runs          the run trail for one agent                [agenter]
POST /api/agents/{key}/run           "kør nu" for the whole organisation        [agenter]

Pausing writes who, when and why on `agent_settings` (§2) and an audit row; the
paused agent's existing proposals stay (afklaring 1). "Kør nu" is the same job the
scheduler enqueues — same lock, same `agent_runs` row, `trigger = manual`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.definitions import BY_KEY, DEFINITIONS, AgentDefinition
from app.api.schemas import AgentInfoOut, AgentRunOut, AgentSettingsIn
from app.core import access, audit, jobs
from app.core.auth import Principal, require, tenant_session
from app.domain.models import (
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AgentTrigger,
    AuditAction,
    Profile,
)
from app.jobs import alerts, cron, runs

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _definition_or_404(key: str) -> AgentDefinition:
    d = BY_KEY.get(key)
    if d is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "Ukendt agent", "code": "unknown_agent"}
        )
    return d


def _info(session: Session, org_id: uuid.UUID, d: AgentDefinition) -> AgentInfoOut:
    setting = session.get(AgentSetting, (org_id, d.key))
    last = session.scalars(
        select(AgentRun)
        .where(AgentRun.agent_key == d.key, AgentRun.status != AgentRunStatus.koerer)
        .order_by(AgentRun.started_at.desc())
        .limit(1)
    ).first()
    paused_by_name = None
    if setting is not None and setting.paused_by is not None:
        p = session.get(Profile, setting.paused_by)
        paused_by_name = p.name if p else None
    return AgentInfoOut(
        agent_key=d.key,
        label=d.label,
        purpose=d.purpose,
        task=d.task,
        scope=d.scope,
        trigger=d.trigger,
        cadence=d.cadence,
        event=d.event,
        enabled=setting.enabled if setting is not None else True,
        schedule_override=setting.schedule_override if setting is not None else None,
        paused_by_name=paused_by_name,
        paused_at=setting.paused_at if setting is not None else None,
        paused_reason=setting.paused_reason if setting is not None else None,
        last_run=AgentRunOut.model_validate(last) if last is not None else None,
        alerts=[],
    )


@router.get("", response_model=list[AgentInfoOut])
def list_agents(
    principal: Principal = Depends(require(access.AGENTER)),
    session: Session = Depends(tenant_session),
) -> list[AgentInfoOut]:
    found = alerts.evaluate(session, principal.org_id)
    out = []
    for d in DEFINITIONS:
        info = _info(session, principal.org_id, d)
        info.alerts = [f"{a.message}" for a in found if a.agent_key == d.key]
        out.append(info)
    return out


@router.put("/{agent_key}/settings", response_model=AgentInfoOut)
def update_settings(
    agent_key: str,
    body: AgentSettingsIn,
    principal: Principal = Depends(require(access.AGENTER)),
    session: Session = Depends(tenant_session),
) -> AgentInfoOut:
    d = _definition_or_404(agent_key)
    if body.schedule_override:
        if not d.scheduled:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": "Agenten kører ikke efter kalender", "code": "not_scheduled"},
            )
        try:
            cron.validate(body.schedule_override)
        except cron.CronError as e:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": str(e), "code": "invalid_cron"},
            ) from e
    if not body.enabled and not (body.reason or "").strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "Angiv en begrundelse for at pause agenten",
                "code": "reason_required",
            },
        )
    setting = session.get(AgentSetting, (principal.org_id, d.key))
    if setting is None:
        setting = AgentSetting(organization_id=principal.org_id, agent_key=d.key)
        session.add(setting)
    was_enabled = setting.enabled is not False
    setting.enabled = body.enabled
    setting.schedule_override = body.schedule_override or None
    if not body.enabled:
        setting.paused_by = principal.user_id
        setting.paused_at = datetime.now(UTC)
        setting.paused_reason = body.reason.strip() if body.reason else None
    else:
        setting.paused_by = None
        setting.paused_at = None
        setting.paused_reason = None
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.agent_settings_changed,
        actor=audit.human(principal),
        object_kind="agent",
        object_label=d.label,
        details={
            "agent_key": d.key,
            "enabled": body.enabled,
            "was_enabled": was_enabled,
            "reason": body.reason,
            "schedule_override": body.schedule_override,
        },
    )
    session.commit()
    return _info(session, principal.org_id, d)


@router.get("/{agent_key}/runs", response_model=list[AgentRunOut])
def list_runs(
    agent_key: str,
    limit: int = 30,
    principal: Principal = Depends(require(access.AGENTER)),
    session: Session = Depends(tenant_session),
) -> list[AgentRunOut]:
    d = _definition_or_404(agent_key)
    rows = session.scalars(
        select(AgentRun)
        .where(AgentRun.agent_key == d.key)
        .order_by(AgentRun.started_at.desc())
        .limit(max(1, min(limit, 200)))
    ).all()
    return [AgentRunOut.model_validate(r) for r in rows]


@router.post("/{agent_key}/run", status_code=status.HTTP_202_ACCEPTED)
def run_now(
    agent_key: str,
    principal: Principal = Depends(require(access.AGENTER)),
    session: Session = Depends(tenant_session),
) -> dict[str, str]:
    d = _definition_or_404(agent_key)
    session.commit()  # the job reads with its own session; nothing pending may stay unseen
    jobs.enqueue(
        runs.run_org,
        agent_key=d.key,
        org_id=principal.org_id,
        trigger=AgentTrigger.manual,
        triggered_by=principal.user_id,
    )
    return {"status": "queued", "agent_key": d.key}
