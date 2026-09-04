"""KPI Parse — reads an uploaded report (doc_type rapport) and proposes
measurements for the contract's KPIs (ADR-0019 §2 `document`, ADR-0009 task
`kpi_parse` on the cheapest model). Runs when a report version becomes current.

The model reads a number off a page; the code decides whether the target is met
(ADR-0019 §5) — after a human approved the number (ADR-0016: a supplier's report
is untrusted input).
"""

# ruff: noqa: E501
from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.agents import runtime
from app.ai import citations, suggestions
from app.ai.store import add_citations
from app.core import audit
from app.core.auth import Principal
from app.domain.models import (
    AgentRun,
    AgentTrigger,
    AiSuggestion,
    Confidence,
    Contract,
    DocType,
    Kpi,
    MeasurementSource,
    SuggestionKind,
    SuggestionSubject,
)
from app.finance import kpi_status, service

AGENT_KEY = "kpi_parse"
LABEL = "AI · KPI/SLA Agent"
TASK = "kpi_parse"
SPEC = runtime.AgentSpec(AGENT_KEY, LABEL, TASK)
REPORT_DOC_TYPES = frozenset({DocType.rapport})


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, tallet står i")
    page_pdf: int = Field(description="nr-attributten på den <side>, tallet står på")
    quote: str = Field(description="Ordret uddrag (højst 200 tegn) med tallet")


class MeasurementItem(BaseModel):
    kpi_id: str = Field(description="id fra <post>-blokken med kontraktens KPI'er")
    period_start: str = Field(description="Første dag i den målte periode, YYYY-MM-DD")
    value: str = Field(
        description="Den målte værdi som rent tal med punktum som decimal, i KPI'ens enhed"
    )
    confidence: Literal["hoej", "mellem", "lav"]
    citation: Cite


class ParseOutput(BaseModel):
    measurements: list[MeasurementItem] = Field(
        description="Én pr. (KPI, periode), som rapporten dokumenterer; tom liste hvis ingen"
    )
    rationale: str = Field(
        description="Kort på dansk: hvilke tal blev fundet, hvilke KPI'er rapporten ikke dækker"
    )


INSTRUCTIONS = """Du er KPI/SLA Agent i et kontraktstyringssystem for en dansk offentlig indkøber.
Du læser en leverandørrapport og finder de målte værdier for kontraktens KPI'er, som er givet i en <post>-blok med id, navn, enhed og periode.

Regler:
1. Rapportér kun tal, der står i rapporten, for KPI'er i listen. Match på navn og enhed; er du i tvivl om, hvilken KPI et tal hører til, udelad det og skriv det i rationale.
2. Hver værdi skal have en citation med et ORDRET uddrag af den sætning eller tabelrække, tallet står i.
3. Beregn intet. Gengiv tallet som det står (99,62 % → 99.62). Periodens start er første dag i den måned, det kvartal eller det år, rapporten dækker.
4. Én post pr. (KPI, periode). Dækker rapporten flere perioder, giv én post pr. periode.
5. Skriv rationale på dansk, kort.
"""

QUESTION = "Find de målte KPI-værdier i rapporten ovenfor i det angivne JSON-skema."


def _kpi_block(kpis: Sequence[Kpi]) -> llm.DataBlock:
    lines = [
        f"{k.id} | {k.name} | enhed: {k.unit.value} | periode: {k.period.value} | mål: {kpi_status.target_text(k.target_operator.value, k.target_value, k.target_value_high, k.unit.value)}"
        for k in kpis
    ]
    return llm.DataBlock(kind="post", id="kpier", label="Kontraktens KPI'er", text="\n".join(lines))


