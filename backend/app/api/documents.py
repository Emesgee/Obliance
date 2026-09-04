"""Documents API — ADR-0006 over HTTP.

    GET  /api/contracts/{id}                         contract detail
    GET  /api/contracts/{id}/documents               documents + versions
    POST /api/contracts/{id}/documents               upload → document + version 1   [kontrakt_red]
    POST /api/documents/{doc_id}/versions            upload → next version (kladde)  [kontrakt_red]
    POST /api/documents/versions/{vid}/make-current  the human act (ADR-0006 §3)     [kontrakt_red]
    GET  /api/documents/versions/{vid}/pages         page text (viewer / citations)
    GET  /api/documents/versions/{vid}/clauses       heuristic clause index
    GET  /api/documents/versions/{vid}/file          original bytes (streamed)

Visibility is RLS's: every read goes through tenant_session, so a version on a
fortrolig contract the caller cannot see is a 404, not a 403 — there is nothing
to reveal.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.contracts import mask_financials
from app.api.schemas import (
    ClauseOut,
    ContractOut,
    DocumentOut,
    DocumentVersionOut,
    PageOut,
)
from app.core import access, audit, storage
from app.core.auth import Principal, current_principal, require, tenant_session
from app.documents import service
from app.documents.convert import CONVERTIBLE_MIMES
from app.documents.ingest import PDF_MIME
from app.domain.models import (
    Contract,
    ContractDocument,
    DocType,
    DocumentClause,
    DocumentPage,
    DocumentVersion,
)

router = APIRouter(prefix="/api", tags=["documents"])

ACCEPTED_MIMES = frozenset({PDF_MIME}) | CONVERTIBLE_MIMES


def _not_found(what: str = "Ikke fundet") -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail={"error": what, "code": "not_found"})


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise _not_found("Kontrakten findes ikke")
    return c


def _version_or_404(session: Session, version_id: uuid.UUID) -> DocumentVersion:
    v = session.get(DocumentVersion, version_id)
    if v is None:
        raise _not_found("Versionen findes ikke")
    return v


async def _read_upload(file: UploadFile) -> tuple[bytes, str, str]:
    mime = (file.content_type or "").split(";")[0].strip().lower()
    if mime not in ACCEPTED_MIMES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail={
                "error": "Filtypen understøttes ikke — upload PDF, Word, Excel eller PowerPoint",
                "code": "unsupported_type",
            },
        )
    data = await file.read()
    if not data:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail={"error": "Filen er tom", "code": "empty_file"}
        )
    return data, file.filename or "fil", mime


def _doc_out(session: Session, doc: ContractDocument) -> DocumentOut:
    versions = session.scalars(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == doc.id)
        .order_by(DocumentVersion.version_no.desc())
    ).all()
    return DocumentOut(
        id=doc.id,
        contract_id=doc.contract_id,
        doc_type=doc.doc_type,
        title=doc.title,
        current_version_id=doc.current_version_id,
        amends_document_id=doc.amends_document_id,
        created_at=doc.created_at,
        versions=[DocumentVersionOut.model_validate(v) for v in versions],
    )


# ---- contract detail ------------------------------------------------------------------


@router.get("/contracts/{contract_id}", response_model=ContractOut)
def get_contract(
    contract_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> ContractOut:
    c = _contract_or_404(session, contract_id)
    return mask_financials(ContractOut.model_validate(c), principal)


# ---- documents ------------------------------------------------------------------------


@router.get("/contracts/{contract_id}/documents", response_model=list[DocumentOut])
def list_documents(
    contract_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[DocumentOut]:
    _contract_or_404(session, contract_id)
    docs = session.scalars(
        select(ContractDocument)
        .where(ContractDocument.contract_id == contract_id)
        .order_by(ContractDocument.created_at)
    ).all()
    return [_doc_out(session, d) for d in docs]


@router.post(
    "/contracts/{contract_id}/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    contract_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    doc_type: Annotated[DocType, Form()] = DocType.andet,
    title: Annotated[str, Form()] = "",
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> DocumentOut:
    _contract_or_404(session, contract_id)
    data, filename, mime = await _read_upload(file)
    try:
        doc, _ = service.create_document(
            session,
            contract_id=contract_id,
            org_id=principal.org_id,
            actor=principal.user_id,
            audit_actor=audit.human(principal),
            doc_type=doc_type,
            title=title,
            data=data,
            filename=filename,
            mime=mime,
        )
    except service.DocumentError as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return _doc_out(session, doc)


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_version(
    document_id: uuid.UUID,
    file: Annotated[UploadFile, File()],
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> DocumentVersionOut:
    data, filename, mime = await _read_upload(file)
    try:
        version = service.add_version(
            session,
            document_id=document_id,
            actor=principal.user_id,
            audit_actor=audit.human(principal),
            data=data,
            filename=filename,
            mime=mime,
        )
    except service.DocumentError as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return DocumentVersionOut.model_validate(version)


@router.post("/documents/versions/{version_id}/make-current", response_model=DocumentVersionOut)
def make_current(
    version_id: uuid.UUID,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> DocumentVersionOut:
    try:
        version = service.make_current(
            session,
            version_id=version_id,
            actor=principal.user_id,
            audit_actor=audit.human(principal),
        )
    except service.DocumentError as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return DocumentVersionOut.model_validate(version)


@router.get("/documents/versions/{version_id}/pages", response_model=list[PageOut])
def version_pages(
    version_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[PageOut]:
    _version_or_404(session, version_id)
    rows = session.scalars(
        select(DocumentPage)
        .where(DocumentPage.version_id == version_id)
        .order_by(DocumentPage.page_pdf)
    ).all()
    return [PageOut.model_validate(r) for r in rows]


@router.get("/documents/versions/{version_id}/clauses", response_model=list[ClauseOut])
def version_clauses(
    version_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> list[ClauseOut]:
    _version_or_404(session, version_id)
    rows = session.scalars(
        select(DocumentClause)
        .where(DocumentClause.version_id == version_id)
        .order_by(DocumentClause.page_pdf, DocumentClause.char_start)
    ).all()
    return [ClauseOut.model_validate(r) for r in rows]


@router.get("/documents/versions/{version_id}/file")
def version_file(
    version_id: uuid.UUID,
    session: Session = Depends(tenant_session),
) -> Response:
    v = _version_or_404(session, version_id)
    data = storage.read_bytes(v.storage_key)
    return Response(
        content=data,
        media_type=v.mime,
        headers={"Content-Disposition": f'inline; filename="{v.original_filename}"'},
    )
