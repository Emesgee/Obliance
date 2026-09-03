"""ai_suggestions — ADR-0004's one mechanism.

    upsert(...)                 agent side: fingerprint → update, never duplicate (§4)
    approve(...) / reject(...)  the human verdict (§2/§3); reject needs a reason
    expire_for_version(...)     system only: a document version switch (§2)

Materialisation is per subject_kind: `MATERIALIZERS[subject] = fn(session, s, principal)`
registered by the module that owns the target table (contract_intake lives in
app.agents.contract_intake). The agent never touches the target table; the
approving human does, in one transaction, with `origin = ai` semantics recorded
in the audit log.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import access, audit
from app.core.auth import Principal
from app.domain.models import (
    OPEN_SUGGESTION_STATUSES,
    AiSuggestion,
    AuditAction,
    Confidence,
    Contract,
    SuggestionKind,
    SuggestionStatus,
    SuggestionSubject,
)


class SuggestionError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


# ADR-0004 §2: approve needs `hitl` plus the subject's own permission.
SUBJECT_PERMISSION: dict[SuggestionSubject, str] = {
    SuggestionSubject.raci_entry: access.RACI_GODKEND,
    SuggestionSubject.invoice_finding: access.OKONOMI,
    SuggestionSubject.sla_breach: access.OKONOMI,
    SuggestionSubject.contract_intake: access.KONTRAKT_RED,
    SuggestionSubject.obligation: access.KONTRAKT_RED,
    SuggestionSubject.risk: access.KONTRAKT_RED,
    SuggestionSubject.task: access.KONTRAKT_RED,
}


@dataclass
class Materialized:
    materialized_id: uuid.UUID | None
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    note: str = ""


Materializer = Callable[[Session, AiSuggestion, Principal], Materialized]
MATERIALIZERS: dict[SuggestionSubject, Materializer] = {}


def fingerprint(
    agent_key: str, contract_id: uuid.UUID, subject: SuggestionSubject, *key: str
) -> str:
    raw = "|".join([agent_key, str(contract_id), subject.value, *key])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _label(session: Session, contract_id: uuid.UUID) -> str:
    c = session.get(Contract, contract_id)
    return f"{c.reference} {c.name}" if c else str(contract_id)


def upsert(
    session: Session,
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    agent_key: str,
    agent_label: str,
    agent_run_id: uuid.UUID | None,
    kind: SuggestionKind,
    subject_kind: SuggestionSubject,
    subject_id: uuid.UUID | None,
    payload: dict[str, Any],
    confidence: Confidence,
    rationale: str,
    citations: list[dict[str, Any]],
    fp: str,
    amount_dkk: Decimal | None = None,
) -> tuple[AiSuggestion, bool]:
    """Returns (suggestion, created). A rejected fingerprint is not re-proposed
    (ADR-0004 §4) — the caller changes the fingerprint when the basis changes."""
    if kind == SuggestionKind.create and not citations:
        raise SuggestionError("citations_required", "Et create-forslag skal have mindst én kilde")
    existing = session.scalars(
        select(AiSuggestion)
        .where(AiSuggestion.fingerprint == fp)
        .order_by(AiSuggestion.created_at.desc())
    ).first()
    now = datetime.now(UTC)
    if existing is not None and existing.status in OPEN_SUGGESTION_STATUSES:
        existing.agent_run_id = agent_run_id
        existing.payload = payload
        existing.confidence = confidence
        existing.rationale = rationale
        existing.citations = citations
        existing.amount_dkk = amount_dkk
        existing.updated_at = now
        session.flush()
        return existing, False
    if existing is not None and existing.status in (
        SuggestionStatus.afvist,
        SuggestionStatus.godkendt,
    ):
        return existing, False
    s = AiSuggestion(
        organization_id=org_id,
        contract_id=contract_id,
        agent_key=agent_key,
        agent_run_id=agent_run_id,
        kind=kind,
        subject_kind=subject_kind,
        subject_id=subject_id,
        payload=payload,
        confidence=confidence,
        rationale=rationale,
        citations=citations,
        amount_dkk=amount_dkk,
        fingerprint=fp,
        created_at=now,
        updated_at=now,
    )
    session.add(s)
    session.flush()
    audit.record(
        session,
        org_id=org_id,
        action=AuditAction.ai_suggestion_created,
        actor=audit.agent(agent_label),
        object_kind="ai_suggestion",
        object_id=s.id,
        object_label=f"{subject_kind.value} · {_label(session, contract_id)}",
        contract_id=contract_id,
        details={"confidence": confidence.value, "kind": kind.value},
        agent_run_id=agent_run_id,
    )
    return s, True


def _open_or_error(session: Session, suggestion_id: uuid.UUID) -> AiSuggestion:
    s = session.get(AiSuggestion, suggestion_id)
    if s is None:
        raise SuggestionError("not_found", "Forslaget findes ikke", 404)
    if s.status not in OPEN_SUGGESTION_STATUSES:
        raise SuggestionError("already_decided", f"Forslaget er allerede {s.status.value}", 409)
    return s


def _check_permission(s: AiSuggestion, principal: Principal) -> None:
    if not principal.can(access.HITL):
        raise SuggestionError("forbidden", "Kræver tilladelsen hitl", 403)
    needed = SUBJECT_PERMISSION.get(s.subject_kind)
    if needed and not principal.can(needed):
        raise SuggestionError("forbidden", f"Kræver tilladelsen {needed}", 403)


def approve(
    session: Session, *, suggestion_id: uuid.UUID, principal: Principal, comment: str | None
) -> AiSuggestion:
    s = _open_or_error(session, suggestion_id)
    _check_permission(s, principal)
    if s.amount_dkk is not None:
        # ADR-0003 amount thresholds + two signatures: not built yet; refuse rather
        # than approve money without the second-signature path.
        raise SuggestionError(
            "not_supported", "Pengeforslag kræver beløbsgrænse-flowet (kommer)", 409
        )
    mat = MATERIALIZERS.get(s.subject_kind)
    if mat is None:
        raise SuggestionError(
            "not_supported", f"Ingen materialisering for {s.subject_kind.value}", 409
        )
    result = mat(session, s, principal)
    now = datetime.now(UTC)
    s.status = SuggestionStatus.godkendt
    s.decided_by = principal.user_id
    s.decided_at = now
    s.decision_comment = " · ".join(x for x in [(comment or "").strip(), result.note] if x) or None
    s.materialized_id = result.materialized_id
    s.updated_at = now
    session.flush()
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.ai_suggestion_approved,
        actor=audit.human(principal),
        object_kind="ai_suggestion",
        object_id=s.id,
        object_label=f"{s.subject_kind.value} · {_label(session, s.contract_id)}",
        contract_id=s.contract_id,
        details={"applied": result.applied, "skipped": result.skipped, "comment": comment},
    )
    return s


def reject(
    session: Session, *, suggestion_id: uuid.UUID, principal: Principal, comment: str
) -> AiSuggestion:
    reason = (comment or "").strip()
    if len(reason) < 3:
        # ADR-0004 §2: the reason is mandatory — it is what calibrates the agents.
        raise SuggestionError("comment_required", "Angiv en begrundelse for afvisningen")
    s = _open_or_error(session, suggestion_id)
    _check_permission(s, principal)
    now = datetime.now(UTC)
    s.status = SuggestionStatus.afvist
    s.decided_by = principal.user_id
    s.decided_at = now
    s.decision_comment = reason
    s.updated_at = now
    session.flush()
    audit.record(
        session,
        org_id=s.organization_id,
        action=AuditAction.ai_suggestion_rejected,
        actor=audit.human(principal),
        object_kind="ai_suggestion",
        object_id=s.id,
        object_label=f"{s.subject_kind.value} · {_label(session, s.contract_id)}",
        contract_id=s.contract_id,
        details={"reason": reason},
    )
    return s


def expire_for_version(
    session: Session, *, org_id: uuid.UUID, contract_id: uuid.UUID, old_version_id: uuid.UUID | None
) -> int:
    """ADR-0004 §2 + ADR-0006 §3: only suggestions whose citations point at the
    replaced version (or whose basis is the whole agreement — contract_intake) expire."""
    rows = session.scalars(
        select(AiSuggestion).where(
            AiSuggestion.contract_id == contract_id,
            AiSuggestion.status.in_(OPEN_SUGGESTION_STATUSES),
        )
    ).all()
    n = 0
    now = datetime.now(UTC)
    for s in rows:
        cites_old = old_version_id is not None and any(
            c.get("document_version_id") == str(old_version_id) for c in s.citations
        )
        if not (cites_old or s.subject_kind == SuggestionSubject.contract_intake):
            continue
        s.status = SuggestionStatus.foraeldet
        s.updated_at = now
        n += 1
        audit.record(
            session,
            org_id=org_id,
            action=AuditAction.ai_suggestion_expired,
            actor=audit.system("System · Dokumentversion"),
            object_kind="ai_suggestion",
            object_id=s.id,
            object_label=f"{s.subject_kind.value} · {_label(session, contract_id)}",
            contract_id=contract_id,
            details={"old_version_id": str(old_version_id) if old_version_id else None},
        )
    session.flush()
    return n
