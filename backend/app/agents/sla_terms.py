"""KPI targets and penalty-term parameters as proposals (ADR-0019 §1, ADR-0013 §1).

Extracted in the same pass as obligations (the Obligation Extraction Agent's
schema carries `kpis` and `penalty_terms`), proposed as `create` suggestions of
subject `kpi` (kontrakt_red approves) and `penalty_term` (okonomi approves —
money parameters). A clause the model cannot express in the enums is not
proposed: the pydantic Literals refuse it, and the model is told to name it in
the rationale instead.
"""

# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents import runtime
from app.ai import citations, suggestions
from app.ai.store import add_citations
from app.core import audit
from app.core.auth import Principal
from app.domain.models import (
    AgentRun,
    AiSuggestion,
    AuditAction,
    Confidence,
    Contract,
    Kpi,
    KpiPeriod,
    KpiUnit,
    Origin,
    PenaltyBasis,
    PenaltyTerm,
    PenaltyTimeUnit,
    PriceTerm,
    SuggestionKind,
    SuggestionSubject,
    TargetOperator,
    TermStatus,
    TermType,
)
from app.finance import kpi_status


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, uddraget stammer fra")
    page_pdf: int = Field(description="nr-attributten på den <side>, uddraget står på")
    quote: str = Field(description="Ordret uddrag (højst 300 tegn) af klausulen")


class KpiItem(BaseModel):
    name: str = Field(description="Kort navn, fx 'Oppetid, kritiske systemer'")
    unit: Literal["pct", "antal", "timer", "dkk", "score"]
    target_operator: Literal["gte", "lte", "eq", "between"]
    target_value: str = Field(description="Målværdi som rent tal med punktum som decimal, fx 99.8")
    target_value_high: str | None = Field(description="Øvre grænse, kun ved between")
    period: Literal["maaned", "kvartal", "halvaar", "aar"] = Field(
        description="Måleperioden, som klausulen angiver"
    )
    confidence: Literal["hoej", "mellem", "lav"]
    citation: Cite


class Tier(BaseModel):
    below: str = Field(description="Tærskel: satsen gælder for målinger UNDER denne værdi, fx 99.5")
    rate: str = Field(description="Sats som decimalbrøk, fx 0.10 for 10 %")


class PenaltyTermItem(BaseModel):
    name: str = Field(description="Kort navn, fx 'Service credit ved manglende oppetid'")
    term_type: Literal[
        "service_credit_pct_of_fee",
        "service_credit_tiered",
        "delivery_penalty_per_week",
        "fixed_penalty_per_breach",
    ]
    trigger_description: str = Field(
        description="Hvornår klausulen udløses, som skrevet, fx 'oppetid under 99,8 %'"
    )
    applies_to_kpi: str | None = Field(
        description="Navnet på det mål (fra kpis), klausulen knytter sig til; null hvis ingen"
    )
    rate: str | None = Field(
        description="Sats som decimalbrøk (5 % = 0.05); null ved trappe eller fast beløb"
    )
    tiers: list[Tier] | None = Field(
        description="Trappe af (tærskel, sats), kun ved service_credit_tiered"
    )
    basis: Literal[
        "maanedligt_driftsvederlag",
        "aarligt_vederlag",
        "vaerdi_ikke_leverede_ordrelinjer",
        "maanedens_omsaetning",
        "fast_beloeb",
    ]
    basis_amount: str | None = Field(
        description="Beløbet, satsen beregnes af, som rent tal — kun hvis det står i materialet (fx 612500.00); ellers null"
    )
    time_unit: Literal["maaned", "paabegyndt_uge", "dag", "haendelse"]
    cap_rate: str | None = Field(
        description="Loft som decimalbrøk af månedens omsætning, fx 0.15; null hvis intet loft"
    )
    cap_amount: str | None = Field(description="Loft som fast beløb, hvis angivet; ellers null")
    confidence: Literal["hoej", "mellem", "lav"]
    citation: Cite


class PriceItem(BaseModel):
    product_ref: str | None = Field(
        description="Varenummer/produktreference, hvis prisbilaget har en; ellers null"
    )
    description: str = Field(
        description="Ydelsen eller varen som skrevet, fx 'Farmaceuttimer, dagtimer'"
    )
    unit: str | None = Field(description="Enhed, fx 'time', 'pakning', 'stk'")
    agreed_unit_price: str = Field(
        description="Aftalt enhedspris i DKK ekskl. moms som rent tal, fx 545.00"
    )
    valid_from: str | None = Field(description="Gyldig fra, YYYY-MM-DD, hvis angivet")
    valid_to: str | None = Field(description="Gyldig til, YYYY-MM-DD, hvis angivet")
    confidence: Literal["hoej", "mellem", "lav"]
    citation: Cite


