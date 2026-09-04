"""Risk Agent — reads the current agreement documents and proposes risks with a
probability, a consequence, a mitigation and a citation (ADR-0004 `create` on
subject `risk`, ADR-0005, ADR-0009 task `risk_assess`).

The model *assesses* (sandsynlighed, konsekvens, afværgelse) — that is judgement,
and it is shown as a proposal with its rationale. The score and level are
computed in code from the two numbers a human approved (ADR-0001: derived).

Idempotence (ADR-0004 §4): fingerprint = (document, clause or page, category).
"""

# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
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
    AuditAction,
    Confidence,
    Contract,
    Origin,
    Risk,
    RiskCategory,
    RiskStatus,
    SuggestionKind,
    SuggestionSubject,
)

AGENT_KEY = "risk_assess"
LABEL = "AI · Risk Agent"
TASK = "risk_assess"
SPEC = runtime.AgentSpec(AGENT_KEY, LABEL, TASK)

Category = Literal[
    "operationel",
    "gdpr",
    "kommerciel",
    "udbudsretlig",
    "compliance",
    "juridisk",
    "leverandoer",
    "andet",
]


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, uddraget stammer fra")
    page_pdf: int = Field(description="nr-attributten på den <side>, uddraget står på")
    quote: str = Field(
        description="Ordret uddrag (højst 300 tegn) af den bestemmelse eller det forhold, risikoen udspringer af"
    )


class RiskItem(BaseModel):
    title: str = Field(
        description="Kort titel på dansk, højst 100 tegn, fx 'Ensidig prisregulering uden loft'"
    )
    description: str = Field(
        description="2-3 sætninger: hvad kan gå galt for kunden, og hvorfor det følger af materialet"
    )
    category: Category = Field(
        description="operationel · gdpr · kommerciel · udbudsretlig · compliance · juridisk · leverandoer · andet"
    )
    probability: int = Field(
        ge=1, le=5, description="Sandsynlighed 1 (usandsynlig) – 5 (næsten sikker)"
    )
    consequence: int = Field(
        ge=1, le=5, description="Konsekvens 1 (ubetydelig) – 5 (kritisk) for kunden"
    )
    mitigation: str = Field(
        description="Konkret afværgehandling, som kunden kan tage, i én til to sætninger"
    )
    confidence: Literal["hoej", "mellem", "lav"] = Field(
        description="Din sikkerhed på, at dette er en reel risiko"
    )
    citation: Cite


class RiskOutput(BaseModel):
    risks: list[RiskItem] = Field(
        description="Risici for KUNDEN, som materialet giver anledning til; tom liste hvis ingen"
    )
    rationale: str = Field(
        description="Kort begrundelse på dansk: hvilke forhold vejer tungest, og hvad blev fravalgt"
    )


INSTRUCTIONS = """Du er Risk Agent i et kontraktstyringssystem for en dansk offentlig indkøber (kunden).
Du læser aftalegrundlaget for én kontrakt og identificerer risici for kunden med sandsynlighed, konsekvens og en afværgehandling.

Regler:
1. En risiko er et forhold i aftalen eller i dens fravær af bestemmelser, der kan skade kunden: økonomisk, driftsmæssigt, juridisk, udbudsretligt eller i forhold til persondata.
2. Hver risiko skal have en citation med et ORDRET uddrag fra den side, forholdet står på. Manglende bestemmelser citeres med den nærmeste relevante bestemmelse.
3. Sandsynlighed og konsekvens er din vurdering på en skala fra 1 til 5. Begrund den i description. Beregn ikke en score.
4. Afværgehandlingen skal være noget, kunden selv kan gøre: forhandle, varsle, kontrollere, dokumentere, eskalere.
5. Opfind ingen risici uden grundlag i materialet. Sæt confidence lavere, når risikoen bygger på fortolkning.
6. Skriv på dansk, neutralt og kort.
"""

QUESTION = "Identificér kundens risici i materialet ovenfor i det angivne JSON-skema."


def _execute(s: Session, run: AgentRun, contract: Contract, versions: runtime.Versions) -> None:
    result = llm.run(
        s,
        TASK,
        schema=RiskOutput,
        instructions=INSTRUCTIONS,
        material=runtime.material_for(s, versions),
        question=QUESTION,
        org_id=contract.organization_id,
        actor=audit.agent(LABEL),
        contract_id=contract.id,
        contract_label=f"{contract.reference} {contract.name}",
        agent_run_id=run.id,
    )
    runtime.record_llm(run, result)
    verifier = runtime.Verifier(s, versions)
    created = updated = 0
    for item in result.data.risks:
        cj = verifier.verify(item.citation.document_id, item.citation.page_pdf, item.citation.quote)
        conf = citations.cap(Confidence(item.confidence), all_verified=bool(cj["verified"]))
        anchor = cj.get("clause_ref") or f"s{cj.get('page_pdf')}"
        fp = suggestions.fingerprint(
            AGENT_KEY,
            contract.id,
            SuggestionSubject.risk,
            item.citation.document_id,
            str(anchor),
            item.category,
        )
        payload = {
            "title": item.title.strip()[:200],
            "description": item.description.strip(),
            "category": item.category,
            "probability": item.probability,
            "consequence": item.consequence,
            "mitigation": item.mitigation.strip(),
            "model_confidence": item.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=AGENT_KEY,
            agent_label=LABEL,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.risk,
            subject_id=None,
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
    )


# ---- materialisation (ADR-0004 §3) ----------------------------------------------------------


def next_seq(session: Session, contract_id: uuid.UUID) -> int:
    return (
        int(
            session.scalar(
                select(func.coalesce(func.max(Risk.seq), 0)).where(Risk.contract_id == contract_id)
            )
            or 0
        )
        + 1
    )


def _scale(v: object) -> int:
    if isinstance(v, bool) or not isinstance(v, int | str):
        return 3
    try:
        n = int(v)
    except ValueError:
        return 3
    return min(5, max(1, n))


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    now = datetime.now(UTC)
    category = p.get("category", "andet")
    r = Risk(
        organization_id=s.organization_id,
        contract_id=s.contract_id,
        seq=next_seq(session, s.contract_id),
        title=str(p.get("title") or "Risiko")[:200],
        description=p.get("description"),
        category=RiskCategory(category)
        if category in RiskCategory.__members__
        else RiskCategory.andet,
        probability=_scale(p.get("probability")),
        consequence=_scale(p.get("consequence")),
        status=RiskStatus.aaben,
        mitigation=p.get("mitigation"),
        origin=Origin.ai,
        suggestion_id=s.id,
        created_by=principal.user_id,
        approved_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(r)
    session.flush()
    add_citations(
        session,
        subject_kind="risk",
        subject_id=r.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.risk_created,
        actor=audit.human(principal),
        object_kind="risk",
        object_id=r.id,
        object_label=f"R-{r.seq} {r.title}",
        contract_id=s.contract_id,
        details={
            "origin": "ai",
            "suggestion_id": str(s.id),
            "score": r.probability * r.consequence,
        },
    )
    return suggestions.Materialized(materialized_id=r.id, applied=["risk"])


suggestions.MATERIALIZERS[SuggestionSubject.risk] = materialize
