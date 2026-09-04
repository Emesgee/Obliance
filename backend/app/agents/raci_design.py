"""RACI Design Agent — proposes activities with a function distribution from the
templates for the contract's tier/agreement form and from clauses that oblige the
customer (ADR-0021 §4, ADR-0009 task raci_design). Never a person: people are
staffed by humans in contract_roles.

Materialisation validates ADR-0021 §1 (exactly one A, at least one R, LEV never A)
— a proposal that breaks it cannot be approved without correction.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field
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
    Criticality,
    Origin,
    SuggestionKind,
    SuggestionSubject,
)
from app.raci import service

AGENT_KEY = "raci_design"
LABEL = "AI · RACI Design Agent"
TASK = "raci_design"
SPEC = runtime.AgentSpec(AGENT_KEY, LABEL, TASK)

Function = Literal["CM", "CO", "PROC", "LEGAL", "FIN", "IT", "BUS", "LEV"]
Letter = Literal["R", "A", "C", "I"]


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, uddraget stammer fra")
    page_pdf: int = Field(description="nr-attributten på den <side>, uddraget står på")
    quote: str = Field(
        description="Ordret uddrag (højst 300 tegn) af den bestemmelse, der pålægger kunden noget"
    )


class Cell(BaseModel):
    function: Function
    letter: Letter


class Activity(BaseModel):
    name: str = Field(description="Handlingsorienteret aktivitetsnavn på dansk, højst 100 tegn")
    criticality: Literal["lav", "mellem", "hoej", "kritisk"]
    template_key: str | None = Field(
        description="key fra <post>-blokken, hvis aktiviteten kommer fra en skabelon; ellers null"
    )
    cells: list[Cell] = Field(
        description="Fordeling: præcis ét A, mindst ét R, LEV aldrig A, højst ét bogstav pr. funktion"
    )
    confidence: Literal["hoej", "mellem", "lav"]
    citation: Cite | None = Field(
        description="Klausulen, aktiviteten udspringer af; null for skabelonaktiviteter"
    )


class RaciOutput(BaseModel):
    activities: list[Activity]
    rationale: str = Field(
        description="Kort begrundelse på dansk: hvilke skabeloner blev brugt, hvilke klausuler tilføjede aktiviteter, hvad blev fravalgt"
    )


INSTRUCTIONS = """Du er RACI Design Agent i et kontraktstyringssystem for en dansk offentlig indkøber (kunden).
Du foreslår kontraktens ansvarsmatrix: aktiviteter og fordelingen af R (udfører), A (ansvarlig), C (høres), I (informeres) på otte funktioner:
CM (Contract Manager), CO (Contract Owner), PROC (Procurement), LEGAL (Legal & Compliance), FIN (Finance Controller), IT, BUS (forretningen), LEV (leverandøren).

Regler:
1. Start med skabelonerne i <post>-blokken: de gælder for kontraktens niveau og aftaleform. Medtag dem med template_key og deres default-fordeling, justeret hvis materialet giver grund til det.
2. Tilføj aktiviteter for klausuler, der pålægger KUNDEN at varsle, godkende, kontrollere, beslutte eller rapportere — hver med citat af klausulen.
3. Præcis ét A pr. aktivitet. Mindst ét R. LEV kan være R, C eller I, aldrig A. Højst ét bogstav pr. funktion.
4. Foreslå aldrig personer — kun funktioner.
5. Skriv navne og rationale på dansk, neutralt og kort.
"""

QUESTION = "Foreslå kontraktens RACI-aktiviteter ud fra skabelonerne og materialet ovenfor."

_NORM = re.compile(r"[^a-z0-9æøå]+")


def _template_block(session: Session, contract: Contract) -> llm.DataBlock:
    lines = [
        f"{t.key} | {t.name} | kritikalitet: {t.criticality.value} | default: "
        + ", ".join(f"{k}={v}" for k, v in t.assignments.items())
        for t in service.templates_for(session, contract)
    ]
    tier = contract.tier.value if contract.tier else "ukendt"
    form = contract.agreement_form.value if contract.agreement_form else "ukendt"
    return llm.DataBlock(
        kind="post",
        id="skabeloner",
        label=f"RACI-skabeloner for niveau {tier}, aftaleform {form}",
        text="\n".join(lines) or "(ingen skabeloner matcher)",
    )


def _execute(s: Session, run: AgentRun, contract: Contract, versions: runtime.Versions) -> None:
    material = [_template_block(s, contract), *runtime.material_for(s, versions)]
    result = llm.run(
        s,
        TASK,
        schema=RaciOutput,
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
    created = updated = 0
    for a in result.data.activities:
        cells: dict[str, str] = {str(c.function): str(c.letter) for c in a.cells}
        errors = service.validate(cells)
        cj = (
            verifier.verify(a.citation.document_id, a.citation.page_pdf, a.citation.quote)
            if a.citation
            else None
        )
        conf = citations.cap(
            Confidence(a.confidence), all_verified=(cj is None or bool(cj["verified"]))
        )
        if errors:
            conf = Confidence.lav
        key = a.template_key or _NORM.sub(" ", a.name.lower()).strip()[:60]
        anchor = (cj.get("clause_ref") or f"s{cj.get('page_pdf')}") if cj else "template"
        fp = suggestions.fingerprint(
            AGENT_KEY, contract.id, SuggestionSubject.raci_entry, str(anchor), key
        )
        payload: dict[str, Any] = {
            "name": a.name.strip()[:200],
            "criticality": a.criticality,
            "template_key": a.template_key,
            "cells": cells,
            "validation_errors": errors,
            "model_confidence": a.confidence,
        }
        _, was_created = suggestions.upsert(
            s,
            org_id=contract.organization_id,
            contract_id=contract.id,
            agent_key=AGENT_KEY,
            agent_label=LABEL,
            agent_run_id=run.id,
            kind=SuggestionKind.create,
            subject_kind=SuggestionSubject.raci_entry,
            subject_id=None,
            payload=payload,
            confidence=conf,
            rationale=result.data.rationale,
            # ADR-0005: a template activity cites the governance model as a record
            citations=[cj]
            if cj
            else [
                {
                    "kind": "record",
                    "record_kind": "raci_template",
                    "record_id": None,
                    "label": f"Skabelon: {a.template_key or 'governance'}",
                    "verified": True,
                }
            ],
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


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    contract = session.get(Contract, s.contract_id)
    if contract is None:
        raise suggestions.SuggestionError("not_found", "Kontrakten findes ikke", 404)
    p = s.payload
    cells = {str(k): str(v) for k, v in (p.get("cells") or {}).items()}
    try:
        act = service.create_activity(
            session,
            contract=contract,
            name=str(p.get("name") or "Aktivitet"),
            criticality=Criticality(p.get("criticality", "mellem")),
            cells=cells,
            actor=audit.human(principal),
            actor_id=principal.user_id,
            origin=Origin.ai,
            template_key=p.get("template_key"),
            suggestion_id=s.id,
        )
    except service.RaciError as e:
        raise suggestions.SuggestionError(e.code, f"Matricen er ugyldig: {e}", e.status) from e
    add_citations(
        session,
        subject_kind="raci_activity",
        subject_id=act.id,
        org_id=s.organization_id,
        contract_id=s.contract_id,
        cites=s.citations,
    )
    return suggestions.Materialized(materialized_id=act.id, applied=["raci_activity"])


suggestions.MATERIALIZERS[SuggestionSubject.raci_entry] = materialize