def _execute(s: Session, run: AgentRun, contract: Contract, versions: runtime.Versions) -> None:
    kpis = s.scalars(
        select(Kpi).where(Kpi.contract_id == contract.id, Kpi.active.is_(True)).order_by(Kpi.seq)
    ).all()
    if not kpis:
        run.error = "Ingen KPI'er på kontrakten — godkend målene fra aftalegrundlaget først"
        return
    material = [_kpi_block(kpis), *runtime.material_for(s, versions)]
    result = llm.run(
        s,
        TASK,
        schema=ParseOutput,
        instructions=INSTRUCTIONS,
        material=material,
        question=QUESTION,
        org_id=contract.organization_id,
        actor=audit.agent(LABEL),
        contract_id=contract.id,
        contract_label=f"{contract.reference} {contract.name}",
        agent_run_id=run.id,
    )
    runtime.record_llm(run, result)
    verifier = runtime.Verifier(s, versions)
    by_id = {str(k.id): k for k in kpis}
    created = updated = 0
    for m in result.data.measurements:
        kpi = by_id.get(m.kpi_id)
        try:
            p_start = date.fromisoformat(m.period_start)
            value = Decimal(m.value.replace(",", "."))
        except (ValueError, InvalidOperation):
            continue
        if kpi is None or not kpi_status.is_period_start(kpi.period.value, p_start):
            continue
        cj = verifier.verify(m.citation.document_id, m.citation.page_pdf, m.citation.quote)
        conf = citations.cap(Confidence(m.confidence), all_verified=bool(cj["verified"]))
        fp = suggestions.fingerprint(
            AGENT_KEY,
            contract.id,
            SuggestionSubject.kpi_measurement,
            str(kpi.id),
            p_start.isoformat(),
        )
        payload = {
            "kpi_id": str(kpi.id),
            "kpi_name": kpi.name,
            "unit": kpi.unit.value,
            "period_start": p_start.isoformat(),
            "period_end": kpi_status.period_end(kpi.period.value, p_start).isoformat(),
            "value": str(value),
            "target_text": kpi_status.target_text(
                kpi.target_operator.value, kpi.target_value, kpi.target_value_high, kpi.unit.value
            ),
            "model_confidence": m.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=AGENT_KEY,
            agent_label=LABEL,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.kpi_measurement,
            subject_id=kpi.id,
            payload=payload,
            confidence=conf,
            rationale=result.data.rationale,
            citations=[cj],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    run.suggestions_created = created
    run.suggestions_updated = updated


EXECUTE = _execute  # the org runner (app/jobs/runs.py) drives agents generically
DOC_TYPES = REPORT_DOC_TYPES


def run_for_contract(
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    trigger: AgentTrigger,
    trigger_ref: str | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    return runtime.run_for_contract(
        SPEC,
        _execute,
        org_id=org_id,
        contract_id=contract_id,
        trigger=trigger,
        trigger_ref=trigger_ref,
        triggered_by=triggered_by,
        doc_types=REPORT_DOC_TYPES,
    )


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    kpi = session.get(Kpi, uuid.UUID(str(p["kpi_id"])))
    if kpi is None:
        raise suggestions.SuggestionError("not_found", "KPI'en findes ikke længere", 404)
    try:
        m, breach, claim = service.record_measurement(
            session,
            kpi=kpi,
            period_start=date.fromisoformat(str(p["period_start"])),
            value=Decimal(str(p["value"])),
            source_kind=MeasurementSource.document,
            actor=audit.human(principal),
            actor_id=principal.user_id,
            note=f"fra rapport (AI-forslag godkendt): {s.rationale[:200]}"
            if s.rationale
            else "fra rapport",
            suggestion_id=s.id,
        )
    except service.FinanceError as e:
        raise suggestions.SuggestionError(e.code, str(e), e.status) from e
    add_citations(
        session,
        subject_kind="kpi_measurement",
        subject_id=m.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    note = ""
    if breach is not None:
        note = "SLA-brud registreret" + (
            f", krav KR-{claim.seq} beregnet ({claim.amount} kr.)" if claim else f" ({breach.note})"
        )
    return suggestions.Materialized(materialized_id=m.id, applied=["kpi_measurement"], note=note)


suggestions.MATERIALIZERS[SuggestionSubject.kpi_measurement] = materialize
