"""Re-resolution of citations after a document version switch — ADR-0005 §5.

Every citation on the replaced version is located again in the new version:

    uaendret    same page and clause → nothing
    flyttet     found elsewhere → a successor citation row on the new version
    ikke_fundet the clause was negotiated away → the finding shows "kilde forældet"
                (tasks for the manager come with ADR-0017/tasks)

Citations are never rewritten (§1): the old row keeps pointing at the old version,
with `successor_status` and, when moved, `successor_id`. That is how the system
notices a clause disappeared — without a model.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import citations
from app.core import audit
from app.domain.models import (
    AuditAction,
    Citation,
    CitationKind,
    ContractDocument,
    SuccessorStatus,
)

if TYPE_CHECKING:
    from app.agents.runtime import Versions  # noqa: F401 — documentation only


def reresolve_version(
    session: Session,
    *,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    old_version_id: uuid.UUID | None,
    new_version_id: uuid.UUID,
) -> dict[str, int]:
    from app.agents.runtime import version_index  # local import: avoids an import cycle

    counts = {"uaendret": 0, "flyttet": 0, "ikke_fundet": 0}
    if old_version_id is None:
        return counts
    rows = session.scalars(
        select(Citation).where(
            Citation.document_version_id == old_version_id,
            Citation.kind == CitationKind.document,
            Citation.successor_status.is_(None),
        )
    ).all()
    if not rows:
        return counts
    pages, clauses = version_index(session, new_version_id)
    doc_title = ""
    if rows[0].document_id is not None:
        d = session.get(ContractDocument, rows[0].document_id)
        doc_title = d.title if d else ""
    for c in rows:
        loc = citations.locate(pages, clauses, c.quote or "", c.page_pdf)
        if not loc.verified:
            c.successor_status = SuccessorStatus.ikke_fundet
            counts["ikke_fundet"] += 1
            continue
        if loc.page_pdf == c.page_pdf and loc.clause_ref == c.clause_ref:
            c.successor_status = SuccessorStatus.uaendret
            counts["uaendret"] += 1
            continue
        succ = Citation(
            organization_id=org_id,
            contract_id=contract_id,
            subject_kind=c.subject_kind,
            subject_id=c.subject_id,
            kind=CitationKind.document,
            document_id=c.document_id,
            document_version_id=new_version_id,
            page_pdf=loc.page_pdf,
            page_printed=loc.page_printed,
            clause_ref=loc.clause_ref,
            quote=c.quote,
            quote_hash=c.quote_hash,
            verified=True,
            label=citations.label(doc_title, loc.page_pdf, loc.page_printed, loc.clause_ref),
        )
        session.add(succ)
        session.flush()
        c.successor_status = SuccessorStatus.flyttet
        c.successor_id = succ.id
        counts["flyttet"] += 1
    session.flush()
    audit.record(
        session,
        org_id=org_id,
        action=AuditAction.citations_reresolved,
        actor=audit.system("System · Dokumentversion"),
        object_kind="document_version",
        object_id=new_version_id,
        contract_id=contract_id,
        details={"old_version_id": str(old_version_id), **counts},
    )
    return counts
