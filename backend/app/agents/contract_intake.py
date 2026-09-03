"""Contract Intake Agent — reads the current agreement documents and proposes
master data for the contract (ADR-0004 subject `contract_intake`, ADR-0009 task
`contract_intake`, ADR-0010 event/manual trigger).

What it may do: write ONE update-suggestion per (contract, set of current
agreement versions). What it may not do: touch `contracts`. The approving human
does that, fill-only (bidflow ADR-0024): a field a person already filled is never
overwritten — the proposal is shown next to it instead.
"""

# ruff: noqa: E501  — prompt text and field descriptions read better unwrapped
from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.ai import citations, suggestions
from app.core import audit
from app.core.auth import Principal
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import (
    AGREEMENT_DOC_TYPES,
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AgentTrigger,
    AgreementForm,
    AiSuggestion,
    AuditAction,
    Confidence,
    Contract,
    ContractDocument,
    ContractStatus,
    DocumentClause,
    DocumentPage,
    DocumentVersion,
    IngestStatus,
    SuggestionKind,
    SuggestionSubject,
)

log = logging.getLogger(__name__)

AGENT_KEY = "contract_intake"
LABEL = "AI · Contract Intake Agent"
TASK = "contract_intake"


# ---- the schema the model must fill (ADR-0009 §3: strict, validated) -------------------------


class Cite(BaseModel):
    document_id: str = Field(description="id-attributten på det <dokument>, uddraget stammer fra")
    page_pdf: int = Field(description="nr-attributten på den <side>, uddraget står på")
    quote: str = Field(description="Ordret uddrag (højst 300 tegn), der underbygger værdien")


class StrField(BaseModel):
    value: str | None = Field(description="Værdien, eller null hvis den ikke fremgår af materialet")
    citation: Cite | None = Field(description="Kilden; null kun når value er null")


class Option(BaseModel):
    description: str = Field(
        description="Kort beskrivelse af optionen, fx 'Forlængelse 2 × 12 måneder'"
    )
    months: int | None = Field(description="Varighed i måneder, hvis angivet")
    citation: Cite | None


class IntakeOutput(BaseModel):
    name: StrField = Field(description="Kontraktens titel/navn som skrevet i dokumentet")
    contract_number: StrField = Field(
        description="Kontrakt-/journalnummer, hvis det står i dokumentet"
    )
    agreement_form: StrField = Field(
        description="Én af: serviceaftale, rammeaftale, leveringsaftale, databehandleraftale, andet"
    )
    category: StrField = Field(
        description="Ydelseskategori, fx 'IT-drift', 'Lægemidler', 'Transport'"
    )
    description: StrField = Field(
        description="2-3 sætningers neutral beskrivelse af aftalens genstand"
    )
    start_date: StrField = Field(description="Ikrafttrædelsesdato som YYYY-MM-DD")
    end_date: StrField = Field(description="Udløbsdato som YYYY-MM-DD (uden optioner)")
    notice_period_months: StrField = Field(description="Opsigelsesvarsel i hele måneder, som tal")
    last_termination_date: StrField = Field(
        description="Sidste opsigelsesdato som YYYY-MM-DD, hvis angivet"
    )
    options: list[Option] = Field(description="Forlængelsesoptioner; tom liste hvis ingen")
    price_regulation: StrField = Field(
        description="Prisreguleringsklausul i én sætning, fx 'Nettoprisindeks, årligt pr. 1. januar'"
    )
    total_value_dkk: StrField = Field(
        description="Samlet kontraktværdi i DKK som rent tal med punktum som decimal, fx 24500000.00 — kun hvis beløbet står i materialet"
    )
    annual_value_dkk: StrField = Field(
        description="Årlig værdi i DKK som rent tal, kun hvis beløbet står i materialet"
    )
    confidence: Literal["hoej", "mellem", "lav"] = Field(description="Din samlede sikkerhed")
    rationale: str = Field(
        description="Kort begrundelse på dansk: hvad var entydigt, hvad er fortolket"
    )


INSTRUCTIONS = """Du er Contract Intake Agent i et kontraktstyringssystem for en dansk offentlig indkøber.
Du læser aftalegrundlaget for én kontrakt og udfylder stamdata i det angivne JSON-skema.

Regler:
1. Skriv kun værdier, der står i materialet. Fremgår en værdi ikke, er value null og citation null.
2. Hver værdi skal have en citation med et ORDRET uddrag fra den side, værdien står på. Omskriv ikke uddraget.
3. Beregn aldrig beløb, datoer eller perioder. Gengiv det, der står. Står der "3 måneder", er notice_period_months "3".
4. Datoer skrives YYYY-MM-DD. Beløb skrives som rent tal med punktum som decimaltegn og uden valuta.
5. agreement_form er én af: serviceaftale, rammeaftale, leveringsaftale, databehandleraftale, andet.
6. Skriv rationale og description på dansk, neutralt og kort.
"""

