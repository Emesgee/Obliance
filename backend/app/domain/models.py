"""SQLAlchemy models — mirror of migrations/versions/0001_foundation.py.

Migrations are the source of truth for DDL (policies, grants, enums); these
classes exist for typed queries and for Alembic autogenerate diffs. Money is
numeric(14,2) in DKK (ADR-0001); enums are native Postgres types.
"""

from __future__ import annotations

import enum
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Interval,
    Numeric,
    PrimaryKeyConstraint,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---- enums (names must match the CREATE TYPE statements in 0001) --------------


class MemberRole(enum.StrEnum):
    systemadministrator = "systemadministrator"
    contract_manager = "contract_manager"
    contract_owner = "contract_owner"
    procurement_manager = "procurement_manager"
    legal_compliance = "legal_compliance"
    finance_controller = "finance_controller"
    business_user = "business_user"
    auditor = "auditor"


class ContractPhase(enum.StrEnum):
    forberedelse = "forberedelse"
    udbud = "udbud"
    evaluering = "evaluering"
    kontrahering = "kontrahering"
    aktiv_drift = "aktiv_drift"
    genudbud_exit = "genudbud_exit"


class ContractStatus(enum.StrEnum):
    kladde = "kladde"
    aktiv = "aktiv"
    udloebet = "udloebet"
    opsagt = "opsagt"
    arkiveret = "arkiveret"


class AgreementForm(enum.StrEnum):
    serviceaftale = "serviceaftale"
    rammeaftale = "rammeaftale"
    leveringsaftale = "leveringsaftale"
    databehandleraftale = "databehandleraftale"
    andet = "andet"


class ContractTier(enum.StrEnum):
    N1 = "N1"
    N2 = "N2"
    N3 = "N3"
    N4 = "N4"


class Confidentiality(enum.StrEnum):
    intern = "intern"
    fortrolig = "fortrolig"


class RiskLevel(enum.StrEnum):
    lav = "lav"
    mellem = "mellem"
    hoej = "hoej"


def _pg_enum(cls: type[enum.StrEnum], name: str) -> Enum:
    return Enum(cls, name=name, native_enum=True, values_callable=lambda e: [m.value for m in e])


# ---- identity (no RLS — read before a tenant context exists) -------------------


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    # Deactivation, never deletion (bidflow ADR-0067; Responsibility Gap reads this).
    deactivated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ---- auth (ADR-0024). NULL password_hash = SSO-only or not yet set up.
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RolePermission(Base):
    """ADR-0003: the role × permission matrix as data. Seeded by migration 0002
    from app.core.access.MATRIX; a test asserts the two never drift."""

    __tablename__ = "role_permissions"
    __table_args__ = (PrimaryKeyConstraint("role", "permission"),)

    role: Mapped[MemberRole] = mapped_column(_pg_enum(MemberRole, "member_role"))
    permission: Mapped[str] = mapped_column(Text)