def _date(v: Any) -> date | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v).replace(",", "."))
    except InvalidOperation:
        return None


def emit(
    s: Session,
    *,
    run: AgentRun,
    contract: Contract,
    verifier: runtime.Verifier,
    agent_key: str,
    agent_label: str,
    kpis: list[KpiItem],
    terms: list[PenaltyTermItem],
    rationale: str,
    prices: list[PriceItem] | None = None,
) -> tuple[int, int]:
    created = updated = 0
    for k in kpis:
        if _dec(k.target_value) is None:
            continue
        cj = verifier.verify(k.citation.document_id, k.citation.page_pdf, k.citation.quote)
        conf = citations.cap(Confidence(k.confidence), all_verified=bool(cj["verified"]))
        anchor = cj.get("clause_ref") or f"s{cj.get('page_pdf')}"
        fp = suggestions.fingerprint(
            agent_key,
            contract.id,
            SuggestionSubject.kpi,
            k.citation.document_id,
            str(anchor),
            k.unit,
            k.period,
        )
        payload: dict[str, Any] = {
            "name": k.name.strip()[:200],
            "unit": k.unit,
            "target_operator": k.target_operator,
            "target_value": k.target_value,
            "target_value_high": k.target_value_high,
            "period": k.period,
            "target_text": kpi_status.target_text(
                k.target_operator,
                _dec(k.target_value) or Decimal(0),
                _dec(k.target_value_high),
                k.unit,
            ),
            "model_confidence": k.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=agent_key,
            agent_label=agent_label,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.kpi,
            subject_id=None,
            payload=payload,
            confidence=conf,
            rationale=rationale,
            citations=[cj],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    for t in terms:
        cj = verifier.verify(t.citation.document_id, t.citation.page_pdf, t.citation.quote)
        conf = citations.cap(Confidence(t.confidence), all_verified=bool(cj["verified"]))
        anchor = cj.get("clause_ref") or f"s{cj.get('page_pdf')}"
        fp = suggestions.fingerprint(
            agent_key,
            contract.id,
            SuggestionSubject.penalty_term,
            t.citation.document_id,
            str(anchor),
            t.term_type,
        )
        payload = {
            "name": t.name.strip()[:200],
            "term_type": t.term_type,
            "trigger_description": t.trigger_description,
            "applies_to": t.applies_to_kpi,
            "rate": t.rate,
            "tiers": [x.model_dump() for x in (t.tiers or [])] or None,
            "basis": t.basis,
            "basis_amount": t.basis_amount,
            "time_unit": t.time_unit,
            "cap_rate": t.cap_rate,
            "cap_amount": t.cap_amount,
            "model_confidence": t.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=agent_key,
            agent_label=agent_label,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.penalty_term,
            subject_id=None,
            payload=payload,
            confidence=conf,
            rationale=rationale,
            citations=[cj],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    for pr in prices or []:
        if _dec(pr.agreed_unit_price) is None:
            continue
        cj = verifier.verify(pr.citation.document_id, pr.citation.page_pdf, pr.citation.quote)
        conf = citations.cap(Confidence(pr.confidence), all_verified=bool(cj["verified"]))
        anchor = cj.get("clause_ref") or f"s{cj.get('page_pdf')}"
        key = (pr.product_ref or pr.description).strip().lower()[:60]
        fp = suggestions.fingerprint(
            agent_key,
            contract.id,
            SuggestionSubject.price_term,
            pr.citation.document_id,
            str(anchor),
            key,
        )
        payload_p: dict[str, Any] = {
            "product_ref": pr.product_ref,
            "description": pr.description.strip()[:200],
            "unit": pr.unit,
            "agreed_unit_price": pr.agreed_unit_price,
            "valid_from": pr.valid_from,
            "valid_to": pr.valid_to,
            "model_confidence": pr.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=agent_key,
            agent_label=agent_label,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.price_term,
            subject_id=None,
            payload=payload_p,
            confidence=conf,
            rationale=rationale,
            citations=[cj],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    return created, updated


# ---- materialisation ----------------------------------------------------------------------------


def _next_seq(session: Session, model: type[Any], contract_id: uuid.UUID) -> int:
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


def link_by_name(session: Session, contract_id: uuid.UUID) -> None:
    """A term that names a KPI is attached to it once both exist (either order)."""
    kpis = session.scalars(
        select(Kpi).where(Kpi.contract_id == contract_id, Kpi.penalty_term_id.is_(None))
    ).all()
    terms = session.scalars(
        select(PenaltyTerm).where(
            PenaltyTerm.contract_id == contract_id, PenaltyTerm.applies_to.is_not(None)
        )
    ).all()
    for term in terms:
        want = (term.applies_to or "").strip().lower()
        for kpi in kpis:
            if kpi.penalty_term_id is None and kpi.name.strip().lower() == want:
                kpi.penalty_term_id = term.id
                kpi.updated_at = datetime.now(UTC)
    session.flush()


def materialize_kpi(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    target = _dec(p.get("target_value"))
    if target is None:
        raise suggestions.SuggestionError("bad_payload", "Målværdien kan ikke læses som tal")
    now = datetime.now(UTC)
    unit = KpiUnit(p.get("unit", "pct"))
    kpi = Kpi(
        organization_id=s.organization_id,
        contract_id=s.contract_id,
        seq=_next_seq(session, Kpi, s.contract_id),
        name=str(p.get("name") or "KPI")[:200],
        unit=unit,
        target_operator=TargetOperator(p.get("target_operator", "gte")),
        target_value=target,
        target_value_high=_dec(p.get("target_value_high")),
        period=KpiPeriod(p.get("period", "maaned")),
        warn_band=kpi_status.default_warn_band(unit.value, target),
        origin=Origin.ai,
        suggestion_id=s.id,
        created_by=principal.user_id,
        approved_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(kpi)
    session.flush()
    add_citations(
        session,
        subject_kind="kpi",
        subject_id=kpi.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    link_by_name(session, s.contract_id)
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.kpi_created,
        actor=audit.human(principal),
        object_kind="kpi",
        object_id=kpi.id,
        object_label=f"K-{kpi.seq} {kpi.name}",
        contract_id=s.contract_id,
        details={"origin": "ai", "suggestion_id": str(s.id), "target": str(target)},
    )
    return suggestions.Materialized(materialized_id=kpi.id, applied=["kpi"])


def materialize_term(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    now = datetime.now(UTC)
    version_id = None
    for c in s.citations:
        if c.get("document_version_id"):
            version_id = uuid.UUID(c["document_version_id"])
            break
    term = PenaltyTerm(
        organization_id=s.organization_id,
        contract_id=s.contract_id,
        seq=_next_seq(session, PenaltyTerm, s.contract_id),
        name=str(p.get("name") or "Bodsklausul")[:200],
        term_type=TermType(p.get("term_type", "fixed_penalty_per_breach")),
        trigger_description=p.get("trigger_description"),
        applies_to=p.get("applies_to"),
        rate=_dec(p.get("rate")),
        tiers=p.get("tiers"),
        basis=PenaltyBasis(p.get("basis", "fast_beloeb")),
        basis_amount=_dec(p.get("basis_amount")),
        time_unit=PenaltyTimeUnit(p.get("time_unit", "haendelse")),
        cap_rate=_dec(p.get("cap_rate")),
        cap_amount=_dec(p.get("cap_amount")),
        document_version_id=version_id,
        status=TermStatus.aktiv,
        origin=Origin.ai,
        suggestion_id=s.id,
        created_by=principal.user_id,
        approved_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(term)
    session.flush()
    add_citations(
        session,
        subject_kind="penalty_term",
        subject_id=term.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    link_by_name(session, s.contract_id)
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.penalty_term_created,
        actor=audit.human(principal),
        object_kind="penalty_term",
        object_id=term.id,
        object_label=f"B-{term.seq} {term.name}",
        contract_id=s.contract_id,
        details={
            "origin": "ai",
            "suggestion_id": str(s.id),
            "term_type": term.term_type.value,
            "rate": str(term.rate),
        },
    )
    return suggestions.Materialized(materialized_id=term.id, applied=["penalty_term"])


def materialize_price(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    price = _dec(p.get("agreed_unit_price"))
    if price is None:
        raise suggestions.SuggestionError("bad_payload", "Prisen kan ikke læses som tal")
    row = PriceTerm(
        organization_id=s.organization_id,
        contract_id=s.contract_id,
        product_ref=p.get("product_ref"),
        description=str(p.get("description") or "Ydelse")[:200],
        unit=p.get("unit"),
        agreed_unit_price=price,
        valid_from=_date(p.get("valid_from")),
        valid_to=_date(p.get("valid_to")),
        origin=Origin.ai,
        suggestion_id=s.id,
        created_by=principal.user_id,
        created_at=datetime.now(UTC),
    )
    session.add(row)
    session.flush()
    add_citations(
        session,
        subject_kind="price_term",
        subject_id=row.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.price_term_created,
        actor=audit.human(principal),
        object_kind="price_term",
        object_id=row.id,
        object_label=row.description,
        contract_id=s.contract_id,
        details={"origin": "ai", "suggestion_id": str(s.id), "agreed_unit_price": str(price)},
    )
    return suggestions.Materialized(materialized_id=row.id, applied=["price_term"])


suggestions.MATERIALIZERS[SuggestionSubject.kpi] = materialize_kpi
suggestions.MATERIALIZERS[SuggestionSubject.price_term] = materialize_price
suggestions.MATERIALIZERS[SuggestionSubject.penalty_term] = materialize_term
