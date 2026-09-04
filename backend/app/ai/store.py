"""Citation rows for a materialised subject (ADR-0005 §1) — shared by every
materialiser, kept out of the agent modules to avoid import cycles."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.ai import citations
from app.domain.models import Citation, CitationKind


def add_citations(
    session: Session,
    *,
    subject_kind: str,
    subject_id: uuid.UUID,
    org_id: uuid.UUID,
    contract_id: uuid.UUID,
    cites: list[dict[str, Any]],
) -> list[Citation]:
    rows: list[Citation] = []
    for c in cites:
        if c.get("kind", "document") != "document" or not c.get("document_version_id"):
            continue
        row = Citation(
            organization_id=org_id,
            contract_id=contract_id,
            subject_kind=subject_kind,
            subject_id=subject_id,
            kind=CitationKind.document,
            document_id=uuid.UUID(c["document_id"]) if c.get("document_id") else None,
            document_version_id=uuid.UUID(c["document_version_id"]),
            page_pdf=c.get("page_pdf"),
            page_printed=c.get("page_printed"),
            clause_ref=c.get("clause_ref"),
            quote=c.get("quote"),
            quote_hash=citations.quote_hash(c.get("quote") or ""),
            verified=bool(c.get("verified")),
            label=c.get("label") or "",
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows
