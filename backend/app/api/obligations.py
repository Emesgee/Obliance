"""Obligations — the register (ADR-0001 child), written only by humans.

    GET   /api/contracts/{id}/obligations     register rows with citations; status derived
    POST  /api/contracts/{id}/obligations     manual row (origin=human)      [kontrakt_red]
    PATCH /api/obligations/{id}               edit fields / change status   [kontrakt_red]

AI proposals are NOT in this list — they live in ai_suggestions (ADR-0004) and the
screen unions them in with the AI badge. `forsinket` is derived at read time
(ADR-0001: derived facts are not stored).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.obligation_extract import next_seq
from app.api.schemas import CitationOut, ObligationCreate, ObligationOut, ObligationPatch
from app.core import access, audit
from app.core.auth import Principal, require, tenant_session
from app.domain.models import (
    AuditAction,
    Citation,
    Contract,
    Obligation,
    ObligationStatus,
    Origin,
    SuccessorStatus,
)

router = APIRouter(prefix="/api", tags=["obligations"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def obligation_out(session: Session, o: Obligation) -> ObligationOut:
    cites = session.scalars(
        select(Citation)
        .where(Citation.subject_kind == "obligation", Citation.subject_id == o.id)
        .order_by(Citation.created_at)
    ).all()
    return ObligationOut(
        **{k: getattr(o, k) for k in ObligationOut.STORED_FIELDS},
        citations=[CitationOut.model_validate(c) for c in cites],
        source_stale=any(c.successor_status == SuccessorStatus.ikke_fundet for c in cites),
    )


@router.get("/contracts/{contract_id}/obligations", response_model=list[ObligationOut])
def list_obligations(
    contract_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[ObligationOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(Obligation).where(Obligation.contract_id == contract_id).order_by(Obligation.seq)
    ).all()
    return [obligation_out(session, o) for o in rows]


@router.post(
    "/contracts/{contract_id}/obligations",
    response_model=ObligationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_obligation(
    contract_id: uuid.UUID,
    body: ObligationCreate,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> ObligationOut:
    c = _contract_or_404(session, contract_id)
    now = datetime.now(UTC)
    o = Obligation(
        organization_id=principal.org_id,
        contract_id=c.id,
        seq=next_seq(session, c.id),
        origin=Origin.human,
        created_by=principal.user_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    session.add(o)
    session.flush()
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.obligation_created,
        actor=audit.human(principal),
        object_kind="obligation",
        object_id=o.id,
        object_label=f"F-{o.seq} {o.title}",
        contract_id=c.id,
        details={"origin": "human"},
    )
    return obligation_out(session, o)


@router.patch("/obligations/{obligation_id}", response_model=ObligationOut)
def patch_obligation(
    obligation_id: uuid.UUID,
    body: ObligationPatch,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> ObligationOut:
    o = session.get(Obligation, obligation_id)
    if o is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Forpligtelsen findes ikke", "code": "not_found"},
        )
    changes = body.model_dump(exclude_unset=True)
    new_status = changes.pop("status", None)
    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}
    for k, v in changes.items():
        cur = getattr(o, k)
        if cur != v:
            before[k] = str(cur) if cur is not None else None
            setattr(o, k, v)
            after[k] = str(v) if v is not None else None
    now = datetime.now(UTC)
    label = f"F-{o.seq} {o.title}"
    if after:
        o.updated_at = now
        audit.record(
            session,
            org_id=o.organization_id,
            action=AuditAction.obligation_updated,
            actor=audit.human(principal),
            object_kind="obligation",
            object_id=o.id,
            object_label=label,
            contract_id=o.contract_id,
            details={"before": before, "after": after},
        )
    if new_status is not None and new_status != o.status:
        old = o.status
        o.status = new_status
        o.fulfilled_at = now if new_status == ObligationStatus.opfyldt else None
        o.updated_at = now
        audit.record(
            session,
            org_id=o.organization_id,
            action=AuditAction.obligation_status_changed,
            actor=audit.human(principal),
            object_kind="obligation",
            object_id=o.id,
            object_label=label,
            contract_id=o.contract_id,
            details={"before": old.value, "after": new_status.value},
        )
    session.flush()
    return obligation_out(session, o)
