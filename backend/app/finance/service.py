"""The measurement → breach → claim chain (ADR-0019 §5, ADR-0013 §3/§4) and the
claim lifecycle. Everything here runs in the caller's tenant session; nothing
here calls a model.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import access, audit
from app.core.auth import Principal
from app.domain.models import (
    AuditAction,
    Citation,
    ClaimStatus,
    ClaimType,
    Contract,
    FinancialClaim,
    Kpi,
    KpiMeasurement,
    MeasurementSource,
    MemberRole,
    PenaltyBasis,
    PenaltyTerm,
    SlaBreach,
    TermStatus,
)
from app.finance import kpi_status, penalties

TWO_SIGNATURE_THRESHOLD = Decimal("250000.00")  # ADR-0013 §4 / ADR-0003


class FinanceError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def next_seq(session: Session, model: type[Any], contract_id: uuid.UUID) -> int:
    return (
        int(
            session.scalar(
                select(func.coalesce(func.max(model.seq), 0)).where(
                    model.contract_id == contract_id
                )
            )
            or 0
        )
        + 1
    )


def _label(session: Session, contract_id: uuid.UUID) -> str:
    c = session.get(Contract, contract_id)
    return f"{c.reference} {c.name}" if c else str(contract_id)


def term_citation_label(session: Session, term: PenaltyTerm) -> str:
    c = session.scalars(
        select(Citation).where(
            Citation.subject_kind == "penalty_term", Citation.subject_id == term.id
        )
    ).first()
    return c.label if c else ""


def as_term(session: Session, term: PenaltyTerm, contract: Contract) -> penalties.Term:
    """Approved parameters + a basis amount resolved from the term or the contract."""
    basis_amount = term.basis_amount
    if basis_amount is None:
        if (
            term.basis == PenaltyBasis.maanedligt_driftsvederlag
            and contract.annual_value is not None
        ):
            basis_amount = (contract.annual_value / 12).quantize(Decimal("0.01"))
        elif term.basis == PenaltyBasis.aarligt_vederlag and contract.annual_value is not None:
            basis_amount = contract.annual_value
    tiers = tuple(
        (Decimal(str(t["below"])), Decimal(str(t["rate"])))
        for t in (term.tiers or [])
        if "below" in t and "rate" in t
    )
    return penalties.Term(
        term_type=term.term_type.value,
        rate=term.rate,
        tiers=tiers,
        basis=term.basis.value,
        basis_amount=basis_amount,
        cap_rate=term.cap_rate,
        cap_amount=term.cap_amount,
        citation_label=term_citation_label(session, term),
    )


# ---- measurements ------------------------------------------------------------------------------


def record_measurement(
    session: Session,
    *,
    kpi: Kpi,
    period_start: date,
    value: Decimal,
    source_kind: MeasurementSource,
    actor: audit.Actor,
    actor_id: uuid.UUID | None,
    note: str | None = None,
    suggestion_id: uuid.UUID | None = None,
) -> tuple[KpiMeasurement, SlaBreach | None, FinancialClaim | None]:
    """Record an approved measurement (manual/import/document-after-HITL). A second
    value for the same period supersedes the first — with a reason — and
    recomputes: an open claim is dropped and a new one calculated (ADR-0019 §2)."""
    if not kpi_status.is_period_start(kpi.period.value, period_start):
        raise FinanceError("bad_period", f"Perioden skal starte på en {kpi.period.value}-grænse")
    p_end = kpi_status.period_end(kpi.period.value, period_start)
    existing = session.scalars(
        select(KpiMeasurement).where(
            KpiMeasurement.kpi_id == kpi.id,
            KpiMeasurement.period_start == period_start,
            KpiMeasurement.superseded_by_id.is_(None),
        )
    ).first()
    if existing is not None and not (note or "").strip():
        raise FinanceError("reason_required", "En erstattende måling kræver en begrundelse")
    now = datetime.now(UTC)
    m = KpiMeasurement(
        organization_id=kpi.organization_id,
        contract_id=kpi.contract_id,
        kpi_id=kpi.id,
        period_start=period_start,
        period_end=p_end,
        value=value,
        source_kind=source_kind,
        entered_by=actor_id,
        approved_by=actor_id,
        approved_at=now,
        note=note,
        suggestion_id=suggestion_id,
        supersedes_measurement_id=existing.id if existing else None,
        created_at=now,
    )
    if existing is not None:
        # The partial unique index allows one live row per period: retire the old
        # row (self-reference as placeholder) before the new one is inserted.
        existing.superseded_by_id = existing.id
        session.flush()
    session.add(m)
    session.flush()
    label = f"K-{kpi.seq} {kpi.name} · {period_start.isoformat()}"
    if existing is not None:
        existing.superseded_by_id = m.id
        session.flush()
        _drop_open_claims_for(session, existing, actor, note or "")
        audit.record(
            session,
            org_id=kpi.organization_id,
            action=AuditAction.measurement_superseded,
            actor=actor,
            object_kind="kpi_measurement",
            object_id=m.id,
            object_label=label,
            contract_id=kpi.contract_id,
            details={"old_value": str(existing.value), "new_value": str(value), "reason": note},
        )
    audit.record(
        session,
        org_id=kpi.organization_id,
        action=AuditAction.measurement_recorded,
        actor=actor,
        object_kind="kpi_measurement",
        object_id=m.id,
        object_label=label,
        contract_id=kpi.contract_id,
        details={"value": str(value), "source": source_kind.value},
    )
    breach, claim = evaluate_breach(session, kpi=kpi, measurement=m, actor=actor)
    return m, breach, claim


def _drop_open_claims_for(
    session: Session, old: KpiMeasurement, actor: audit.Actor, reason: str
) -> None:
    breaches = session.scalars(select(SlaBreach).where(SlaBreach.measurement_id == old.id)).all()
    for b in breaches:
        if b.claim_id is None:
            continue
        claim = session.get(FinancialClaim, b.claim_id)
        if claim is None:
            continue
        if claim.status in (
            ClaimStatus.beregnet,
            ClaimStatus.afventer_2_signatur,
            ClaimStatus.godkendt,
        ):
            _set_claim_status(
                session, claim, ClaimStatus.frafaldet, actor, f"måling erstattet: {reason}"
            )
        else:
            # ADR-0019 afklaring 3: a submitted claim is out of the house — a human drops it.
            claim.decision_comment = " · ".join(
                x
                for x in [claim.decision_comment, f"grundlaget er ændret ({reason}) — tag stilling"]
                if x
            )
            claim.updated_at = datetime.now(UTC)
    session.flush()


def evaluate_breach(
    session: Session, *, kpi: Kpi, measurement: KpiMeasurement, actor: audit.Actor
) -> tuple[SlaBreach | None, FinancialClaim | None]:
    """Compare in code (ADR-0019 §5). Not met → breach; with active approved
    parameters → a `beregnet` claim in the same transaction; without → a note."""
    met, _ = kpi_status.met_and_distance(
        kpi.target_operator.value, kpi.target_value, kpi.target_value_high, measurement.value
    )
    if met:
        return None, None
    term = session.get(PenaltyTerm, kpi.penalty_term_id) if kpi.penalty_term_id else None
    breach = SlaBreach(
        organization_id=kpi.organization_id,
        contract_id=kpi.contract_id,
        kpi_id=kpi.id,
        measurement_id=measurement.id,
        period_start=measurement.period_start,
        period_end=measurement.period_end,
        target_value=kpi.target_value,
        actual_value=measurement.value,
        penalty_term_id=term.id if term else None,
    )
    session.add(breach)
    session.flush()
    claim: FinancialClaim | None = None
    if term is None:
        breach.note = "SLA-brud registreret — ingen bodsklausul knyttet til KPI'en"
    elif term.status != TermStatus.aktiv:
        breach.note = "SLA-brud registreret — bodsklausulens parametre kræver ny godkendelse"
    else:
        contract = session.get(Contract, kpi.contract_id)
        assert contract is not None
        try:
            result = penalties.calculate(
                as_term(session, term, contract),
                {"actual": str(measurement.value), "target": str(kpi.target_value)},
            )
        except penalties.DataMissing as e:
            breach.note = f"SLA-brud registreret — beregning mangler input: {e.field}"
        except penalties.Unstructurable as e:
            breach.note = f"SLA-brud registreret — bodsklausul kan ikke beregnes: {e}"
        else:
            claim = create_claim(
                session,
                contract=contract,
                claim_type=ClaimType.service_credit
                if term.term_type.value.startswith("service_credit")
                else ClaimType.bod,
                period_start=measurement.period_start,
                period_end=measurement.period_end,
                term=term,
                breach=breach,
                result=result,
                actor=actor,
            )
            breach.claim_id = claim.id
    audit.record(
        session,
        org_id=kpi.organization_id,
        action=AuditAction.sla_breach_recorded,
        actor=actor,
        object_kind="sla_breach",
        object_id=breach.id,
        object_label=f"K-{kpi.seq} {kpi.name} · {measurement.period_start.isoformat()}",
        contract_id=kpi.contract_id,
        details={
            "target": str(kpi.target_value),
            "actual": str(measurement.value),
            "note": breach.note,
        },
    )
    session.flush()
    return breach, claim


# ---- claims --------------------------------------------------------------------------------------


def create_claim(
    session: Session,
    *,
    contract: Contract,
    claim_type: ClaimType,
    period_start: date | None,
    period_end: date | None,
    term: PenaltyTerm | None,
    breach: SlaBreach | None,
    result: penalties.Result,
    actor: audit.Actor,
    created_by: uuid.UUID | None = None,
) -> FinancialClaim:
    now = datetime.now(UTC)
    claim = FinancialClaim(
        organization_id=contract.organization_id,
        contract_id=contract.id,
        seq=next_seq(session, FinancialClaim, contract.id),
        claim_type=claim_type,
        period_start=period_start,
        period_end=period_end,
        penalty_term_id=term.id if term else None,
        breach_id=breach.id if breach else None,
        inputs=result.inputs,
        formula_version=result.formula_version,
        basis_text=result.basis_text,
        amount_uncapped=result.amount_uncapped,
        amount=result.amount,
        cap_applied=result.cap_applied,
        created_by=created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(claim)
    session.flush()
    if term is not None:
        for c in session.scalars(
            select(Citation).where(
                Citation.subject_kind == "penalty_term", Citation.subject_id == term.id
            )
        ):
            session.add(
                Citation(
                    organization_id=c.organization_id,
                    contract_id=c.contract_id,
                    subject_kind="financial_claim",
                    subject_id=claim.id,
                    kind=c.kind,
                    document_id=c.document_id,
                    document_version_id=c.document_version_id,
                    page_pdf=c.page_pdf,
                    page_printed=c.page_printed,
                    clause_ref=c.clause_ref,
                    quote=c.quote,
                    quote_hash=c.quote_hash,
                    verified=c.verified,
                    label=c.label,
                )
            )
    audit.record(
        session,
        org_id=contract.organization_id,
        action=AuditAction.claim_calculated,
        actor=actor,
        object_kind="financial_claim",
        object_id=claim.id,
        object_label=f"KR-{claim.seq} {claim_type.value}",
        contract_id=contract.id,
        details={
            "amount": str(claim.amount),
            "amount_uncapped": str(claim.amount_uncapped),
            "formula_version": claim.formula_version,
        },
    )
    session.flush()
    return claim


def _set_claim_status(
    session: Session,
    claim: FinancialClaim,
    status: ClaimStatus,
    actor: audit.Actor,
    comment: str | None,
) -> None:
    old = claim.status
    claim.status = status
    claim.updated_at = datetime.now(UTC)
    if comment:
        claim.decision_comment = comment
    audit.record(
        session,
        org_id=claim.organization_id,
        action=AuditAction.claim_status_changed,
        actor=actor,
        object_kind="financial_claim",
        object_id=claim.id,
        object_label=f"KR-{claim.seq} {claim.claim_type.value}",
        contract_id=claim.contract_id,
        details={
            "before": old.value,
            "after": status.value,
            "amount": str(claim.amount),
            "comment": comment,
        },
    )


def _claim_or_error(session: Session, claim_id: uuid.UUID) -> FinancialClaim:
    claim = session.get(FinancialClaim, claim_id)
    if claim is None:
        raise FinanceError("not_found", "Kravet findes ikke", 404)
    return claim


def approve_claim(
    session: Session, *, claim_id: uuid.UUID, principal: Principal, comment: str | None
) -> FinancialClaim:
    """ADR-0013 §4: okonomi; the one who created the basis cannot approve; over
    250.000 kr. two signatures, the second a Contract Owner."""
    claim = _claim_or_error(session, claim_id)
    if not principal.can(access.OKONOMI):
        raise FinanceError("forbidden", "Kræver tilladelsen okonomi", 403)
    if claim.created_by == principal.user_id:
        raise FinanceError(
            "separation_of_duties",
            "Den, der registrerede grundlaget, kan ikke godkende kravet",
            403,
        )
    now = datetime.now(UTC)
    if claim.status == ClaimStatus.beregnet:
        claim.approved_by = principal.user_id
        claim.approved_at = now
        if claim.amount > TWO_SIGNATURE_THRESHOLD:
            _set_claim_status(
                session, claim, ClaimStatus.afventer_2_signatur, audit.human(principal), comment
            )
        else:
            _set_claim_status(session, claim, ClaimStatus.godkendt, audit.human(principal), comment)
    elif claim.status == ClaimStatus.afventer_2_signatur:
        if claim.approved_by == principal.user_id:
            raise FinanceError(
                "second_signature_same_user", "Anden signatur skal være en anden person", 403
            )
        if principal.role != MemberRole.contract_owner:
            raise FinanceError(
                "second_signature_role", "Anden signatur skal være Contract Owner", 403
            )
        claim.second_approved_by = principal.user_id
        claim.second_approved_at = now
        _set_claim_status(session, claim, ClaimStatus.godkendt, audit.human(principal), comment)
    else:
        raise FinanceError("bad_transition", f"Kravet er {claim.status.value}", 409)
    audit.record(
        session,
        org_id=claim.organization_id,
        action=AuditAction.claim_approved,
        actor=audit.human(principal),
        object_kind="financial_claim",
        object_id=claim.id,
        object_label=f"KR-{claim.seq} {claim.claim_type.value}",
        contract_id=claim.contract_id,
        details={"amount": str(claim.amount), "signature": 2 if claim.second_approved_by else 1},
    )
    session.flush()
    return claim


def submit_claim(
    session: Session, *, claim_id: uuid.UUID, principal: Principal, comment: str | None
) -> FinancialClaim:
    """`fremsat` is a separate human act (ADR-0013 afklaring 2). The system sends nothing."""
    claim = _claim_or_error(session, claim_id)
    if not principal.can(access.OKONOMI):
        raise FinanceError("forbidden", "Kræver tilladelsen okonomi", 403)
    if claim.status != ClaimStatus.godkendt:
        raise FinanceError("bad_transition", "Kun et godkendt krav kan fremsættes", 409)
    claim.submitted_by = principal.user_id
    claim.submitted_at = datetime.now(UTC)
    _set_claim_status(session, claim, ClaimStatus.fremsat, audit.human(principal), comment)
    audit.record(
        session,
        org_id=claim.organization_id,
        action=AuditAction.claim_submitted,
        actor=audit.human(principal),
        object_kind="financial_claim",
        object_id=claim.id,
        object_label=f"KR-{claim.seq} {claim.claim_type.value}",
        contract_id=claim.contract_id,
        details={"amount": str(claim.amount)},
    )
    session.flush()
    return claim


_SETTLE_FROM = {
    ClaimStatus.modregnet: (ClaimStatus.fremsat,),
    ClaimStatus.betalt: (ClaimStatus.fremsat,),
    ClaimStatus.afvist_af_leverandoer: (ClaimStatus.fremsat,),
    ClaimStatus.frafaldet: (
        ClaimStatus.beregnet,
        ClaimStatus.afventer_2_signatur,
        ClaimStatus.godkendt,
        ClaimStatus.fremsat,
        ClaimStatus.afvist_af_leverandoer,
    ),
}


def settle_claim(
    session: Session,
    *,
    claim_id: uuid.UUID,
    principal: Principal,
    status: ClaimStatus,
    comment: str | None,
) -> FinancialClaim:
    claim = _claim_or_error(session, claim_id)
    if not principal.can(access.OKONOMI):
        raise FinanceError("forbidden", "Kræver tilladelsen okonomi", 403)
    allowed = _SETTLE_FROM.get(status)
    if allowed is None or claim.status not in allowed:
        raise FinanceError(
            "bad_transition", f"Kan ikke gå fra {claim.status.value} til {status.value}", 409
        )
    if status == ClaimStatus.frafaldet and len((comment or "").strip()) < 3:
        raise FinanceError("comment_required", "Frafald kræver en begrundelse")
    _set_claim_status(session, claim, status, audit.human(principal), comment)
    session.flush()
    return claim


def recompute_claim(session: Session, *, claim_id: uuid.UUID) -> penalties.Result:
    """ADR-0013 §3: the stored inputs + term give the same number again."""
    claim = _claim_or_error(session, claim_id)
    term = session.get(PenaltyTerm, claim.penalty_term_id) if claim.penalty_term_id else None
    contract = session.get(Contract, claim.contract_id)
    if term is None or contract is None:
        raise FinanceError(
            "not_recomputable", "Kravet har ingen bodsklausul at genberegne fra", 409
        )
    return penalties.calculate(as_term(session, term, contract), claim.inputs)
