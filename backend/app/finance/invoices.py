"""Inbound invoice feed — ADR-0018. Parse → normalise → fingerprint → match → check.

    parse_file(name, data) -> (rows, errors)     CSV (;, comma decimals) or xlsx (no formulas)
    import_rows(session, ...) -> ImportReport     idempotent: dup / superseded / new
    match_invoice(session, inv, actor)            reference → rule → suggestion (matchkø)
    check_invoice(session, inv, actor)            lines vs price_terms, in code (ADR-0013)
    approve_invoice / reject_invoice              the human's decision — nothing is written back

Nothing here calls the ERP, ever (§1). Nothing here calls a model: the file is
structured, and the comparison is arithmetic.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import suggestions
from app.core import audit
from app.core.auth import Principal
from app.domain.models import (
    OPEN_SUGGESTION_STATUSES,
    AiSuggestion,
    AuditAction,
    ClaimType,
    Confidence,
    Contract,
    ContractStatus,
    ControlResult,
    ImportError_,
    Invoice,
    InvoiceLine,
    InvoiceSource,
    InvoiceStatus,
    MatchedBy,
    PriceTerm,
    SourceKind,
    SuggestionKind,
    SuggestionStatus,
    SuggestionSubject,
    Supplier,
)
from app.finance import penalties, service

# Fixed column schema (Danish Excel: semicolon, comma decimals). One row per line.
COLUMNS = (
    "fakturanr",
    "fakturadato",
    "forfaldsdato",
    "leverandoer_cvr",
    "kontraktreference",
    "linje",
    "beskrivelse",
    "antal",
    "enhed",
    "enhedspris",
    "linjetotal",
    "periode_fra",
    "periode_til",
    "produktref",
)
REQUIRED = (
    "fakturanr",
    "fakturadato",
    "leverandoer_cvr",
    "linje",
    "beskrivelse",
    "antal",
    "enhedspris",
)
MAX_ROWS = 20_000
MAX_BYTES = 10 * 1024 * 1024
FILE_SOURCE_NAME = "Filimport"


class ImportRejected(ValueError):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class RowError:
    row_no: int
    reason: str
    raw: dict[str, Any]


@dataclass
class ImportReport:
    received: int = 0
    new: int = 0
    updated: int = 0
    superseded: int = 0
    rejected: int = 0
    matched: int = 0
    queued: int = 0
    errors: list[RowError] = field(default_factory=list)
    invoice_ids: list[uuid.UUID] = field(default_factory=list)


# ---- parsing ----------------------------------------------------------------------------------


def _dec(v: Any) -> Decimal | None:
    if v is None:
        return None
    s = str(v).strip().replace(" ", "").replace(" ", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")  # Danish thousands separator: 3.496 is three thousand
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _date(v: Any) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def parse_file(name: str, data: bytes) -> list[dict[str, Any]]:
    """Rows as dicts keyed by COLUMNS, values as strings/dates. Excel cells are read
    as cached values — formulas are never evaluated (ADR-0016)."""
    if len(data) > MAX_BYTES:
        raise ImportRejected("too_large", "Filen er større end 10 MB", 413)
    lower = name.lower()
    rows: list[dict[str, Any]] = []
    if lower.endswith(".xlsx"):
        import openpyxl

        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        header: list[str] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                header = [_cell(c).lower() for c in row]
                continue
            if all(c is None for c in row):
                continue
            rows.append({header[j]: (row[j] if j < len(row) else None) for j in range(len(header))})
            if len(rows) > MAX_ROWS:
                raise ImportRejected("too_many_rows", f"Filen har flere end {MAX_ROWS} rækker", 413)
    else:
        text = data.decode("utf-8-sig", errors="replace")
        sample = text[:2048]
        delim = ";" if sample.count(";") >= sample.count(",") else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delim)
        reader.fieldnames = [f.strip().lower() for f in (reader.fieldnames or [])]
        for r in reader:
            if not any((v or "").strip() for v in r.values() if isinstance(v, str)):
                continue
            rows.append({k: (v.strip() if isinstance(v, str) else v) for k, v in r.items() if k})
            if len(rows) > MAX_ROWS:
                raise ImportRejected("too_many_rows", f"Filen har flere end {MAX_ROWS} rækker", 413)
    if rows and not set(REQUIRED) <= set(rows[0].keys()):
        missing = sorted(set(REQUIRED) - set(rows[0].keys()))
        raise ImportRejected("bad_schema", f"Kolonner mangler: {', '.join(missing)}")
    return rows


def fingerprint(cvr: str, number: str, total: Decimal, invoice_date: date) -> str:
    raw = "|".join([cvr.strip(), number.strip(), f"{total:.2f}", invoice_date.isoformat()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---- import -------------------------------------------------------------------------------------


@dataclass
class _Line:
    line_no: int
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    line_total: Decimal
    period_from: date | None
    period_to: date | None
    product_ref: str | None
    raw: dict[str, Any]


@dataclass
class _Header:
    number: str
    invoice_date: date
    due_date: date | None
    cvr: str
    reference: str | None
    lines: list[_Line] = field(default_factory=list)
    first_row: int = 0

    @property
    def total(self) -> Decimal:
        return sum((ln.line_total for ln in self.lines), Decimal("0")).quantize(Decimal("0.01"))


def _group(rows: list[dict[str, Any]], report: ImportReport) -> list[_Header]:
    by_key: dict[tuple[str, str], _Header] = {}
    for i, r in enumerate(rows, start=2):  # header is row 1
        raw = {k: _cell(v) for k, v in r.items()}
        number = _cell(r.get("fakturanr"))
        cvr = _cell(r.get("leverandoer_cvr")).replace(" ", "")
        inv_date = _date(r.get("fakturadato"))
        qty = _dec(r.get("antal"))
        price = _dec(r.get("enhedspris"))
        line_no = _dec(r.get("linje"))
        problems = []
        if not number:
            problems.append("fakturanr mangler")
        if inv_date is None:
            problems.append("fakturadato kan ikke læses")
        if not cvr.isdigit() or len(cvr) != 8:
            problems.append("leverandoer_cvr skal være 8 cifre")
        if qty is None:
            problems.append("antal kan ikke læses")
        if price is None:
            problems.append("enhedspris kan ikke læses")
        if line_no is None:
            problems.append("linje kan ikke læses")
        if problems:
            report.errors.append(RowError(i, "; ".join(problems), raw))
            continue
        assert (
            inv_date is not None and qty is not None and price is not None and line_no is not None
        )
        total = _dec(r.get("linjetotal"))
        if total is None:
            total = qty * price
        key = (cvr, number)
        h = by_key.get(key)
        if h is None:
            h = _Header(
                number=number,
                invoice_date=inv_date,
                due_date=_date(r.get("forfaldsdato")),
                cvr=cvr,
                reference=_cell(r.get("kontraktreference")) or None,
                first_row=i,
            )
            by_key[key] = h
        h.lines.append(
            _Line(
                line_no=int(line_no),
                description=_cell(r.get("beskrivelse")),
                quantity=qty,
                unit=_cell(r.get("enhed")) or None,
                unit_price=price,
                line_total=total.quantize(Decimal("0.01")),
                period_from=_date(r.get("periode_fra")),
                period_to=_date(r.get("periode_til")),
                product_ref=_cell(r.get("produktref")) or None,
                raw=raw,
            )
        )
    return list(by_key.values())


def file_source(session: Session, org_id: uuid.UUID) -> InvoiceSource:
    src = session.scalars(
        select(InvoiceSource).where(
            InvoiceSource.organization_id == org_id, InvoiceSource.kind == SourceKind.file_import
        )
    ).first()
    if src is None:
        src = InvoiceSource(
            organization_id=org_id, kind=SourceKind.file_import, name=FILE_SOURCE_NAME
        )
        session.add(src)
        session.flush()
    return src


def import_rows(
    session: Session,
    *,
    principal: Principal,
    file_name: str,
    rows: list[dict[str, Any]],
    source: InvoiceSource | None = None,
) -> ImportReport:
    """One transaction per file (§4): received / new / updated / superseded / rejected."""
    org_id = principal.org_id
    src = source or file_source(session, org_id)
    report = ImportReport(received=len(rows))
    headers = _group(rows, report)
    suppliers = {
        s.cvr: s
        for s in session.scalars(select(Supplier).where(Supplier.organization_id == org_id))
    }
    now = datetime.now(UTC)
    actor = audit.human(principal)
    for h in headers:
        supplier = suppliers.get(h.cvr)
        if supplier is None:
            # §7: an unknown CVR is an error-queue entry, never a new supplier
            report.errors.append(
                RowError(
                    h.first_row,
                    f"ukendt leverandør-CVR {h.cvr} — opret leverandøren først",
                    {"fakturanr": h.number, "cvr": h.cvr},
                )
            )
            continue
        fp = fingerprint(h.cvr, h.number, h.total, h.invoice_date)
        existing = session.scalars(
            select(Invoice).where(Invoice.organization_id == org_id, Invoice.fingerprint == fp)
        ).first()
        if existing is not None:
            existing.last_seen_at = now
            existing.raw_payload = {"file": file_name, "rows": [ln.raw for ln in h.lines]}
            report.updated += 1
            continue
        same_number = session.scalars(
            select(Invoice).where(
                Invoice.organization_id == org_id,
                Invoice.supplier_id == supplier.id,
                Invoice.invoice_number == h.number,
                Invoice.status != InvoiceStatus.erstattet,
            )
        ).first()
        inv = Invoice(
            organization_id=org_id,
            source_id=src.id,
            supplier_id=supplier.id,
            invoice_number=h.number,
            invoice_date=h.invoice_date,
            due_date=h.due_date,
            total_amount=h.total,
            contract_reference=h.reference,
            fingerprint=fp,
            raw_payload={"file": file_name, "rows": [ln.raw for ln in h.lines]},
            first_seen_at=now,
            last_seen_at=now,
            supersedes_invoice_id=same_number.id if same_number else None,
        )
        session.add(inv)
        session.flush()
        for ln in h.lines:
            session.add(
                InvoiceLine(
                    organization_id=org_id,
                    invoice_id=inv.id,
                    line_no=ln.line_no,
                    description=ln.description,
                    quantity=ln.quantity,
                    unit=ln.unit,
                    unit_price=ln.unit_price,
                    line_total=ln.line_total,
                    period_from=ln.period_from,
                    period_to=ln.period_to,
                    product_ref=ln.product_ref,
                )
            )
        session.flush()
        if same_number is not None:
            same_number.status = InvoiceStatus.erstattet
            report.superseded += 1
        report.new += 1
        report.invoice_ids.append(inv.id)
        outcome = match_invoice(session, inv, actor)
        if outcome == "matched":
            report.matched += 1
            check_invoice(session, inv, actor)
        else:
            report.queued += 1
    for e in report.errors:
        session.add(
            ImportError_(
                organization_id=org_id,
                source_id=src.id,
                file_name=file_name,
                row_no=e.row_no,
                reason=e.reason,
                raw=e.raw,
            )
        )
    report.rejected = len(report.errors)
    src.last_sync_at = now
    src.last_sync_status = "ok" if not report.errors else f"{report.rejected} afvist"
    session.flush()
    audit.record(
        session,
        org_id=org_id,
        action=AuditAction.invoices_imported,
        actor=actor,
        object_kind="invoice_source",
        object_id=src.id,
        object_label=f"{src.name} · {file_name}",
        details={
            "received": report.received,
            "new": report.new,
            "updated": report.updated,
            "superseded": report.superseded,
            "rejected": report.rejected,
        },
    )
    return report


# ---- matching (§5) ----------------------------------------------------------------------------


def _set_contract(
    session: Session, inv: Invoice, contract: Contract, by: MatchedBy, actor: audit.Actor
) -> None:
    inv.contract_id = contract.id
    inv.matched_by = by
    inv.status = InvoiceStatus.matchet
    for ln in session.scalars(select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id)):
        ln.contract_id = contract.id
    session.flush()
    audit.record(
        session,
        org_id=inv.organization_id,
        action=AuditAction.invoice_matched,
        actor=actor,
        object_kind="invoice",
        object_id=inv.id,
        object_label=f"Faktura {inv.invoice_number}",
        contract_id=contract.id,
        details={"matched_by": by.value},
    )


def _candidates(session: Session, inv: Invoice) -> list[Contract]:
    rows = session.scalars(
        select(Contract).where(
            Contract.supplier_id == inv.supplier_id,
            Contract.status.in_((ContractStatus.aktiv, ContractStatus.kladde)),
        )
    ).all()

    def active_on(c: Contract) -> bool:
        if c.start_date and inv.invoice_date < c.start_date:
            return False
        return not (c.end_date and inv.invoice_date > c.end_date)

    return [c for c in rows if active_on(c)]


def match_invoice(session: Session, inv: Invoice, actor: audit.Actor) -> str:
    """Returns 'matched' or 'queued'. Step 3 writes an invoice_match proposal only when
    there are candidates to choose between; no candidates = plain queue entry."""
    if inv.contract_reference:
        ref = inv.contract_reference.strip()
        c = session.scalars(
            select(Contract).where(
                Contract.organization_id == inv.organization_id,
                (func.upper(Contract.reference) == ref.upper()) | (Contract.contract_number == ref),
            )
        ).first()
        if c is not None:
            _set_contract(session, inv, c, MatchedBy.reference, actor)
            return "matched"
    cands = _candidates(session, inv)
    if len(cands) == 1:
        _set_contract(session, inv, cands[0], MatchedBy.rule, actor)
        return "matched"
    if len(cands) > 1:
        # Proposal lives on the first candidate so RLS scopes it to people who may see it.
        suggestions.upsert(
            session,
            org_id=inv.organization_id,
            contract_id=cands[0].id,
            agent_key="system",
            agent_label="System · Fakturamatch",
            agent_run_id=None,
            kind=SuggestionKind.update,
            subject_kind=SuggestionSubject.invoice_match,
            subject_id=inv.id,
            payload={
                "invoice_id": str(inv.id),
                "invoice_number": inv.invoice_number,
                "total_amount": str(inv.total_amount),
                "candidates": [
                    {"contract_id": str(c.id), "reference": c.reference, "name": c.name}
                    for c in cands
                ],
            },
            confidence=Confidence.lav,
            rationale=(
                f"{len(cands)} aktive kontrakter hos leverandøren i perioden — vælg den rigtige"
            ),
            citations=[],
            fp=suggestions.fingerprint(
                "system", cands[0].id, SuggestionSubject.invoice_match, str(inv.id)
            ),
        )
    return "queued"


def choose_contract(
    session: Session, *, inv: Invoice, contract: Contract, principal: Principal
) -> None:
    _set_contract(session, inv, contract, MatchedBy.manual, audit.human(principal))
    open_match = session.scalars(
        select(AiSuggestion).where(
            AiSuggestion.subject_kind == SuggestionSubject.invoice_match,
            AiSuggestion.subject_id == inv.id,
            AiSuggestion.status.in_(OPEN_SUGGESTION_STATUSES),
        )
    ).all()
    for s in open_match:
        s.status = SuggestionStatus.godkendt
        s.decided_by = principal.user_id
        s.decided_at = datetime.now(UTC)
        s.decision_comment = f"valgt: {contract.reference}"
    check_invoice(session, inv, audit.human(principal))


# ---- the check (§6, ADR-0013 price_deviation) ---------------------------------------------------


def _price_for(terms: Iterable[PriceTerm], line: InvoiceLine, on: date) -> PriceTerm | None:
    def valid(t: PriceTerm) -> bool:
        if t.valid_from and on < t.valid_from:
            return False
        return not (t.valid_to and on > t.valid_to)

    if line.product_ref:
        for t in terms:
            if (
                t.product_ref
                and t.product_ref.strip().lower() == line.product_ref.strip().lower()
                and valid(t)
            ):
                return t
    desc = line.description.strip().lower()
    for t in terms:
        if t.description.strip().lower() == desc and valid(t):
            return t
    return None


def check_invoice(session: Session, inv: Invoice, actor: audit.Actor) -> None:
    """Compare each line with the agreed price. Deviations become `invoice_finding`
    proposals (okonomi decides); none → 'Kontrol bestået — klar til godkendelse'."""
    if inv.contract_id is None:
        return
    terms = session.scalars(select(PriceTerm).where(PriceTerm.contract_id == inv.contract_id)).all()
    lines = session.scalars(
        select(InvoiceLine).where(InvoiceLine.invoice_id == inv.id).order_by(InvoiceLine.line_no)
    ).all()
    if not terms:
        inv.status = InvoiceStatus.kontrolleret
        inv.control_result = ControlResult.ingen_prisgrundlag
        inv.control_note = "Ingen aftalte priser på kontrakten — linjerne kunne ikke kontrolleres"
    else:
        findings = 0
        unpriced = 0
        for ln in lines:
            t = _price_for(terms, ln, inv.invoice_date)
            if t is None:
                unpriced += 1
                continue
            if ln.unit_price <= t.agreed_unit_price:
                continue
            r = penalties.price_deviation(t.agreed_unit_price, ln.unit_price, ln.quantity)
            findings += 1
            suggestions.upsert(
                session,
                org_id=inv.organization_id,
                contract_id=inv.contract_id,
                agent_key="system",
                agent_label="System · Fakturakontrol",
                agent_run_id=None,
                kind=SuggestionKind.create,
                subject_kind=SuggestionSubject.invoice_finding,
                subject_id=inv.id,
                payload={
                    "invoice_id": str(inv.id),
                    "invoice_number": inv.invoice_number,
                    "line_no": ln.line_no,
                    "description": ln.description,
                    "quantity": str(ln.quantity),
                    "agreed_unit_price": str(t.agreed_unit_price),
                    "invoiced_unit_price": str(ln.unit_price),
                    "amount": str(r.amount),
                    "basis_text": r.basis_text,
                    "price_term_id": str(t.id),
                    "recommendation": "Afvis differencen og anmod om kreditnota",
                },
                confidence=Confidence.hoej,
                rationale=(
                    f"Linje {ln.line_no}: {ln.description} afregnet til {ln.unit_price} "
                    f"mod aftalt {t.agreed_unit_price}"
                ),
                citations=[
                    {
                        "kind": "record",
                        "record_kind": "invoice_line",
                        "record_id": str(ln.id),
                        "label": f"Faktura {inv.invoice_number} · linje {ln.line_no}",
                        "verified": True,
                    }
                ],
                fp=suggestions.fingerprint(
                    "system",
                    inv.contract_id,
                    SuggestionSubject.invoice_finding,
                    str(inv.id),
                    str(ln.line_no),
                ),
                amount_dkk=None,
            )
        inv.status = InvoiceStatus.kontrolleret
        if findings:
            inv.control_result = ControlResult.afvigelse
            inv.control_note = f"{findings} linje(r) over aftalt pris" + (
                f", {unpriced} uden prisgrundlag" if unpriced else ""
            )
        else:
            inv.control_result = ControlResult.bestaaet
            inv.control_note = "Kontrol bestået — klar til godkendelse" + (
                f" ({unpriced} linje(r) uden prisgrundlag)" if unpriced else ""
            )
    session.flush()
    audit.record(
        session,
        org_id=inv.organization_id,
        action=AuditAction.invoice_checked,
        actor=actor,
        object_kind="invoice",
        object_id=inv.id,
        object_label=f"Faktura {inv.invoice_number}",
        contract_id=inv.contract_id,
        details={
            "result": inv.control_result.value if inv.control_result else None,
            "note": inv.control_note,
        },
    )


# ---- the human's decision (§6) ----------------------------------------------------------------


def decide_invoice(
    session: Session, *, inv: Invoice, principal: Principal, approve: bool, comment: str | None
) -> Invoice:
    if inv.status not in (InvoiceStatus.kontrolleret, InvoiceStatus.matchet):
        raise ImportRejected("bad_transition", f"Fakturaen er {inv.status.value}", 409)
    if not approve and len((comment or "").strip()) < 3:
        raise ImportRejected("comment_required", "Afvisning kræver en begrundelse")
    inv.status = InvoiceStatus.godkendt if approve else InvoiceStatus.afvist
    inv.decided_by = principal.user_id
    inv.decided_at = datetime.now(UTC)
    inv.decision_comment = comment
    session.flush()
    audit.record(
        session,
        org_id=inv.organization_id,
        action=AuditAction.invoice_approved if approve else AuditAction.invoice_rejected,
        actor=audit.human(principal),
        object_kind="invoice",
        object_id=inv.id,
        object_label=f"Faktura {inv.invoice_number}",
        contract_id=inv.contract_id,
        details={"amount": str(inv.total_amount), "comment": comment},
    )
    return inv


# ---- materialising a finding: the claim (ADR-0013 §3) -------------------------------------------


def materialize_finding(session: Session, s: Any, principal: Principal) -> Any:
    p = s.payload
    contract = session.get(Contract, s.contract_id)
    if contract is None:
        raise suggestions.SuggestionError("not_found", "Kontrakten findes ikke", 404)
    r = penalties.price_deviation(
        Decimal(str(p["agreed_unit_price"])),
        Decimal(str(p["invoiced_unit_price"])),
        Decimal(str(p["quantity"])),
    )
    inv = session.get(Invoice, uuid.UUID(str(p["invoice_id"])))
    claim = service.create_claim(
        session,
        contract=contract,
        claim_type=ClaimType.prisafvigelse,
        period_start=inv.invoice_date if inv else None,
        period_end=inv.invoice_date if inv else None,
        term=None,
        breach=None,
        result=penalties.Result(
            amount_uncapped=r.amount_uncapped,
            amount=r.amount,
            cap_applied=False,
            basis_text=(
                f"{r.basis_text} — Faktura {p.get('invoice_number')} · linje {p.get('line_no')}"
            ),
            inputs={
                **r.inputs,
                "invoice_id": p["invoice_id"],
                "line_no": p.get("line_no"),
                "price_term_id": p.get("price_term_id"),
            },
        ),
        actor=audit.human(principal),
        created_by=None,
    )
    return suggestions.Materialized(
        materialized_id=claim.id,
        applied=["financial_claim"],
        note=f"krav KR-{claim.seq} beregnet ({claim.amount} kr.)",
    )


def spend_by_year(session: Session, contract_id: uuid.UUID) -> dict[int, Decimal]:
    """ADR-0001/0018: forbrug derives from approved invoices only."""
    rows = session.execute(
        select(func.extract("year", Invoice.invoice_date), func.sum(Invoice.total_amount))
        .where(Invoice.contract_id == contract_id, Invoice.status == InvoiceStatus.godkendt)
        .group_by(func.extract("year", Invoice.invoice_date))
    ).all()
    return {int(y): Decimal(t) for y, t in rows}


suggestions.MATERIALIZERS[SuggestionSubject.invoice_finding] = materialize_finding
