"""Obligation Extraction Agent — reads the current agreement documents and
proposes one obligation per distinct duty, each with a verified citation
(ADR-0004 `create` on subject `obligation`, ADR-0005, ADR-0009 task
`obligation_extract` on the strongest model: recall is the expensive axis).

Idempotence (ADR-0004 §4): fingerprint = (document, clause or page, party,
frequency) — not the title, which the model rewords on every run. A rerun on unchanged documents updates the same open
suggestions; a rejected one is not re-proposed until the version changes (the
version id is part of the citation, and expiry runs on the switch).

Materialisation (the human's act): one `obligations` row with origin=ai and the
approver as created_by/approved_by, plus one `citations` row per source.
"""

# ruff: noqa: E501
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import llm
from app.agents import runtime, sla_terms
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
    Criticality,
    Obligation,
    ObligationFrequency,
    ObligationParty,
    ObligationStatus,
    Origin,
    SuggestionKind,
    SuggestionSubject,
)

AGENT_KEY = "obligation_extract"
LABEL = "AI · Obligation Extraction Agent"
TASK = "obligation_extract"
SPEC = runtime.AgentSpec(AGENT_KEY, LABEL, TASK)

Party = Literal["kunde", "leverandoer", "begge"]
Frequency = Literal[
    "engang", "loebende", "maanedlig", "kvartalsvis", "halvaarlig", "aarlig", "ved_haendelse"
]
Crit = Literal["lav", "mellem", "hoej", "kritisk"]


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, uddraget stammer fra")
    page_pdf: int = Field(description="nr-attributten på den <side>, uddraget står på")
    quote: str = Field(
        description="Ordret uddrag (højst 300 tegn) af den bestemmelse, der pålægger forpligtelsen"
    )


class ObligationItem(BaseModel):
    title: str = Field(
        description="Kort, handlingsorienteret titel på dansk, højst 100 tegn, fx 'Levere kvartalsvis driftsrapport'"
    )
    description: str = Field(
        description="1-2 sætninger: hvad skal gøres, af hvem, hvornår — kun det, der står i materialet"
    )
    party: Party = Field(description="Hvem er forpligtet: kunde, leverandoer eller begge")
    frequency: Frequency = Field(
        description="engang · loebende · maanedlig · kvartalsvis · halvaarlig · aarlig · ved_haendelse"
    )
    deadline: str | None = Field(
        description="Konkret frist som YYYY-MM-DD, kun hvis en dato står i materialet; ellers null"
    )
    criticality: Crit = Field(
        description="lav · mellem · hoej · kritisk — efter konsekvensen ved misligholdelse"
    )
    consequence: str | None = Field(
        description="Konsekvens ved misligholdelse som beskrevet i materialet (bod, tilbagehold, ophævelse); null hvis ingen nævnes"
    )
    confidence: Literal["hoej", "mellem", "lav"] = Field(
        description="Din sikkerhed på denne forpligtelse"
    )
    citation: Cite


class ExtractOutput(BaseModel):
    obligations: list[ObligationItem] = Field(
        description="Alle forpligtelser i materialet, én pr. selvstændig pligt"
    )
    kpis: list[sla_terms.KpiItem] = Field(
        description="Målbare mål (KPI/SLA) med operator, værdi, enhed og periode; tom liste hvis ingen"
    )
    penalty_terms: list[sla_terms.PenaltyTermItem] = Field(
        description="Bods- og service credit-klausuler som strukturerede parametre; udelad klausuler, der ikke kan udtrykkes i felterne, og nævn dem i rationale"
    )
    rationale: str = Field(
        description="Kort begrundelse på dansk: hvad var entydigt, hvad er fortolket, hvad blev udeladt og hvorfor"
    )


