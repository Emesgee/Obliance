"""Shared agent runtime — what every contract-scoped agent does the same way.

    run_for_contract(spec, execute, org_id=..., contract_id=..., trigger=...)

Own session in system context (ADR-0002), one `agent_runs` row per run written
also when skipped (ADR-0010 §3), never raises (bidflow ADR-0054: the run row is
the report), audit rows for completion/failure (ADR-0011). `execute` gets the
contract and its current agreement versions and does the agent's own work.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import llm
from app.ai import citations
from app.core import audit
from app.core.db import SessionLocal
from app.core.rls import tenant
from app.domain.models import (
    AGREEMENT_DOC_TYPES,
    AgentRun,
    AgentRunStatus,
    AgentSetting,
    AgentTrigger,
    AuditAction,
    Contract,
    ContractDocument,
    DocType,
    DocumentClause,
    DocumentPage,
    DocumentVersion,
    IngestStatus,
)

log = logging.getLogger(__name__)

Versions = list[tuple[ContractDocument, DocumentVersion]]


@dataclass(frozen=True, slots=True)
class AgentSpec:
    key: str
    label: str  # "AI · Contract Intake Agent" — the audit log's spelling
    task: str  # ADR-0009 task name


Execute = Callable[[Session, AgentRun, Contract, Versions], None]


def agreement_versions(
    session: Session, contract_id: uuid.UUID, doc_types: frozenset[DocType] = AGREEMENT_DOC_TYPES
) -> Versions:
    """The current, ingested versions of the agreement documents (ADR-0006 §4) —
    or of another doc-type set, e.g. reports for kpi_parse."""
    docs = session.scalars(
        select(ContractDocument)
        .where(
            ContractDocument.contract_id == contract_id,
            ContractDocument.doc_type.in_(list(doc_types)),
            ContractDocument.current_version_id.is_not(None),
        )
        .order_by(ContractDocument.created_at)
    ).all()
    out: Versions = []
    for d in docs:
        v = session.get(DocumentVersion, d.current_version_id)
        if v is not None and v.ingest_status == IngestStatus.ok:
            out.append((d, v))
    return out


def pages_for(session: Session, version_id: uuid.UUID) -> list[llm.PageBlock]:
    rows = session.scalars(
        select(DocumentPage)
        .where(DocumentPage.version_id == version_id)
        .order_by(DocumentPage.page_pdf)
    ).all()
    return [llm.PageBlock(p.page_pdf, p.page_printed, p.text) for p in rows]


def material_for(session: Session, versions: Versions) -> list[llm.DataBlock]:
    return [
        llm.DataBlock(kind="dokument", id=str(d.id), label=d.title, pages=pages_for(session, v.id))
        for d, v in versions
    ]


def version_index(
    session: Session, version_id: uuid.UUID
) -> tuple[list[citations.Page], list[citations.Clause]]:
    pages = [
        citations.Page(p.page_pdf, p.page_printed, p.text)
        for p in session.scalars(select(DocumentPage).where(DocumentPage.version_id == version_id))
    ]
    clauses = [
        citations.Clause(c.clause_ref, c.page_pdf, c.char_start)
        for c in session.scalars(
            select(DocumentClause).where(DocumentClause.version_id == version_id)
        )
    ]
    return pages, clauses


class Verifier:
    """Locates model-supplied quotes in the versions the agent read (ADR-0005 §3)."""

    def __init__(self, session: Session, versions: Versions) -> None:
        self._session = session
        self._by_doc = {str(d.id): (d, v) for d, v in versions}
        self._index: dict[uuid.UUID, tuple[list[citations.Page], list[citations.Clause]]] = {}

    def verify(self, document_id: str, page_pdf: int | None, quote: str) -> dict[str, Any]:
        hit = self._by_doc.get(document_id)
        if hit is None:
            return {
                "kind": "document",
                "document_id": document_id,
                "document_version_id": None,
                "page_pdf": page_pdf,
                "page_printed": None,
                "clause_ref": None,
                "quote": quote,
                "verified": False,
                "label": "ukendt dokument",
            }
        d, v = hit
        if v.id not in self._index:
            self._index[v.id] = version_index(self._session, v.id)
        pages, clauses = self._index[v.id]
        loc = citations.locate(pages, clauses, quote, page_pdf)
        return citations.citation_json(
            document_id=d.id, document_version_id=v.id, doc_title=d.title, quote=quote, located=loc
        )


def record_llm(run: AgentRun, result: llm.LlmResult[Any]) -> None:
    run.model = result.model
    run.input_tokens = (run.input_tokens or 0) + result.usage.input_tokens
    run.output_tokens = (run.output_tokens or 0) + result.usage.output_tokens
    if result.cost_dkk is not None:
        run.cost_dkk = (run.cost_dkk or 0) + result.cost_dkk


@dataclass(slots=True)
class Outcome:
    """What one contract's execution amounted to — applied to the run row by the
    caller, so the same code serves one-contract runs and the nightly org run."""

    status: AgentRunStatus
    error: str | None = None
    reason: str | None = None


def execute_one(
    s: Session,
    run: AgentRun,
    spec: AgentSpec,
    execute: Execute,
    contract_id: uuid.UUID,
    doc_types: frozenset[DocType],
) -> Outcome:
    """Run `execute` for one contract. Raises what the agent raises (the caller
    decides whether that fails the run or just this contract)."""
    contract = s.get(Contract, contract_id)
    if contract is None:
        return Outcome(AgentRunStatus.fejlet, "Kontrakten findes ikke")
    versions = agreement_versions(s, contract_id, doc_types)
    if not versions:
        return Outcome(
            AgentRunStatus.sprunget_over,
            (
                "Intet aftalegrundlag: upload en hovedkontrakt, et bilag eller et tillæg"
                if doc_types == AGREEMENT_DOC_TYPES
                else "Ingen indlæst rapport på kontrakten"
            ),
            "no_agreement_documents",
        )
    execute(s, run, contract, versions)
    return Outcome(AgentRunStatus.ok)


def is_disabled(s: Session, org_id: uuid.UUID, key: str) -> bool:
    setting = s.get(AgentSetting, (org_id, key))
    return setting is not None and not setting.enabled


def finish(run: AgentRun, started: datetime) -> None:
    finished = datetime.now(UTC)
    run.finished_at = finished
    run.duration_ms = int((finished - started).total_seconds() * 1000)


def run_for_contract(
    spec: AgentSpec,
    execute: Execute,
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    trigger: AgentTrigger,
    trigger_ref: str | None = None,
    triggered_by: uuid.UUID | None = None,
    doc_types: frozenset[DocType] = AGREEMENT_DOC_TYPES,
) -> uuid.UUID:
    started = datetime.now(UTC)
    with tenant(org_id, system=True), SessionLocal() as s:
        run = AgentRun(
            organization_id=org_id,
            agent_key=spec.key,
            contract_id=contract_id,
            trigger=trigger,
            trigger_ref=trigger_ref,
            triggered_by=triggered_by,
            task=spec.task,
            started_at=started,
        )
        s.add(run)
        s.commit()
        try:
            if is_disabled(s, org_id, spec.key):
                run.status = AgentRunStatus.sprunget_over
                run.error_context = {"reason": "disabled"}
            else:
                out = execute_one(s, run, spec, execute, contract_id, doc_types)
                run.contracts_scanned = 1
                run.status = out.status
                run.error = out.error
                if out.reason:
                    run.error_context = {"reason": out.reason}
                if out.status == AgentRunStatus.ok:
                    audit_completed(s, run, spec, contract_id)
        except llm.LlmBudgetExceeded as e:
            run.status = AgentRunStatus.sprunget_over
            run.error = str(e)
            run.error_context = {"reason": "budget"}
        except Exception as e:  # noqa: BLE001 — the run row is the report
            log.exception("%s failed contract=%s", spec.key, contract_id)
            run.status = AgentRunStatus.fejlet
            run.error = f"{e.__class__.__name__}: {e}"[:1000]
            run.error_context = {"code": getattr(e, "code", None)}
            audit.record(
                s,
                org_id=org_id,
                action=AuditAction.agent_run_failed,
                actor=audit.agent(spec.label),
                object_kind="agent_run",
                object_id=run.id,
                contract_id=contract_id,
                details={"error": run.error},
                agent_run_id=run.id,
            )
        finally:
            finish(run, started)
            s.commit()
        return run.id


def audit_completed(
    s: Session, run: AgentRun, spec: AgentSpec, contract_id: uuid.UUID | None
) -> None:
    label = ""
    if contract_id is not None:
        contract = s.get(Contract, contract_id)
        if contract is not None:
            label = f"{contract.reference} {contract.name}"
    audit.record(
        s,
        org_id=run.organization_id,
        action=AuditAction.agent_run_completed,
        actor=audit.agent(spec.label),
        object_kind="agent_run",
        object_id=run.id,
        object_label=label,
        contract_id=contract_id,
        details={
            "contracts_scanned": run.contracts_scanned,
            "suggestions_created": run.suggestions_created,
            "suggestions_updated": run.suggestions_updated,
        },
        agent_run_id=run.id,
    )