QUESTION = "Udfyld skemaet med kontraktens stamdata ud fra materialet ovenfor."

# ---- run -------------------------------------------------------------------------------------


def _pages_for(session: Session, version_id: uuid.UUID) -> list[llm.PageBlock]:
    rows = session.scalars(
        select(DocumentPage)
        .where(DocumentPage.version_id == version_id)
        .order_by(DocumentPage.page_pdf)
    ).all()
    return [llm.PageBlock(p.page_pdf, p.page_printed, p.text) for p in rows]


def _agreement_versions(
    session: Session, contract_id: uuid.UUID
) -> list[tuple[ContractDocument, DocumentVersion]]:
    docs = session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.contract_id == contract_id,
            ContractDocument.doc_type.in_(list(AGREEMENT_DOC_TYPES)),
            ContractDocument.current_version_id.is_not(None),
        )
        .order_by(ContractDocument.created_at)
    ).all()
    out: list[tuple[ContractDocument, DocumentVersion]] = []
    for d in docs:
        v = session.get(DocumentVersion, d.current_version_id)
        if v is not None and v.ingest_status == IngestStatus.ok:
            out.append((d, v))
    return out


def _snapshot(c: Contract) -> dict[str, Any]:
    return {
        "name": c.name,
        "contract_number": c.contract_number,
        "agreement_form": c.agreement_form.value if c.agreement_form else None,
        "category": c.category,
        "description": c.description,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "notice_period_days": c.notice_period.days if c.notice_period else None,
        "last_termination_date": c.last_termination_date.isoformat()
        if c.last_termination_date
        else None,
        "options": c.options,
        "price_regulation": c.price_regulation,
        "total_value": str(c.total_value) if c.total_value is not None else None,
        "annual_value": str(c.annual_value) if c.annual_value is not None else None,
    }


FIELD_NAMES = [
    "name",
    "contract_number",
    "agreement_form",
    "category",
    "description",
    "start_date",
    "end_date",
    "notice_period_months",
    "last_termination_date",
    "price_regulation",
    "total_value_dkk",
    "annual_value_dkk",
]


def _build_payload(
    session: Session,
    contract: Contract,
    out: IntakeOutput,
    versions: list[tuple[ContractDocument, DocumentVersion]],
) -> tuple[dict[str, Any], list[dict[str, Any]], Confidence]:
    by_doc = {str(d.id): (d, v) for d, v in versions}
    page_cache: dict[uuid.UUID, tuple[list[citations.Page], list[citations.Clause]]] = {}

    def verify(cite: Cite | None) -> dict[str, Any] | None:
        if cite is None:
            return None
        hit = by_doc.get(cite.document_id)
        if hit is None:
            return {
                "kind": "document",
                "document_id": cite.document_id,
                "quote": cite.quote,
                "verified": False,
                "label": "ukendt dokument",
                "page_pdf": cite.page_pdf,
                "document_version_id": None,
                "page_printed": None,
                "clause_ref": None,
            }
        d, v = hit
        if v.id not in page_cache:
            pages = [
                citations.Page(p.page_pdf, p.page_printed, p.text)
                for p in session.scalars(
                    select(DocumentPage).where(DocumentPage.version_id == v.id)
                )
            ]
            clauses = [
                citations.Clause(c.clause_ref, c.page_pdf, c.char_start)
                for c in session.scalars(
                    select(DocumentClause).where(DocumentClause.version_id == v.id)
                )
            ]
            page_cache[v.id] = (pages, clauses)
        pages, clauses = page_cache[v.id]
        loc = citations.locate(pages, clauses, cite.quote, cite.page_pdf)
        return citations.citation_json(
            document_id=d.id,
            document_version_id=v.id,
            doc_title=d.title,
            quote=cite.quote,
            located=loc,
        )

    fields: dict[str, Any] = {}
    all_cites: list[dict[str, Any]] = []
    all_verified = True
    for name in FIELD_NAMES:
        f: StrField = getattr(out, name)
        if f.value is None:
            continue
        cj = verify(f.citation)
        verified = bool(cj and cj["verified"])
        all_verified &= verified
        fields[name] = {"value": f.value, "citation": cj, "verified": verified}
        if cj:
            all_cites.append(cj)
    options: list[dict[str, Any]] = []
    for o in out.options:
        cj = verify(o.citation)
        verified = bool(cj and cj["verified"])
        all_verified &= verified
        options.append(
            {"description": o.description, "months": o.months, "citation": cj, "verified": verified}
        )
        if cj:
            all_cites.append(cj)
    payload = {
        "fields": fields,
        "options": options,
        "before": _snapshot(contract),
        "model_confidence": out.confidence,
        "basis_version_ids": sorted(str(v.id) for _, v in versions),
    }
    conf = citations.cap(Confidence(out.confidence), all_verified=all_verified)
    return payload, all_cites, conf


