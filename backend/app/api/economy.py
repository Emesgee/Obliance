"""Økonomi over HTTP (ADR-0018): suppliers, price terms, the invoice feed.

    GET/POST  /api/suppliers                                          [okonomi|kontrakt_red]
    PATCH     /api/contracts/{id}/supplier                            [kontrakt_red]
    GET/POST  /api/contracts/{id}/price-terms                         [okonomi to write]
    POST      /api/invoices/import   multipart CSV/xlsx → report      [okonomi]
    GET       /api/invoices?queue=unmatched|pending                   [okonomi]
    GET       /api/contracts/{id}/invoices · /spend
    POST      /api/invoices/{id}/match · /approve · /reject           [okonomi]
    GET       /api/import-errors                                      [okonomi]

Nothing here writes to an ERP system — there is no such endpoint (§1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    CitationOut,
    ContractSupplierIn,
    ImportErrorOut,
    ImportReportOut,
    InvoiceDecisionIn,
    InvoiceLineOut,
    InvoiceMatchIn,
    InvoiceOut,
    PriceTermCreate,
    PriceTermOut,
    SpendOut,
    SupplierCreate,
    SupplierOut,
)
from app.core import access, audit
from app.core.auth import Principal, current_principal, require, tenant_session
from app.domain.models import (
    OPEN_SUGGESTION_STATUSES,
    AiSuggestion,
    AuditAction,
    Citation,
    Contract,
    ImportError_,
    Invoice,
    InvoiceLine,
    InvoiceStatus,
    Origin,
    PriceTerm,
    SuggestionSubject,
    Supplier,
)
from app.finance import invoices as feed

router = APIRouter(prefix="/api", tags=["economy"])


def _contract_or_404(session: Session, contract_id: uuid.UUID) -> Contract:
    c = session.get(Contract, contract_id)
    if c is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Kontrakten findes ikke", "code": "not_found"},
        )
    return c


def _invoice_or_404(session: Session, invoice_id: uuid.UUID) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Fakturaen findes ikke", "code": "not_found"},
        )
    return inv


def _money_or_403(principal: Principal) -> None:
    if not (principal.can(access.OKONOMI) or principal.can(access.KONTRAKT_RED)):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"error": "Kræver okonomi eller kontrakt_red", "code": "forbidden"},
        )


# ---- suppliers ---------------------------------------------------------------------------------


@router.get("/suppliers", response_model=list[SupplierOut])
def list_suppliers(
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> list[SupplierOut]:
    rows = session.scalars(select(Supplier).order_by(Supplier.name)).all()
    return [SupplierOut.model_validate(s) for s in rows]


@router.post("/suppliers", response_model=SupplierOut, status_code=status.HTTP_201_CREATED)
def create_supplier(
    body: SupplierCreate,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> SupplierOut:
    _money_or_403(principal)
    if session.scalars(select(Supplier).where(Supplier.cvr == body.cvr)).first():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error": "Leverandøren findes allerede", "code": "cvr_taken"},
        )
    s = Supplier(
        organization_id=principal.org_id, created_by=principal.user_id, **body.model_dump()
    )
    session.add(s)
    session.flush()
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.supplier_created,
        actor=audit.human(principal),
        object_kind="supplier",
        object_id=s.id,
        object_label=f"{s.name} ({s.cvr})",
    )
    return SupplierOut.model_validate(s)


@router.patch("/contracts/{contract_id}/supplier", response_model=SupplierOut | None)
def set_contract_supplier(
    contract_id: uuid.UUID,
    body: ContractSupplierIn,
    principal: Principal = Depends(require(access.KONTRAKT_RED)),
    session: Session = Depends(tenant_session),
) -> SupplierOut | None:
    c = _contract_or_404(session, contract_id)
    sup = session.get(Supplier, body.supplier_id) if body.supplier_id else None
    if body.supplier_id and sup is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"error": "Leverandøren findes ikke", "code": "not_found"},
        )
    before = str(c.supplier_id) if c.supplier_id else None
    c.supplier_id = sup.id if sup else None
    c.updated_at = datetime.now(UTC)
    session.flush()
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.contract_updated,
        actor=audit.human(principal),
        object_kind="contract",
        object_id=c.id,
        object_label=f"{c.reference} {c.name}",
        contract_id=c.id,
        details={"before": {"supplier_id": before}, "after": {"supplier_id": str(c.supplier_id)}},
    )
    return SupplierOut.model_validate(sup) if sup else None


# ---- price terms -------------------------------------------------------------------------------


def _price_out(session: Session, t: PriceTerm) -> PriceTermOut:
    cites = session.scalars(
        select(Citation).where(Citation.subject_kind == "price_term", Citation.subject_id == t.id)
    ).all()
    return PriceTermOut(
        id=t.id,
        contract_id=t.contract_id,
        product_ref=t.product_ref,
        description=t.description,
        unit=t.unit,
        agreed_unit_price=t.agreed_unit_price,
        valid_from=t.valid_from,
        valid_to=t.valid_to,
        origin=t.origin,
        citations=[CitationOut.model_validate(c) for c in cites],
    )


@router.get("/contracts/{contract_id}/price-terms", response_model=list[PriceTermOut])
def list_price_terms(
    contract_id: uuid.UUID, session: Session = Depends(tenant_session)
) -> list[PriceTermOut]:
    _contract_or_404(session, contract_id)
    rows = session.scalars(
        select(PriceTerm)
        .where(PriceTerm.contract_id == contract_id)
        .order_by(PriceTerm.description)
    ).all()
    return [_price_out(session, t) for t in rows]


@router.post(
    "/contracts/{contract_id}/price-terms",
    response_model=PriceTermOut,
    status_code=status.HTTP_201_CREATED,
)
def create_price_term(
    contract_id: uuid.UUID,
    body: PriceTermCreate,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> PriceTermOut:
    c = _contract_or_404(session, contract_id)
    t = PriceTerm(
        organization_id=principal.org_id,
        contract_id=c.id,
        origin=Origin.human,
        created_by=principal.user_id,
        **body.model_dump(),
    )
    session.add(t)
    session.flush()
    audit.record(
        session,
        org_id=principal.org_id,
        action=AuditAction.price_term_created,
        actor=audit.human(principal),
        object_kind="price_term",
        object_id=t.id,
        object_label=t.description,
        contract_id=c.id,
        details={"origin": "human", "agreed_unit_price": str(t.agreed_unit_price)},
    )
    return _price_out(session, t)


# ---- invoices ----------------------------------------------------------------------------------


def invoice_out(session: Session, inv: Invoice) -> InvoiceOut:
    sup = session.get(Supplier, inv.supplier_id)
    c = session.get(Contract, inv.contract_id) if inv.contract_id else None
    lines = session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no)
    ).all()
    cands: list[dict[str, str]] = []
    if inv.contract_id is None:
        m = session.scalars(
            select(AiSuggestion).where(
                AiSuggestion.subject_kind == SuggestionSubject.invoice_match,
                AiSuggestion.subject_id == inv.id,
                AiSuggestion.status.in_(OPEN_SUGGESTION_STATUSES),
            )
        ).first()
        if m is not None:
            cands = [{k: str(v) for k, v in x.items()} for x in m.payload.get("candidates", [])]
    return InvoiceOut(
        id=inv.id,
        contract_id=inv.contract_id,
        contract_ref=c.reference if c else None,
        supplier_id=inv.supplier_id,
        supplier_name=sup.name if sup else "",
        supplier_cvr=sup.cvr if sup else "",
        invoice_number=inv.invoice_number,
        invoice_date=inv.invoice_date,
        due_date=inv.due_date,
        currency=inv.currency,
        total_amount=inv.total_amount,
        contract_reference=inv.contract_reference,
        status=inv.status,
        matched_by=inv.matched_by,
        control_result=inv.control_result,
        control_note=inv.control_note,
        supersedes_invoice_id=inv.supersedes_invoice_id,
        first_seen_at=inv.first_seen_at,
        decided_at=inv.decided_at,
        decision_comment=inv.decision_comment,
        lines=[InvoiceLineOut.model_validate(ln) for ln in lines],
        candidates=cands,
    )


@router.post("/invoices/import", response_model=ImportReportOut)
async def import_invoices(
    file: Annotated[UploadFile, File()],
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> ImportReportOut:
    data = await file.read()
    name = file.filename or "import.csv"
    try:
        rows = feed.parse_file(name, data)
        report = feed.import_rows(session, principal=principal, file_name=name, rows=rows)
    except feed.ImportRejected as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return ImportReportOut(
        received=report.received,
        new=report.new,
        updated=report.updated,
        superseded=report.superseded,
        rejected=report.rejected,
        matched=report.matched,
        queued=report.queued,
        errors=[{"row_no": e.row_no, "reason": e.reason} for e in report.errors],
    )


@router.get("/invoices", response_model=list[InvoiceOut])
def list_invoices(
    queue: str | None = Query(default=None, pattern="^(unmatched|pending|all)$"),
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> list[InvoiceOut]:
    q = select(Invoice).order_by(Invoice.invoice_date.desc(), Invoice.first_seen_at.desc())
    if queue == "unmatched":
        q = q.where(Invoice.contract_id.is_(None), Invoice.status == InvoiceStatus.modtaget)
    elif queue == "pending":
        q = q.where(Invoice.status.in_((InvoiceStatus.kontrolleret, InvoiceStatus.matchet)))
    return [invoice_out(session, i) for i in session.scalars(q.limit(500)).all()]


@router.get("/contracts/{contract_id}/invoices", response_model=list[InvoiceOut])
def contract_invoices(
    contract_id: uuid.UUID,
    principal: Principal = Depends(current_principal),
    session: Session = Depends(tenant_session),
) -> list[InvoiceOut]:
    _contract_or_404(session, contract_id)
    _money_or_403(principal)
    rows = session.scalars(
        select(Invoice)
        .where(Invoice.contract_id == contract_id)
        .order_by(Invoice.invoice_date.desc())
    ).all()
    return [invoice_out(session, i) for i in rows]


@router.get("/contracts/{contract_id}/spend", response_model=SpendOut)
def contract_spend(
    contract_id: uuid.UUID,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> SpendOut:
    _contract_or_404(session, contract_id)
    return SpendOut(by_year=feed.spend_by_year(session, contract_id))


@router.post("/invoices/{invoice_id}/match", response_model=InvoiceOut)
def match_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceMatchIn,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> InvoiceOut:
    inv = _invoice_or_404(session, invoice_id)
    if inv.contract_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"error": "Fakturaen er allerede matchet", "code": "already_matched"},
        )
    c = _contract_or_404(session, body.contract_id)
    feed.choose_contract(session, inv=inv, contract=c, principal=principal)
    return invoice_out(session, inv)


@router.post("/invoices/{invoice_id}/approve", response_model=InvoiceOut)
def approve_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceDecisionIn | None = None,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> InvoiceOut:
    inv = _invoice_or_404(session, invoice_id)
    try:
        feed.decide_invoice(
            session,
            inv=inv,
            principal=principal,
            approve=True,
            comment=body.comment if body else None,
        )
    except feed.ImportRejected as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return invoice_out(session, inv)


@router.post("/invoices/{invoice_id}/reject", response_model=InvoiceOut)
def reject_invoice(
    invoice_id: uuid.UUID,
    body: InvoiceDecisionIn,
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> InvoiceOut:
    inv = _invoice_or_404(session, invoice_id)
    try:
        feed.decide_invoice(
            session, inv=inv, principal=principal, approve=False, comment=body.comment
        )
    except feed.ImportRejected as e:
        raise HTTPException(e.status, detail={"error": str(e), "code": e.code}) from e
    return invoice_out(session, inv)


@router.get("/import-errors", response_model=list[ImportErrorOut])
def import_errors(
    principal: Principal = Depends(require(access.OKONOMI)),
    session: Session = Depends(tenant_session),
) -> list[ImportErrorOut]:
    rows = session.scalars(
        select(ImportError_)
        .where(ImportError_.resolved_at.is_(None))
        .order_by(ImportError_.created_at.desc())
        .limit(200)
    ).all()
    return [ImportErrorOut.model_validate(e) for e in rows]