class OrganizationMember(Base):
    __tablename__ = "organization_members"
    __table_args__ = (PrimaryKeyConstraint("organization_id", "profile_id"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE")
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT")
    )
    # Exactly one role per membership (ADR-0003).
    role: Mapped[MemberRole] = mapped_column(_pg_enum(MemberRole, "member_role"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ---- contracts aggregate (ADR-0001) --------------------------------------------


class Contract(Base):
    __tablename__ = "contracts"
    __table_args__ = (UniqueConstraint("organization_id", "reference"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    # Human reference, assigned at creation, never changed (ADR-0001 §Identitet).
    reference: Mapped[str] = mapped_column(Text, nullable=False)
    contract_number: Mapped[str | None] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    agreement_form: Mapped[AgreementForm | None] = mapped_column(
        _pg_enum(AgreementForm, "agreement_form")
    )
    category: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    phase: Mapped[ContractPhase] = mapped_column(
        _pg_enum(ContractPhase, "contract_phase"),
        nullable=False,
        server_default=ContractPhase.forberedelse.value,
    )
    status: Mapped[ContractStatus] = mapped_column(
        _pg_enum(ContractStatus, "contract_status"),
        nullable=False,
        server_default=ContractStatus.kladde.value,
    )
    tier: Mapped[ContractTier | None] = mapped_column(_pg_enum(ContractTier, "contract_tier"))
    confidentiality: Mapped[Confidentiality] = mapped_column(
        _pg_enum(Confidentiality, "confidentiality"),
        nullable=False,
        server_default=Confidentiality.intern.value,
    )
    risk_level: Mapped[RiskLevel | None] = mapped_column(_pg_enum(RiskLevel, "risk_level"))
    # [{type, kadence, naeste, deltagere}] — structured so agents can read "next meeting".
    governance_meetings: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    governance_note: Mapped[str | None] = mapped_column(Text)
    # CO and CM — mirrored into contract_roles later (ADR-0021 §2).
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    manager_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    notice_period: Mapped[timedelta | None] = mapped_column(Interval)
    # Stored, not derived: it is written in the contract and may differ from end − notice.
    last_termination_date: Mapped[date | None] = mapped_column(Date)
    options: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    price_regulation: Mapped[str | None] = mapped_column(Text)
    price_regulation_date: Mapped[date | None] = mapped_column(Date)
    total_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    annual_value: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ContractBudget(Base):
    """budget2026 in the mockup → one row per (contract, year). Spend is derived."""

    __tablename__ = "contract_budgets"
    __table_args__ = (PrimaryKeyConstraint("contract_id", "year"),)

    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    year: Mapped[int] = mapped_column(Integer)
    budget: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class ContractAccess(Base):
    """Access list for fortrolig contracts (ADR-0002 level 2). One active row per
    (contract, profile) — enforced by a partial unique index in the migration."""

    __tablename__ = "contract_access"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    profile_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False
    )
    granted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reason: Mapped[str | None] = mapped_column(Text)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---- documents (ADR-0005/0006) ------------------------------------------------------


class DocType(enum.StrEnum):
    """Agreement basis (first five) vs documentation (last three) — ADR-0006 §1."""

    hovedkontrakt = "hovedkontrakt"
    bilag = "bilag"
    prisbilag = "prisbilag"
    databehandleraftale = "databehandleraftale"
    tillaeg = "tillaeg"
    rapport = "rapport"
    korrespondance = "korrespondance"
    andet = "andet"


AGREEMENT_DOC_TYPES: frozenset[DocType] = frozenset(
    {
        DocType.hovedkontrakt,
        DocType.bilag,
        DocType.prisbilag,
        DocType.databehandleraftale,
        DocType.tillaeg,
    }
)


class VersionStatus(enum.StrEnum):
    kladde = "kladde"
    gaeldende = "gaeldende"
    historisk = "historisk"


class IngestStatus(enum.StrEnum):
    afventer = "afventer"
    koerer = "koerer"
    ok = "ok"
    fejlet = "fejlet"


class ContractDocument(Base):
    """Logical document (child of the contract). Files live in document_versions."""

    __tablename__ = "contract_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    doc_type: Mapped[DocType] = mapped_column(_pg_enum(DocType, "doc_type"), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    # Exactly one gaeldende version per document (partial unique index in 0003).
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # tillaeg only: the document this amendment supplements (ADR-0006 afklaring 3).
    amends_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_documents.id", ondelete="RESTRICT")
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DocumentVersion(Base):
    """An immutable uploaded file. Never updated in content, never deleted by the
    app role (ADR-0006 §1) — only ADR-0012's retention role may remove one."""

    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version_no"),
        UniqueConstraint("document_id", "sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_documents.id", ondelete="RESTRICT"), nullable=False
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    mime: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    status: Mapped[VersionStatus] = mapped_column(
        _pg_enum(VersionStatus, "version_status"),
        nullable=False,
        server_default=VersionStatus.kladde.value,
    )
    made_current_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    made_current_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Rendered PDF for non-PDF uploads (bidflow ADR-0049), lazily.
    pdf_storage_key: Mapped[str | None] = mapped_column(Text)
    ingest_status: Mapped[IngestStatus] = mapped_column(
        _pg_enum(IngestStatus, "ingest_status"),
        nullable=False,
        server_default=IngestStatus.afventer.value,
    )
    ingest_error: Mapped[str | None] = mapped_column(Text)
    ocr_applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    page_count: Mapped[int | None] = mapped_column(Integer)
    effective_note: Mapped[str | None] = mapped_column(Text)


class DocumentPage(Base):
    """Page text per version — the substrate for citation verification (ADR-0005)."""

    __tablename__ = "document_pages"
    __table_args__ = (PrimaryKeyConstraint("version_id", "page_pdf"),)

    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    page_pdf: Mapped[int] = mapped_column(Integer)
    # The document's own printed page label, if one could be read (bidflow ADR-0062).
    page_printed: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class DocumentClause(Base):
    """Heuristic clause index (ADR-0005 §2): '8.2', 'Bilag 5', 'Tabel 1', '§ 3'."""

    __tablename__ = "document_clauses"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    clause_ref: Mapped[str] = mapped_column(Text, nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    page_pdf: Mapped[int] = mapped_column(Integer, nullable=False)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False)


# ---- AI layer (migration 0004: ADR-0004, 0010, 0011, 0014) ------------------------------


class ActorType(enum.StrEnum):
    human = "human"
    agent = "agent"
    system = "system"


class AuditAction(enum.StrEnum):
    """Closed taxonomy (ADR-0011 §3). A new action is a migration, on purpose."""

    login = "login"
    login_failed = "login_failed"
    contract_created = "contract_created"
    contract_updated = "contract_updated"
    contract_status_changed = "contract_status_changed"
    document_uploaded = "document_uploaded"
    document_version_made_current = "document_version_made_current"
    ai_suggestion_created = "ai_suggestion_created"
    ai_suggestion_approved = "ai_suggestion_approved"
    ai_suggestion_rejected = "ai_suggestion_rejected"
    ai_suggestion_expired = "ai_suggestion_expired"
    ai_query = "ai_query"
    agent_run_completed = "agent_run_completed"
    agent_run_failed = "agent_run_failed"
    # added in migration 0005
    obligation_created = "obligation_created"
    obligation_updated = "obligation_updated"
    obligation_status_changed = "obligation_status_changed"
    citations_reresolved = "citations_reresolved"
    # added in migration 0006
    risk_created = "risk_created"
    risk_updated = "risk_updated"
    risk_status_changed = "risk_status_changed"


class SuggestionKind(enum.StrEnum):
    create = "create"
    update = "update"


class SuggestionSubject(enum.StrEnum):
    obligation = "obligation"
    risk = "risk"
    raci_entry = "raci_entry"
    invoice_finding = "invoice_finding"
    contract_intake = "contract_intake"
    sla_breach = "sla_breach"
    task = "task"


class SuggestionStatus(enum.StrEnum):
    foreslaaet = "foreslaaet"
    afventer_2_signatur = "afventer_2_signatur"
    godkendt = "godkendt"
    afvist = "afvist"
    foraeldet = "foraeldet"


OPEN_SUGGESTION_STATUSES = (SuggestionStatus.foreslaaet, SuggestionStatus.afventer_2_signatur)


class Confidence(enum.StrEnum):
    hoej = "hoej"
    mellem = "mellem"
    lav = "lav"


class AgentRunStatus(enum.StrEnum):
    koerer = "koerer"
    ok = "ok"
    fejlet = "fejlet"
    sprunget_over = "sprunget_over"


class AgentTrigger(enum.StrEnum):
    schedule = "schedule"
    event = "event"
    manual = "manual"


class AuditLog(Base):
    """Append-only (grant-enforced). Labels are frozen at write time — ADR-0011 §2."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    actor_type: Mapped[ActorType] = mapped_column(_pg_enum(ActorType, "actor_type"), nullable=False)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(Text, nullable=False)
    actor_role: Mapped[str | None] = mapped_column(Text)
    action: Mapped[AuditAction] = mapped_column(
        _pg_enum(AuditAction, "audit_action"), nullable=False
    )
    object_kind: Mapped[str] = mapped_column(Text, nullable=False)
    object_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    object_label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    details: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    request_id: Mapped[str | None] = mapped_column(Text)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    prev_hash: Mapped[str | None] = mapped_column(Text)
    row_hash: Mapped[str] = mapped_column(Text, nullable=False)


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    trigger: Mapped[AgentTrigger] = mapped_column(
        _pg_enum(AgentTrigger, "agent_trigger"), nullable=False
    )
    trigger_ref: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        _pg_enum(AgentRunStatus, "agent_run_status"),
        nullable=False,
        server_default=AgentRunStatus.koerer.value,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    contracts_scanned: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suggestions_created: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    suggestions_updated: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    model: Mapped[str | None] = mapped_column(Text)
    task: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_dkk: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    batch_id: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    error_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class AiSuggestion(Base):
    """The only table an agent writes proposals to (ADR-0004 §1)."""

    __tablename__ = "ai_suggestions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    agent_key: Mapped[str] = mapped_column(Text, nullable=False)
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    kind: Mapped[SuggestionKind] = mapped_column(
        _pg_enum(SuggestionKind, "suggestion_kind"), nullable=False
    )
    subject_kind: Mapped[SuggestionSubject] = mapped_column(
        _pg_enum(SuggestionSubject, "suggestion_subject"), nullable=False
    )
    subject_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[Confidence] = mapped_column(
        _pg_enum(Confidence, "confidence"), nullable=False
    )
    rationale: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    citations: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    amount_dkk: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[SuggestionStatus] = mapped_column(
        _pg_enum(SuggestionStatus, "suggestion_status"),
        nullable=False,
        server_default=SuggestionStatus.foreslaaet.value,
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_comment: Mapped[str | None] = mapped_column(Text)
    materialized_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class UsageEvent(Base):
    """One row per operation, price frozen at write time (ADR-0014)."""

    __tablename__ = "usage_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[ActorType] = mapped_column(_pg_enum(ActorType, "actor_type"), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    contract_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT")
    )
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(Text, nullable=False)
    backend: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    batch: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    inference_geo: Mapped[str | None] = mapped_column(Text)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    cost_dkk: Mapped[Decimal | None] = mapped_column(Numeric(12, 4))
    dkk_per_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))


class AgentSetting(Base):
    __tablename__ = "agent_settings"
    __table_args__ = (PrimaryKeyConstraint("organization_id", "agent_key"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    agent_key: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    schedule_override: Mapped[str | None] = mapped_column(Text)
    paused_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    paused_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    paused_reason: Mapped[str | None] = mapped_column(Text)


# ---- obligations + citations (migration 0005: ADR-0001 child, ADR-0005 §1) ------------------


class ObligationParty(enum.StrEnum):
    kunde = "kunde"
    leverandoer = "leverandoer"
    begge = "begge"


class ObligationFrequency(enum.StrEnum):
    engang = "engang"
    loebende = "loebende"
    maanedlig = "maanedlig"
    kvartalsvis = "kvartalsvis"
    halvaarlig = "halvaarlig"
    aarlig = "aarlig"
    ved_haendelse = "ved_haendelse"


class Criticality(enum.StrEnum):
    lav = "lav"
    mellem = "mellem"
    hoej = "hoej"
    kritisk = "kritisk"


class ObligationStatus(enum.StrEnum):
    """Stored status. `forsinket` is derived (aaben + deadline passed) — ADR-0001."""

    aaben = "aaben"
    opfyldt = "opfyldt"
    lukket = "lukket"


class Origin(enum.StrEnum):
    human = "human"
    ai = "ai"


class CitationKind(enum.StrEnum):
    document = "document"
    record = "record"


class SuccessorStatus(enum.StrEnum):
    uaendret = "uaendret"
    flyttet = "flyttet"
    ikke_fundet = "ikke_fundet"


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (UniqueConstraint("contract_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # shown as F-<seq>
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    party: Mapped[ObligationParty] = mapped_column(
        _pg_enum(ObligationParty, "obligation_party"), nullable=False
    )
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    frequency: Mapped[ObligationFrequency] = mapped_column(
        _pg_enum(ObligationFrequency, "obligation_frequency"), nullable=False
    )
    deadline: Mapped[date | None] = mapped_column(Date)
    criticality: Mapped[Criticality] = mapped_column(
        _pg_enum(Criticality, "criticality"), nullable=False
    )
    status: Mapped[ObligationStatus] = mapped_column(
        _pg_enum(ObligationStatus, "obligation_status"),
        nullable=False,
        server_default=ObligationStatus.aaben.value,
    )
    consequence: Mapped[str | None] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[Origin] = mapped_column(_pg_enum(Origin, "origin_kind"), nullable=False)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    fulfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Citation(Base):
    """A source as a structured object (ADR-0005 §1). One row per fragment; points at
    a document *version*, never rewritten — re-resolution adds successors (§5)."""

    __tablename__ = "citations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    subject_kind: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    kind: Mapped[CitationKind] = mapped_column(
        _pg_enum(CitationKind, "citation_kind"), nullable=False
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contract_documents.id", ondelete="RESTRICT")
    )
    document_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="RESTRICT")
    )
    page_pdf: Mapped[int | None] = mapped_column(Integer)
    page_printed: Mapped[str | None] = mapped_column(Text)
    clause_ref: Mapped[str | None] = mapped_column(Text)
    quote: Mapped[str | None] = mapped_column(Text)
    quote_hash: Mapped[str | None] = mapped_column(Text)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    record_kind: Mapped[str | None] = mapped_column(Text)
    record_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    label: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    successor_status: Mapped[SuccessorStatus | None] = mapped_column(
        _pg_enum(SuccessorStatus, "successor_status")
    )
    successor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("citations.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


# ---- risks (migration 0006: ADR-0001 child, mockup's Risiko) ------------------------------


class RiskCategory(enum.StrEnum):
    operationel = "operationel"
    gdpr = "gdpr"
    kommerciel = "kommerciel"
    udbudsretlig = "udbudsretlig"
    compliance = "compliance"
    juridisk = "juridisk"
    leverandoer = "leverandoer"
    andet = "andet"


class RiskStatus(enum.StrEnum):
    aaben = "aaben"
    under_haandtering = "under_haandtering"
    lukket = "lukket"


def risk_level_for(score: int) -> RiskLevel:
    """Derived from sandsynlighed × konsekvens (1–25); never stored (ADR-0001)."""
    if score >= 13:
        return RiskLevel.hoej
    if score >= 6:
        return RiskLevel.mellem
    return RiskLevel.lav


class Risk(Base):
    __tablename__ = "risks"
    __table_args__ = (UniqueConstraint("contract_id", "seq"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # shown as R-<seq>
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[RiskCategory] = mapped_column(
        _pg_enum(RiskCategory, "risk_category"), nullable=False
    )
    probability: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–5
    consequence: Mapped[int] = mapped_column(Integer, nullable=False)  # 1–5
    status: Mapped[RiskStatus] = mapped_column(
        _pg_enum(RiskStatus, "risk_status"), nullable=False, server_default=RiskStatus.aaben.value
    )
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    deadline: Mapped[date | None] = mapped_column(Date)
    mitigation: Mapped[str | None] = mapped_column(Text)  # afværgelse
    note: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[Origin] = mapped_column(_pg_enum(Origin, "origin_kind"), nullable=False)
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    approved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
