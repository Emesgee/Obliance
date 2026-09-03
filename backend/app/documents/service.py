"""Document service — ADR-0006's three human-facing operations.

    create_document  logical document + version 1 (auto-current when ingest ok)
    add_version      next immutable version, kladde until a human makes it current
    make_current     the ONLY way a version becomes gaeldende; emits the event

All functions take a tenant-scoped Session (app.core.auth.tenant_session): RLS
decides what the caller may see; this module decides what is allowed to change.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import events, storage
from app.core.config import settings
from app.documents.ingest import ingest_version
from app.domain.models import (
    ContractDocument,
    DocType,
    DocumentVersion,
    IngestStatus,
    VersionStatus,
)


class DocumentError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


def _storage_key(
    org: uuid.UUID, contract: uuid.UUID, doc: uuid.UUID, no: int, filename: str
) -> str:
    return f"{org}/{contract}/{doc}/{no}/{storage.safe_filename(filename)}"


def _store_version(
    session: Session,
    *,
    doc: ContractDocument,
    uploader: uuid.UUID,
    data: bytes,
    filename: str,
    mime: str,
) -> DocumentVersion:
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise DocumentError("too_large", f"Filen er større end {settings.max_upload_mb} MB", 413)
    sha = hashlib.sha256(data).hexdigest()
    dup = session.scalar(
        select(DocumentVersion.version_no).where(
            DocumentVersion.document_id == doc.id, DocumentVersion.sha256 == sha
        )
    )
    if dup is not None:
        raise DocumentError("duplicate_version", f"Filen er allerede version {dup}", 409)
    next_no = (
        session.scalar(
            select(func.coalesce(func.max(DocumentVersion.version_no), 0)).where(
                DocumentVersion.document_id == doc.id
            )
        )
        or 0
    ) + 1
    version = DocumentVersion(
        organization_id=doc.organization_id,
        contract_id=doc.contract_id,
        document_id=doc.id,
        version_no=next_no,
        storage_key=_storage_key(doc.organization_id, doc.contract_id, doc.id, next_no, filename),
        sha256=sha,
        size_bytes=len(data),
        mime=mime,
        original_filename=storage.safe_filename(filename),
        uploaded_by=uploader,
    )
    session.add(version)
    session.flush()
    storage.save(version.storage_key, data)
    if settings.ingest_runs_inline:
        ingest_version(session, version.id)
    # else: enqueued as a worker job when ADR-0010's queue exists.
    return version


def create_document(
    session: Session,
    *,
    contract_id: uuid.UUID,
    org_id: uuid.UUID,
    actor: uuid.UUID,
    doc_type: DocType,
    title: str,
    data: bytes,
    filename: str,
    mime: str,
) -> tuple[ContractDocument, DocumentVersion]:
    doc = ContractDocument(
        organization_id=org_id,
        contract_id=contract_id,
        doc_type=doc_type,
        title=title.strip() or storage.safe_filename(filename),
        created_by=actor,
    )
    session.add(doc)
    session.flush()  # RLS: contract must be visible, else this raises
    version = _store_version(
        session, doc=doc, uploader=actor, data=data, filename=filename, mime=mime
    )
    # ADR-0006 §3: the first version is made current automatically on a good ingest.
    if version.ingest_status == IngestStatus.ok:
        make_current(session, version_id=version.id, actor=actor)
    return doc, version


def add_version(
    session: Session,
    *,
    document_id: uuid.UUID,
    actor: uuid.UUID,
    data: bytes,
    filename: str,
    mime: str,
) -> DocumentVersion:
    doc = session.get(ContractDocument, document_id)
    if doc is None:
        raise DocumentError("not_found", "Dokumentet findes ikke", 404)
    return _store_version(session, doc=doc, uploader=actor, data=data, filename=filename, mime=mime)


def make_current(session: Session, *, version_id: uuid.UUID, actor: uuid.UUID) -> DocumentVersion:
    """Swap gaeldende in one transaction and emit document_version_changed."""
    version = session.get(DocumentVersion, version_id)
    if version is None:
        raise DocumentError("not_found", "Versionen findes ikke", 404)
    if version.ingest_status != IngestStatus.ok:
        raise DocumentError(
            "not_ingested", "Versionen er ikke færdigindlæst og kan ikke gøres gældende", 409
        )
    if version.status == VersionStatus.gaeldende:
        return version
    old = session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.document_id == version.document_id,
            DocumentVersion.status == VersionStatus.gaeldende,
        )
    )
    now = datetime.now(UTC)
    if old is not None:
        old.status = VersionStatus.historisk
        session.flush()  # release the partial unique index before the new one takes it
    version.status = VersionStatus.gaeldende
    version.made_current_by = actor
    version.made_current_at = now
    doc = session.get(ContractDocument, version.document_id)
    if doc is not None:
        doc.current_version_id = version.id
    session.flush()
    events.emit(
        events.DOCUMENT_VERSION_CHANGED,
        contract_id=version.contract_id,
        document_id=version.document_id,
        old_version_id=old.id if old else None,
        new_version_id=version.id,
    )
    return version
