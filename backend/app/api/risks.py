"""Risks — the register (ADR-0001 child), written only by humans.

    GET   /api/contracts/{id}/risks     register rows with citations; score/level derived
    POST  /api/contracts/{id}/risks     manual row (origin=human)      [kontrakt_red]
    PATCH /api/risks/{id}               edit fields / change status   [kontrakt_red]

AI proposals live in ai_suggestions (subject `risk`) until a human decides.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.risk_assess import next_seq
from app.api.schemas import CitationOut, RiskCreate, RiskOut, RiskPatch
from app.core import access, audit
from app.core.auth import Principal, require, tenant_session
from app.domain.models import (
    AuditAction,
    Citation,
    Contract,
    Origin,
    Risk,
    RiskStatus,
    SuccessorStatus,
)

router = APIRouter(prefix="/api", tags=["risks"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def risk_out(session: Session, r: Risk) -> RiskOut:
    cites = session.scalars(
        select(Citation)
        .where(Citation.subject_kind == "risk", Citation.subject_id == r.id)
        .order_by(Citation.created_at)
    ).all()
    return RiskOut(
        **{k: getattr(r, k) for k in RiskOut.STORED_FIELDS},
        citations=[CitationOut.model_validate(c) for c in cites],
        source_stale=any(c.successor_status == SuccessorStatus.ikke_fundet for c in cites),
    )


@router.get("/contracts/{contract_id}/risks", response_model=list[RiskOut])
def list_risks(
    contract_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[RiskOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(Risk).where(Risk.contract_id == contract_id).order_by(Risk.seq)
    ).all()
    return [risk_out(session, r) for r in rows]


@router.post(
    "/contracts/{contract_id}/risks", response_model=RiskOut, status_code=status.HTTP_201_CREATED
)
def create_risk(
    contract_id: uuid.UUID,
    body: RiskCreate,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> RiskOut:
    c = _contract_or_404(session, contract_id)
    now = datetime.now(UTC)
    r = Risk(
        organization_id=principal.org_id,
        contract_id=c.id,
        seq=next_seq(session, c.id),
        origin=Origin.human,
        created_by=principal.user_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    session.add(r)
    session.flush()
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.risk_created,
        actor=audit.human(principal),
        object_kind="risk",
        object_id=r.id,
        object_label=f"R-{r.seq} {r.title}",
        contract_id=c.id,
        details={"origin": "human", "score": r.probability * r.consequence},
    )
    return risk_out(session, r)


@router.patch("/risks/{risk_id}", response_model=RiskOut)
def patch_risk(
    risk_id: uuid.UUID,
    body: RiskPatch,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> RiskOut:
    r = session.get(Risk, risk_id)
    if r is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Risikoen findes ikke", "code": "not_found"},
        )
    changes = body.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}
    for k, v in changes.items():
        cur = getattr(r, k)
        if cur != v:
            before[k] = str(cur) if cur is not None else None
            setattr(r, k, v)
            after[k] = str(v) if v is not None else None
    now = datetime.now(UTC)
    label = f"R-{r.seq} {r.title}"
    if after:
        r.updated_at = now
        audit.record(
            session,
            org_id=r.organization_id,
            action=AuditAction.risk_updated,
            actor=audit.human(principal),
            object_kind="risk",
            object_id=r.id,
            object_label=label,
            contract_id=r.contract_id,
            details={"before": before, "after": after},
        )
    if new_status is not None and new_status != r.status:
        old = r.status
        r.status = new_status
        r.closed_at = now if new_status == RiskStatus.lukket else None
        r.updated_at = now
        audit.record(
            session,
            org_id=r.organization_id,
            action=AuditAction.risk_status_changed,
            actor=audit.human(principal),
            object_kind="risk",
            object_id=r.id,
            object_label=label,
            contract_id=r.contract_id,
            details={"before": old.value, "after": new_status.value},
        )
    session.flush()
    return risk_out(session, r)
