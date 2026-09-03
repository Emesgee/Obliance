"""Pydantic models = the API contract (ADR-0023 §1: one schema definition,
consumed by the generated TypeScript client)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.models import (
    AgreementForm,
    Confidentiality,
    ContractPhase,
    ContractStatus,
    ContractTier,
    DocType,
    IngestStatus,
    MemberRole,
    VersionStatus,
)

# ---- auth ----------------------------------------------------------------------


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    # Reserved for increment 2 (bidflow ADR-0065): step-up token when MFA is enrolled.
    mfa_required: bool = False


class MeOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    org_id: uuid.UUID
    org_name: str
    role: MemberRole
    permissions: list[str]


# ---- contracts (ADR-0001) --------------------------------------------------------


class ContractOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reference: str
    contract_number: str | None
    name: str
    agreement_form: AgreementForm | None
    category: str | None
    department: str | None
    phase: ContractPhase
    status: ContractStatus
    tier: ContractTier | None
    confidentiality: Confidentiality
    owner_id: uuid.UUID | None
    manager_id: uuid.UUID | None
    start_date: date | None
    end_date: date | None
    # Financial fields are None (not 0) when the caller lacks `okonomi` — ADR-0003 §2.
    total_value: Decimal | None
    annual_value: Decimal | None


class ContractCreate(BaseModel):
    reference: str = Field(min_length=1, max_length=64, pattern=r"^[A-ZÆØÅ]{1,3}-\d{4}-\d{3,}$")
    name: str = Field(min_length=1, max_length=300)
    agreement_form: AgreementForm | None = None
    category: str | None = None
    department: str | None = None
    phase: ContractPhase = ContractPhase.forberedelse
    tier: ContractTier | None = None
    confidentiality: Confidentiality = Confidentiality.intern
    owner_id: uuid.UUID | None = None
    manager_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    total_value: Decimal | None = None
    annual_value: Decimal | None = None


class ContractListOut(BaseModel):
    items: list[ContractOut]
    total: int


# ---- documents (ADR-0005/0006) ----------------------------------------------------


class DocumentVersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    version_no: int
    status: VersionStatus
    ingest_status: IngestStatus
    ingest_error: str | None
    original_filename: str
    mime: str
    size_bytes: int
    sha256: str
    page_count: int | None
    ocr_applied: bool
    uploaded_by: uuid.UUID | None
    uploaded_at: datetime
    made_current_by: uuid.UUID | None
    made_current_at: datetime | None
    effective_note: str | None


class DocumentOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    doc_type: DocType
    title: str
    current_version_id: uuid.UUID | None
    amends_document_id: uuid.UUID | None
    created_at: datetime
    versions: list[DocumentVersionOut]


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_pdf: int
    page_printed: str | None
    text: str


class ClauseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clause_ref: str
    heading: str
    page_pdf: int
    char_start: int
    char_end: int
