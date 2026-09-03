"""HITL + agents over HTTP.

    GET  /api/contracts/{id}/suggestions               queue + history for one contract
    POST /api/suggestions/{id}/approve                 hitl + subject permission (ADR-0004 §2)
    POST /api/suggestions/{id}/reject                  same, reason mandatory
    GET  /api/contracts/{id}/agent-runs                the run trail (ADR-0010 §3)
    POST /api/contracts/{id}/agents/{key}/run          "kør nu" — `agenter` (ADR-0010 afkl. 3)
    GET  /api/contracts/{id}/audit                     the audit trail — `audit` (ADR-0011 §5)

Model ids never leave the server (ADR-0008: provenance is developer-only), so
AgentRunOut has no `model` field.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import AGENTS
from app.ai import suggestions
from app.api.schemas import AgentRunOut, ApproveIn, AuditOut, RejectIn, SuggestionOut
from app.core import access, jobs
from app.core.auth import Principal, current_principal, require, tenant_session
from app.domain.models import AgentRun, AgentTrigger, AiSuggestion, AuditLog, Contract

router = APIRouter(prefix="/api", tags=["ai"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def _raise(e: suggestions.SuggestionError) -> HTTPException:
    return HTTPException(e.status, detail={"error": str(e), "code": e.code})


@router.get("/contracts/{contract_id}/suggestions", response_model=list[SuggestionOut])
def list_suggestions(
    contract_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[SuggestionOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(AiSuggestion)
        .where(AiSuggestion.contract_id == contract_id)
        .order_by(AiSuggestion.created_at.desc())
    ).all()
    return [SuggestionOut.model_validate(r) for r in rows]


@router.post("/suggestions/{suggestion_id}/approve", response_model=SuggestionOut)
def approve(
    suggestion_id: uuid.UUID,
    body: ApproveIn | None = None,
    principal: Principal = Depends(require(access.HITL)),
    session: Session = Depends(tenant_session),
) -> SuggestionOut:
    try:
        s = suggestions.approve(
            session,
            suggestion_id=suggestion_id,
            principal=principal,
            comment=body.comment if body else None,
        )
    except suggestions.SuggestionError as e:
        raise _raise(e) from e
    return SuggestionOut.model_validate(s)


@router.post("/suggestions/{suggestion_id}/reject", response_model=SuggestionOut)
def reject(
    suggestion_id: uuid.UUID,
    body: RejectIn,
    principal: Principal = Depends(require(access.HITL)),
    session: Session = Depends(tenant_session),
) -> SuggestionOut:
    try:
        s = suggestions.reject(
            session, suggestion_id=suggestion_id, principal=principal, comment=body.comment
        )
    except suggestions.SuggestionError as e:
        raise _raise(e) from e
    return SuggestionOut.model_validate(s)


@router.get("/contracts/{contract_id}/agent-runs", response_model=list[AgentRunOut])
def list_agent_runs(
    contract_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[AgentRunOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(AgentRun)
        .where(AgentRun.contract_id == contract_id)
        .order_by(AgentRun.started_at.desc())
        .limit(20)
    ).all()
    return [AgentRunOut.model_validate(r) for r in rows]


@router.post(
    "/contracts/{contract_id}/agents/{agent_key}/run", status_code=status.HTTP_202_ACCEPTED
)
def run_agent(
    contract_id: uuid.UUID,
    agent_key: str,
    principal: Principal = Depends(require(access.AGENTER)),
    session: Session = Depends(tenant_session),
) -> dict[str, str]:
    _contract_or_404(session, contract_id)
    agent = AGENTS.get(agent_key)
    if agent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "Ukendt agent", "code": "unknown_agent"}
        )
    session.commit()  # the job reads with its own session; nothing pending may stay unseen
    jobs.enqueue(
        agent.run_for_contract,
        org_id=principal.org_id,
        contract_id=contract_id,
        trigger=AgentTrigger.manual,
        triggered_by=principal.user_id,
    )
    return {"status": "queued", "agent_key": agent_key}


@router.get("/contracts/{contract_id}/audit", response_model=list[AuditOut])
def contract_audit(
    contract_id: uuid.UUID,
    principal: Principal = Depends(require(access.AUDIT)),
    session: Session = Depends(tenant_session),
) -> list[AuditOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(AuditLog)
        .where(AuditLog.contract_id == contract_id)
        .order_by(AuditLog.occurred_at.desc())
        .limit(200)
    ).all()
    return [AuditOut.model_validate(r) for r in rows]


__all__ = ["router", "current_principal"]
