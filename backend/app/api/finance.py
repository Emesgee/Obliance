"""KPI/SLA, penalty terms and claims over HTTP (ADR-0019, ADR-0013).

GET   /api/contracts/{id}/kpis                 targets + derived status + history
POST  /api/contracts/{id}/kpis                 manual target                   [kontrakt_red]
PATCH /api/kpis/{id}                           warn_band, links, active        [kontrakt_red]
POST  /api/kpis/{id}/measurements              measurement → breach/claim   [kontrakt_red|okonomi]
GET   /api/contracts/{id}/sla-breaches
GET   /api/contracts/{id}/penalty-terms
POST  /api/contracts/{id}/penalty-terms        manual parameters               [okonomi]
PATCH /api/penalty-terms/{id}                                                  [okonomi]
GET   /api/contracts/{id}/claims               amounts None without okonomi
POST  /api/claims/{id}/approve · /submit · /settle · /recompute               [okonomi]
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.sla_terms import link_by_name
from app.api.schemas import (
    CitationOut,
    ClaimActionIn,
    ClaimOut,
    ClaimSettleIn,
    KpiCreate,
    KpiOut,
    KpiPatch,
    KpiStatusOut,
    MeasurementIn,
    MeasurementOut,
    MeasurementResultOut,
    PenaltyTermCreate,
    PenaltyTermOut,
    PenaltyTermPatch,
    RecomputeOut,
    SlaBreachOut,
)
from app.core import access, audit
from app.core.auth import Principal, current_principal, require, tenant_session
from app.domain.models import (
    AuditAction,
    Citation,
    Contract,
    FinancialClaim,
    Kpi,
    KpiMeasurement,
    MeasurementSource,
    Origin,
    PenaltyTerm,
    SlaBreach,
    SuccessorStatus,
)
from app.finance import kpi_status, service

router = APIRouter(prefix="/api", tags=["finance"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def _raise(e: service.FinanceError) -> HTTPException:
    return HTTPException(e.status, detail={"error": str(e), "code": e.code})


def _cites(session: Session, kind: str, subject_id: uuid.UUID) -> list[CitationOut]:
    rows = session.scalars(
        select(Citation)
        .where(Citation.subject_kind == kind, Citation.subject_id == subject_id)
        .order_by(Citation.created_at)
    ).all()
    return [CitationOut.model_validate(c) for c in rows]


# ---- KPIs ------------------------------------------------------------------------------------


def kpi_out(session: Session, k: Kpi, today: date | None = None) -> KpiOut:
    rows = session.scalars(
        select(KpiMeasurement)
        .where(KpiMeasurement.kpi_id == k.id)
        .order_by(KpiMeasurement.period_start.desc(), KpiMeasurement.created_at.desc())
    ).all()
    live = [m for m in rows if m.superseded_by_id is None]
    latest = kpi_status.Measurement(live[0].period_start, live[0].value) if live else None
    st = kpi_status.evaluate(
        period=k.period.value,
        operator=k.target_operator.value,
        target=k.target_value,
        high=k.target_value_high,
        warn_band=k.warn_band,
        latest=latest,
        today=today or date.today(),
    )
    return KpiOut(
        id=k.id,
        contract_id=k.contract_id,
        seq=k.seq,
        ref=f"K-{k.seq}",
        name=k.name,
        unit=k.unit,
        target_operator=k.target_operator,
        target_value=k.target_value,
        target_value_high=k.target_value_high,
        target_text=kpi_status.target_text(
            k.target_operator.value, k.target_value, k.target_value_high, k.unit.value
        ),
        period=k.period,
        warn_band=k.warn_band,
        penalty_term_id=k.penalty_term_id,
        measurement_obligation_id=k.measurement_obligation_id,
        active=k.active,
        origin=k.origin,
        status=KpiStatusOut(
            color=st.color,
            reason=st.reason,
            measured_period_start=st.measured_period_start,
            value=st.value,
        ),
        measurements=[MeasurementOut.model_validate(m) for m in rows],
        citations=_cites(session, "kpi", k.id),
    )


@router.get("/contracts/{contract_id}/kpis", response_model=list[KpiOut])
def list_kpis(contract_id: uuid.UUID, session: Session = Depends(tenant_session)) -> list[KpiOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(Kpi).where(Kpi.contract_id == contract_id).order_by(Kpi.seq)
    ).all()
    return [kpi_out(session, k) for k in rows]


@router.post(
    "/contracts/{contract_id}/kpis", response_model=KpiOut, status_code=status.HTTP_201_CREATED
)
def create_kpi(
    contract_id: uuid.UUID,
    body: KpiCreate,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> KpiOut:
    c = _contract_or_404(session, contract_id)
    now = datetime.now(UTC)
    data = body.model_dump()
    if data["warn_band"] is None:
        data["warn_band"] = kpi_status.default_warn_band(body.unit.value, body.target_value)
    k = Kpi(
        organization_id=principal.org_id,
        contract_id=c.id,
        seq=service.next_seq(session, Kpi, c.id),
        origin=Origin.human,
        created_by=principal.user_id,
        created_at=now,
        updated_at=now,
        **data,
    )
    session.add(k)
    session.flush()
    link_by_name(session, c.id)
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.kpi_created,
        actor=audit.human(principal),
        object_kind="kpi",
        object_id=k.id,
        object_label=f"K-{k.seq} {k.name}",
        contract_id=c.id,
        details={"origin": "human", "target": str(k.target_value)},
    )
    return kpi_out(session, k)


@router.patch("/kpis/{kpi_id}", response_model=KpiOut)
def patch_kpi(
    kpi_id: uuid.UUID,
    body: KpiPatch,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> KpiOut:
    k = session.get(Kpi, kpi_id)
    if k is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "KPI'en findes ikke", "code": "not_found"}
        )
    changes = body.model_dump(exclude_unset=True)
    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}
    for key, v in changes.items():
        cur = getattr(k, key)
        if cur != v:
            before[key] = str(cur) if cur is not None else None
            setattr(k, key, v)
            after[key] = str(v) if v is not None else None
    if after:
        k.updated_at = datetime.now(UTC)
        audit.record(
            session,
            org_id=k.organization_id,
            action=AuditAction.kpi_updated,
            actor=audit.human(principal),
            object_kind="kpi",
            object_id=k.id,
            object_label=f"K-{k.seq} {k.name}",
            contract_id=k.contract_id,
            details={"before": before, "after": after},
        )
    session.flush()
    return kpi_out(session, k)


@router.post(
    "/kpis/{kpi_id}/measurements",
    response_model=MeasurementResultOut,
    status_code=status.HTTP_201_CREATED,
)
def record_measurement(
    kpi_id: uuid.UUID,
    body: MeasurementIn,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> MeasurementResultOut:
    """ADR-0019 §2 `manual`: kontrakt_red (or okonomi) records directly; the chain runs."""
    if not (principal.can(access.KONTRAKT_RED) or principal.can(access.OKONOMI)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"error": "Kræver kontrakt_red eller okonomi", "code": "forbidden"},
        )
    k = session.get(Kpi, kpi_id)
    if k is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"error": "KPI'en findes ikke", "code": "not_found"}
        )
    try:
        m, breach, claim = service.record_measurement(
            session,
            kpi=k,
            period_start=body.period_start,
            value=body.value,
            source_kind=MeasurementSource.manual,
            actor=audit.human(principal),
            actor_id=principal.user_id,
            note=body.note,
        )
    except service.FinanceError as e:
        raise _raise(e) from e
    return MeasurementResultOut(
        measurement=MeasurementOut.model_validate(m),
        breach=SlaBreachOut.model_validate(breach) if breach else None,
        claim=claim_out(session, claim, principal) if claim else None,
    )


@router.get("/contracts/{contract_id}/sla-breaches", response_model=list[SlaBreachOut])
def list_breaches(
    contract_id: uuid.UUID, session: Session = Depends(tenant_session)
) -> list[SlaBreachOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(SlaBreach)
        .where(SlaBreach.contract_id == contract_id)
        .order_by(SlaBreach.period_start.desc())
    ).all()
    return [SlaBreachOut.model_validate(b) for b in rows]


# ---- penalty terms ---------------------------------------------------------------------------


def term_out(session: Session, t: PenaltyTerm) -> PenaltyTermOut:
    return PenaltyTermOut(
        id=t.id,
        contract_id=t.contract_id,
        seq=t.seq,
        ref=f"B-{t.seq}",
        name=t.name,
        term_type=t.term_type,
        trigger_description=t.trigger_description,
        applies_to=t.applies_to,
        rate=t.rate,
        tiers=t.tiers,
        basis=t.basis,
        basis_amount=t.basis_amount,
        time_unit=t.time_unit,
        cap_rate=t.cap_rate,
        cap_amount=t.cap_amount,
        status=t.status,
        origin=t.origin,
        citations=_cites(session, "penalty_term", t.id),
    )


@router.get("/contracts/{contract_id}/penalty-terms", response_model=list[PenaltyTermOut])
def list_terms(
    contract_id: uuid.UUID, session: Session = Depends(tenant_session)
) -> list[PenaltyTermOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(PenaltyTerm).where(PenaltyTerm.contract_id == contract_id).order_by(PenaltyTerm.seq)
    ).all()
    return [term_out(session, t) for t in rows]


@router.post(
    "/contracts/{contract_id}/penalty-terms",
    response_model=PenaltyTermOut,
    status_code=status.HTTP_201_CREATED,
)
def create_term(
    contract_id: uuid.UUID,
    body: PenaltyTermCreate,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> PenaltyTermOut:
    c = _contract_or_404(session, contract_id)
    now = datetime.now(UTC)
    t = PenaltyTerm(
        organization_id=principal.org_id,
        contract_id=c.id,
        seq=service.next_seq(session, PenaltyTerm, c.id),
        origin=Origin.human,
        created_by=principal.user_id,
        approved_by=principal.user_id,
        created_at=now,
        updated_at=now,
        **body.model_dump(),
    )
    session.add(t)
    session.flush()
    link_by_name(session, c.id)
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.penalty_term_created,
        actor=audit.human(principal),
        object_kind="penalty_term",
        object_id=t.id,
        object_label=f"B-{t.seq} {t.name}",
        contract_id=c.id,
        details={"origin": "human", "term_type": t.term_type.value, "rate": str(t.rate)},
    )
    return term_out(session, t)


@router.patch("/penalty-terms/{term_id}", response_model=PenaltyTermOut)
def patch_term(
    term_id: uuid.UUID,
    body: PenaltyTermPatch,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> PenaltyTermOut:
    t = session.get(PenaltyTerm, term_id)
    if t is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Bodsklausulen findes ikke", "code": "not_found"},
        )
    changes = body.model_dump(exclude_unset=True)
    before: dict[str, str | None] = {}
    after: dict[str, str | None] = {}
    for key, v in changes.items():
        cur = getattr(t, key)
        if cur != v:
            before[key] = str(cur) if cur is not None else None
            setattr(t, key, v)
            after[key] = str(v) if v is not None else None
    if after:
        t.updated_at = datetime.now(UTC)
        t.approved_by = principal.user_id
        link_by_name(session, t.contract_id)
        audit.record(
            session,
            org_id=t.organization_id,
            action=AuditAction.penalty_term_updated,
            actor=audit.human(principal),
            object_kind="penalty_term",
            object_id=t.id,
            object_label=f"B-{t.seq} {t.name}",
            contract_id=t.contract_id,
            details={"before": before, "after": after},
        )
    session.flush()
    return term_out(session, t)


# ---- claims ----------------------------------------------------------------------------------


def claim_out(session: Session, c: FinancialClaim, principal: Principal) -> ClaimOut:
    money = principal.can(access.OKONOMI)
    return ClaimOut(
        id=c.id,
        contract_id=c.contract_id,
        seq=c.seq,
        ref=f"KR-{c.seq}",
        claim_type=c.claim_type,
        period_start=c.period_start,
        period_end=c.period_end,
        penalty_term_id=c.penalty_term_id,
        breach_id=c.breach_id,
        amount=c.amount if money else None,
        amount_uncapped=c.amount_uncapped if money else None,
        cap_applied=c.cap_applied,
        basis_text=c.basis_text if money else None,
        formula_version=c.formula_version,
        currency=c.currency,
        status=c.status,
        requires_second_signature=c.amount > service.TWO_SIGNATURE_THRESHOLD,
        created_by=c.created_by,
        approved_by=c.approved_by,
        approved_at=c.approved_at,
        second_approved_by=c.second_approved_by,
        submitted_at=c.submitted_at,
        decision_comment=c.decision_comment,
        created_at=c.created_at,
        updated_at=c.updated_at,
        citations=_cites(session, "financial_claim", c.id),
    )


@router.get("/contracts/{contract_id}/claims", response_model=list[ClaimOut])
def list_claims(
    contract_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> list[ClaimOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(FinancialClaim)
        .where(FinancialClaim.contract_id == contract_id)
        .order_by(FinancialClaim.seq.desc())
    ).all()
    return [claim_out(session, c, principal) for c in rows]


@router.post("/claims/{claim_id}/approve", response_model=ClaimOut)
def approve_claim(
    claim_id: uuid.UUID,
    body: ClaimActionIn | None = None,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> ClaimOut:
    try:
        c = service.approve_claim(
            session, claim_id=claim_id, principal=principal, comment=body.comment if body else None
        )
    except service.FinanceError as e:
        raise _raise(e) from e
    return claim_out(session, c, principal)


@router.post("/claims/{claim_id}/submit", response_model=ClaimOut)
def submit_claim(
    claim_id: uuid.UUID,
    body: ClaimActionIn | None = None,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> ClaimOut:
    try:
        c = service.submit_claim(
            session, claim_id=claim_id, principal=principal, comment=body.comment if body else None
        )
    except service.FinanceError as e:
        raise _raise(e) from e
    return claim_out(session, c, principal)


@router.post("/claims/{claim_id}/settle", response_model=ClaimOut)
def settle_claim(
    claim_id: uuid.UUID,
    body: ClaimSettleIn,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> ClaimOut:
    try:
        c = service.settle_claim(
            session,
            claim_id=claim_id,
            principal=principal,
            status=body.status,
            comment=body.comment,
        )
    except service.FinanceError as e:
        raise _raise(e) from e
    return claim_out(session, c, principal)


@router.post("/claims/{claim_id}/recompute", response_model=RecomputeOut)
def recompute_claim(
    claim_id: uuid.UUID,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> RecomputeOut:
    """ADR-0013 §3: same inputs, same term, same number — a safe button."""
    try:
        r = service.recompute_claim(session, claim_id=claim_id)
    except service.FinanceError as e:
        raise _raise(e) from e
    c = session.get(FinancialClaim, claim_id)
    assert c is not None
    return RecomputeOut(
        amount=r.amount,
        amount_uncapped=r.amount_uncapped,
        basis_text=r.basis_text,
        formula_version=r.formula_version,
        matches_stored=r.amount == c.amount and r.formula_version == c.formula_version,
    )


__all__ = ["router", "SuccessorStatus"]