def run_for_contract(
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    trigger: AgentTrigger,
    trigger_ref: str | None = None,
    triggered_by: uuid.UUID | None = None,
) -> uuid.UUID:
    """Entry point for jobs. Own session, system context (ADR-0002), own run row.
    Never raises: the outcome is the agent_runs row (bidflow ADR-0054)."""
    started = datetime.now(UTC)
    with tenant(org_id, system=True), SessionLocal() as s:
        run = AgentRun(
            organization_id=org_id,
            agent_key=AGENT_KEY,
            contract_id=contract_id,
            trigger=trigger,
            trigger_ref=trigger_ref,
            triggered_by=triggered_by,
            task=TASK,
            started_at=started,
        )
        s.add(run)
        s.commit()
        try:
            _execute(s, run, org_id, contract_id)
        except llm.LlmBudgetExceeded as e:
            run.status = AgentRunStatus.sprunget_over
            run.error = str(e)
            run.error_context = {"reason": "budget"}
        except Exception as e:  # noqa: BLE001 — the run row is the report
            log.exception("contract_intake failed contract=%s", contract_id)
            run.status = AgentRunStatus.fejlet
            run.error = f"{e.__class__.__name__}: {e}"[:1000]
            run.error_context = {"code": getattr(e, "code", None)}
            audit.record(
                s,
                org_id=org_id,
                action=AuditAction.agent_run_failed,
                actor=audit.agent(LABEL),
                object_kind="agent_run",
                object_id=run.id,
                contract_id=contract_id,
                details={"error": run.error},
                agent_run_id=run.id,
            )
        finally:
            finished = datetime.now(UTC)
            run.finished_at = finished
            run.duration_ms = int((finished - started).total_seconds() * 1000)
            s.commit()
        return run.id


def _execute(s: Session, run: AgentRun, org_id: uuid.UUID, contract_id: uuid.UUID) -> None:
    setting = s.get(AgentSetting, (org_id, AGENT_KEY))
    if setting is not None and not setting.enabled:
        run.status = AgentRunStatus.sprunget_over
        run.error_context = {"reason": "disabled"}
        return
    contract = s.get(Contract, contract_id)
    if contract is None:
        run.status = AgentRunStatus.fejlet
        run.error = "Kontrakten findes ikke"
        return
    versions = _agreement_versions(s, contract_id)
    run.contracts_scanned = 1
    if not versions:
        run.status = AgentRunStatus.sprunget_over
        run.error = "Intet aftalegrundlag: upload en hovedkontrakt, et bilag eller et tillæg"
        run.error_context = {"reason": "no_agreement_documents"}
        return

    material = [
        llm.DataBlock(kind="dokument", id=str(d.id), label=d.title, pages=_pages_for(s, v.id))
        for d, v in versions
    ]
    result = llm.run(
        s,
        TASK,
        schema=IntakeOutput,
        instructions=INSTRUCTIONS,
        material=material,
        question=QUESTION,
        org_id=org_id,
        actor=audit.agent(LABEL),
        contract_id=contract_id,
        contract_label=f"{contract.reference} {contract.name}",
        agent_run_id=run.id,
    )
    run.model = result.model
    run.input_tokens = result.usage.input_tokens
    run.output_tokens = result.usage.output_tokens
    run.cost_dkk = result.cost_dkk

    payload, cites, conf = _build_payload(s, contract, result.data, versions)
    fp = suggestions.fingerprint(
        AGENT_KEY, contract_id, SuggestionSubject.contract_intake, *payload["basis_version_ids"]
    )
    _, created = suggestions.upsert(
        s,
        org_id=org_id,
        contract_id=contract_id,
        agent_key=AGENT_KEY,
        agent_label=LABEL,
        agent_run_id=run.id,
        kind=SuggestionKind.update,
        subject_kind=SuggestionSubject.contract_intake,
        subject_id=contract_id,
        payload=payload,
        confidence=conf,
        rationale=result.data.rationale,
        citations=cites,
        fp=fp,
    )
    run.suggestions_created = int(created)
    run.suggestions_updated = int(not created)
    run.status = AgentRunStatus.ok
    audit.record(
        s,
        org_id=org_id,
        action=AuditAction.agent_run_completed,
        actor=audit.agent(LABEL),
        object_kind="agent_run",
        object_id=run.id,
        object_label=f"{contract.reference} {contract.name}",
        contract_id=contract_id,
        details={
            "suggestions_created": run.suggestions_created,
            "suggestions_updated": run.suggestions_updated,
        },
        agent_run_id=run.id,
    )


# ---- materialisation (the human's act, ADR-0004 §3) ----------------------------------------