INSTRUCTIONS = """Du er Obligation Extraction Agent i et kontraktstyringssystem for en dansk offentlig indkøber.
Du læser aftalegrundlaget for én kontrakt og udtrækker ALLE forpligtelser, som parterne har påtaget sig.

Regler:
1. En forpligtelse er en konkret pligt til at gøre, levere, betale, rapportere, opretholde eller undlade noget. Beskrivelser af aftalens genstand, definitioner og rettigheder er ikke forpligtelser.
2. Én post pr. selvstændig pligt. Slå ikke flere pligter sammen, og opfind ingen.
3. Hver post skal have en citation med et ORDRET uddrag fra den side, bestemmelsen står på. Omskriv ikke uddraget.
4. Beregn aldrig datoer eller beløb. deadline udfyldes kun, når en konkret dato står i materialet.
5. Hellere én forpligtelse for meget end én for lidt — en overset forpligtelse koster mere end en afvist. Sæt confidence lavere, når du er i tvivl.
6. Skriv titler, beskrivelser og rationale på dansk, neutralt og kort.
7. Udtræk desuden målbare mål (KPI/SLA): navn, operator, værdi, enhed og måleperiode, med citat af den klausul, målet står i. "≥ 99,8 % pr. måned" er operator gte, værdi 99.8, enhed pct, periode maaned.
8. Udtræk bods- og service credit-klausuler som parametre: type, sats (som decimalbrøk, 5 % = 0.05), beregningsbasis, tidsenhed, evt. trappe og loft, samt hvilket mål klausulen knytter sig til. Kan en klausul ikke udtrykkes i felterne, så udelad den og skriv det i rationale — gæt aldrig.
"""

QUESTION = "Udtræk alle forpligtelser i materialet ovenfor i det angivne JSON-skema."


def _execute(s: Session, run: AgentRun, contract: Contract, versions: runtime.Versions) -> None:
    result = llm.run(
        s,
        TASK,
        schema=ExtractOutput,
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
    for item in result.data.obligations:
        cj = verifier.verify(item.citation.document_id, item.citation.page_pdf, item.citation.quote)
        conf = citations.cap(Confidence(item.confidence), all_verified=bool(cj["verified"]))
        anchor = cj.get("clause_ref") or f"s{cj.get('page_pdf')}"
        # Title wording drifts between runs; the anchor, the party and the cadence
        # do not. Keying on those keeps a rerun from re-proposing the same duty in
        # new words (ADR-0004 §4) at the price of merging two same-party, same-cadence
        # duties in one clause — the rarer failure.
        fp = suggestions.fingerprint(
            AGENT_KEY,
            contract.id,
            SuggestionSubject.obligation,
            item.citation.document_id,
            str(anchor),
            item.party,
            item.frequency,
        )
        payload = {
            "title": item.title.strip()[:200],
            "description": item.description.strip(),
            "party": item.party,
            "frequency": item.frequency,
            "deadline": item.deadline,
            "criticality": item.criticality,
            "consequence": item.consequence,
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
            subject_kind=SuggestionSubject.obligation,
            subject_id=None,
            payload=payload,
            confidence=conf,
            rationale=result.data.rationale,
            citations=[cj],
            fp=fp,
        )
        created += int(was_created)
        updated += int(not was_created)
    c2, u2 = sla_terms.emit(
        s,
        run=run,
        contract=contract,
        verifier=verifier,
        agent_key=AGENT_KEY,
        agent_label=LABEL,
        kpis=result.data.kpis,
        terms=result.data.penalty_terms,
        rationale=result.data.rationale,
    )
    run.suggestions_created = created + c2
    run.suggestions_updated = updated + u2


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
                select(func.coalesce(func.max(Obligation.seq), 0)).where(
                    Obligation.contract_id == contract_id
                )
            )
            or 0
        )
        + 1
    )


def _parse_date(v: Any) -> date | None:
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    p = s.payload
    now = datetime.now(UTC)
    o = Obligation(
        organization_id=s.organization_id,
        contract_id=s.contract_id,
        seq=next_seq(session, s.contract_id),
        title=str(p.get("title") or "Forpligtelse")[:200],
        description=p.get("description"),
        party=ObligationParty(p.get("party", "leverandoer")),
        frequency=ObligationFrequency(p.get("frequency", "engang")),
        deadline=_parse_date(p.get("deadline")),
        criticality=Criticality(p.get("criticality", "mellem")),
        status=ObligationStatus.aaben,
        consequence=p.get("consequence"),
        origin=Origin.ai,
        suggestion_id=s.id,
        created_by=principal.user_id,  # the human owns the row now (ADR-0004 §3)
        approved_by=principal.user_id,
        created_at=now,
        updated_at=now,
    )
    session.add(o)
    session.flush()
    add_citations(
        session,
        subject_kind="obligation",
        subject_id=o.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.obligation_created,
        actor=audit.human(principal),
        object_kind="obligation",
        object_id=o.id,
        object_label=f"F-{o.seq} {o.title}",
        contract_id=s.contract_id,
        details={"origin": "ai", "suggestion_id": str(s.id), "confidence": s.confidence.value},
    )
    return suggestions.Materialized(materialized_id=o.id, applied=["obligation"])


suggestions.MATERIALIZERS[SuggestionSubject.obligation] = materialize
