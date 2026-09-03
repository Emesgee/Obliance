"""Ingest one document version — ADR-0006 §2, bidflow ADR-0054's ordering.

    1. render PDF if needed (docx/xlsx → LibreOffice)   hard
    2. page text                                        hard
    3. clause index                                     best-effort
    (4. chunks + embeddings — ADR-0002/0009, later)     best-effort

Steps 1–2 decide ingest_status; 3 can never fail a version. Ingest writes only
to document_versions (status), document_pages and document_clauses — never to
the registers (ADR-0006 §2). Runs inline in dev/test, as a worker job otherwise
(settings.ingest_runs_inline).
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.core import storage
from app.documents.clauses import index_clauses
from app.documents.convert import CONVERTIBLE_MIMES, to_pdf
from app.documents.pdf import PageText, extract_pages, looks_scanned
from app.domain.models import DocumentClause, DocumentPage, DocumentVersion, IngestStatus

log = logging.getLogger(__name__)

PDF_MIME = "application/pdf"


class IngestError(RuntimeError):
    pass


def _pdf_path_for(version: DocumentVersion, out_dir: Path) -> tuple[Path, str | None]:
    """Return a local PDF path for the version, plus a storage key for a rendered
    PDF (None when the upload already is a PDF)."""
    with storage.materialize(version.storage_key) as src:
        if version.mime == PDF_MIME:
            return src, None
        if version.mime not in CONVERTIBLE_MIMES:
            raise IngestError(f"filtypen understøttes ikke: {version.mime}")
        pdf = to_pdf(src, out_dir)
        if pdf is None:
            raise IngestError("dokumentet kunne ikke konverteres til PDF (LibreOffice)")
        key = version.storage_key.rsplit("/", 1)[0] + "/_rendered.pdf"
        storage.save(key, pdf.read_bytes())
        return pdf, key


def ingest_version(session: Session, version_id: uuid.UUID) -> DocumentVersion:
    """Idempotent: re-running replaces pages/clauses for the version."""
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise IngestError(f"version {version_id} not visible")

    version.ingest_status = IngestStatus.koerer
    version.ingest_error = None
    session.flush()

    try:
        with storage.scratch_dir() as tmp:
            pdf_path, rendered_key = _pdf_path_for(version, tmp)
            pages: list[PageText] = extract_pages(pdf_path)
        if rendered_key:
            version.pdf_storage_key = rendered_key
        version.page_count = len(pages)
        # 2. pages — the primary output. Persist before anything best-effort.
        session.execute(delete(DocumentPage).where(DocumentPage.version_id == version.id))
        for p in pages:
            session.add(
                DocumentPage(
                    version_id=version.id,
                    organization_id=version.organization_id,
                    contract_id=version.contract_id,
                    page_pdf=p.page_pdf,
                    page_printed=p.page_printed,
                    text=p.text,
                )
            )
        if looks_scanned(pages):
            # Not a failure: the file is stored and versioned; OCR is a later,
            # explicit, per-file action (bidflow ADR-0023).
            version.ingest_error = "kunne ikke læses: ingen tekstlag (scannet PDF) — kør OCR"
        version.ingest_status = IngestStatus.ok
        session.flush()
    except IngestError as e:
        version.ingest_status = IngestStatus.fejlet
        version.ingest_error = str(e)
        session.flush()
        return version
    except Exception as e:  # never let a parser crash leave the row in `koerer`
        log.exception("ingest failed for version %s", version.id)
        version.ingest_status = IngestStatus.fejlet
        version.ingest_error = f"uventet fejl under indlæsning: {type(e).__name__}"
        session.flush()
        return version

    # 3. clause index — best-effort (ADR-0005 §2). A failure here is logged, not a
    # failed ingest: the document is still page-citable.
    try:
        session.execute(delete(DocumentClause).where(DocumentClause.version_id == version.id))
        for c in index_clauses(pages):
            session.add(
                DocumentClause(
                    version_id=version.id,
                    organization_id=version.organization_id,
                    contract_id=version.contract_id,
                    clause_ref=c.clause_ref,
                    heading=c.heading,
                    page_pdf=c.page_pdf,
                    char_start=c.char_start,
                    char_end=c.char_end,
                )
            )
        session.flush()
    except Exception:
        log.exception("clause index failed for version %s (best-effort)", version.id)
        session.rollback()
    return version