def _parse_date(v: str) -> date | None:
    try:
        return date.fromisoformat(v.strip())
    except ValueError:
        return None


def _parse_decimal(v: str) -> Decimal | None:
    cleaned = v.replace(" ", "").replace("kr.", "").replace("kr", "").replace("DKK", "")
    if "," in cleaned and "." in cleaned:  # Danish 1.234.567,89
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    try:
        return Decimal(cleaned).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _parse_int(v: str) -> int | None:
    try:
        return int(Decimal(v.strip().replace(",", ".")))
    except (InvalidOperation, ValueError):
        return None


def _empty(x: Any) -> bool:
    return x is None or x == "" or x == []


def materialize(
    session: Session, s: AiSuggestion, principal: Principal
) -> suggestions.Materialized:
    c = session.get(Contract, s.contract_id)
    if c is None:
        raise suggestions.SuggestionError("not_found", "Kontrakten findes ikke", 404)
    fields: dict[str, dict[str, Any]] = s.payload.get("fields", {})
    res = suggestions.Materialized(materialized_id=c.id)
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}

    def apply(name: str, column: str, value: Any) -> None:
        cur = getattr(c, column)
        if not _empty(cur):
            res.skipped.append(name)  # fill-only: a human's field wins (bidflow ADR-0024)
            return
        if value is None:
            res.skipped.append(name)
            return
        before[column] = str(cur) if cur is not None else None
        setattr(c, column, value)
        after[column] = str(value)
        res.applied.append(name)

    def raw(name: str) -> str | None:
        f = fields.get(name)
        return f["value"] if f and f.get("value") not in (None, "") else None

    if (v := raw("name")) is not None:
        apply("name", "name", v)
    if (v := raw("contract_number")) is not None:
        apply("contract_number", "contract_number", v)
    if (v := raw("agreement_form")) is not None:
        form = v.strip().lower()
        apply(
            "agreement_form",
            "agreement_form",
            AgreementForm(form) if form in AgreementForm.__members__ else None,
        )
    if (v := raw("category")) is not None:
        apply("category", "category", v)
    if (v := raw("description")) is not None:
        apply("description", "description", v)
    if (v := raw("start_date")) is not None:
        apply("start_date", "start_date", _parse_date(v))
    if (v := raw("end_date")) is not None:
        apply("end_date", "end_date", _parse_date(v))
    if (v := raw("notice_period_months")) is not None:
        months = _parse_int(v)
        apply(
            "notice_period_months", "notice_period", timedelta(days=30 * months) if months else None
        )
    if (v := raw("last_termination_date")) is not None:
        apply("last_termination_date", "last_termination_date", _parse_date(v))
    if (v := raw("price_regulation")) is not None:
        apply("price_regulation", "price_regulation", v)
    options = [
        {"beskrivelse": o["description"], "maaneder": o.get("months")}
        for o in s.payload.get("options", [])
    ]
    if options:
        apply("options", "options", options)
    # Amounts follow ADR-0003's symmetry: without `okonomi` they are neither shown nor set.
    if principal.can("okonomi"):
        if (v := raw("total_value_dkk")) is not None:
            apply("total_value_dkk", "total_value", _parse_decimal(v))
        if (v := raw("annual_value_dkk")) is not None:
            apply("annual_value_dkk", "annual_value", _parse_decimal(v))
    else:
        res.skipped += [n for n in ("total_value_dkk", "annual_value_dkk") if raw(n) is not None]
        if any(raw(n) is not None for n in ("total_value_dkk", "annual_value_dkk")):
            res.note = "beløb ikke anvendt (kræver okonomi)"

    status_changed = False
    if c.status == ContractStatus.kladde:
        c.status = ContractStatus.aktiv  # ADR-0004 afklaring 2
        status_changed = True
    c.updated_at = datetime.now(UTC)
    session.flush()

    if res.skipped:
        res.note = " · ".join(
            x for x in [res.note, f"delvist anvendt: {', '.join(res.skipped)} beholdt"] if x
        )
    label = f"{c.reference} {c.name}"
    if after:
        audit.record(
            session,
            org_id=c.organization_id,
            action=AuditAction.contract_updated,
            actor=audit.human(principal),
            object_kind="contract",
            object_id=c.id,
            object_label=label,
            contract_id=c.id,
            details={"before": before, "after": after, "origin": "ai", "suggestion_id": str(s.id)},
        )
    if status_changed:
        audit.record(
            session,
            org_id=c.organization_id,
            action=AuditAction.contract_status_changed,
            actor=audit.human(principal),
            object_kind="contract",
            object_id=c.id,
            object_label=label,
            contract_id=c.id,
            details={"before": "kladde", "after": "aktiv", "suggestion_id": str(s.id)},
        )
    return res


suggestions.MATERIALIZERS[SuggestionSubject.contract_intake] = materialize
