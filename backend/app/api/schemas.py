"""Pydantic models = the API contract (ADR-0023 §1: one schema definition,
consumed by the generated TypeScript client)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, computed_field

from app.domain.models import (
    ActorType,
    AgentRunStatus,
    AgentTrigger,
    AgreementForm,
    AuditAction,
    CitationKind,
    ClaimStatus,
    ClaimType,
    Confidence,
    Confidentiality,
    ContractPhase,
    ContractStatus,
    ContractTier,
    Criticality,
    DocType,
    IngestStatus,
    KpiPeriod,
    KpiUnit,
    MeasurementSource,
    MemberRole,
    ObligationFrequency,
    ObligationParty,
    ObligationStatus,
    Origin,
    PenaltyBasis,
    PenaltyTimeUnit,
    RiskCategory,
    RiskLevel,
    RiskStatus,
    SuccessorStatus,
    SuggestionKind,
    SuggestionStatus,
    SuggestionSubject,
    TargetOperator,
    TermStatus,
    TermType,
    VersionStatus,
    risk_level_for,
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
    description: str | None = None
    notice_period: timedelta | None = Field(default=None, exclude=True)
    last_termination_date: date | None = None
    options: list[dict[str, Any]] = Field(default_factory=list)
    price_regulation: str | None = None
    price_regulation_date: date | None = None
    # Financial fields are None (not 0) when the caller lacks `okonomi` — ADR-0003 §2.
    total_value: Decimal | None
    annual_value: Decimal | None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def notice_period_days(self) -> int | None:
        return self.notice_period.days if self.notice_period is not None else None


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


# ---- AI: suggestions, runs, audit (ADR-0004/0010/0011) --------------------------------


class SuggestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    contract_id: uuid.UUID
    agent_key: str
    agent_run_id: uuid.UUID | None
    kind: SuggestionKind
    subject_kind: SuggestionSubject
    subject_id: uuid.UUID | None
    payload: dict[str, Any]
    confidence: Confidence
    rationale: str
    citations: list[dict[str, Any]]
    amount_dkk: Decimal | None
    status: SuggestionStatus
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    decision_comment: str | None
    created_at: datetime
    updated_at: datetime


class ApproveIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class RejectIn(BaseModel):
    comment: str = Field(min_length=3, max_length=2000)


class AgentRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_key: str
    contract_id: uuid.UUID | None
    trigger: AgentTrigger
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    suggestions_created: int
    suggestions_updated: int
    task: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_dkk: Decimal | None
    error: str | None


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    occurred_at: datetime
    actor_type: ActorType
    actor_label: str
    actor_role: str | None
    action: AuditAction
    object_kind: str
    object_id: uuid.UUID | None
    object_label: str
    contract_id: uuid.UUID | None
    details: dict[str, Any]


class BulkApproveIn(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=50)  # ADR-0004 afklaring 1
    comment: str | None = Field(default=None, max_length=2000)


class BulkFailure(BaseModel):
    id: uuid.UUID
    code: str
    error: str


class BulkApproveOut(BaseModel):
    approved: list[uuid.UUID]
    failed: list[BulkFailure]


# ---- obligations + citations (ADR-0001, ADR-0005) ----------------------------------------


class CitationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: CitationKind
    document_id: uuid.UUID | None
    document_version_id: uuid.UUID | None
    page_pdf: int | None
    page_printed: str | None
    clause_ref: str | None
    quote: str | None
    verified: bool
    label: str
    successor_status: SuccessorStatus | None
    successor_id: uuid.UUID | None


class ObligationOut(BaseModel):
    STORED_FIELDS: ClassVar[tuple[str, ...]] = (
        "id",
        "contract_id",
        "seq",
        "title",
        "description",
        "party",
        "responsible_id",
        "frequency",
        "deadline",
        "criticality",
        "status",
        "consequence",
        "note",
        "origin",
        "suggestion_id",
        "created_by",
        "approved_by",
        "created_at",
        "updated_at",
        "fulfilled_at",
    )

    id: uuid.UUID
    contract_id: uuid.UUID
    seq: int
    title: str
    description: str | None
    party: ObligationParty
    responsible_id: uuid.UUID | None
    frequency: ObligationFrequency
    deadline: date | None
    criticality: Criticality
    status: ObligationStatus
    consequence: str | None
    note: str | None
    origin: Origin
    suggestion_id: uuid.UUID | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    fulfilled_at: datetime | None
    citations: list[CitationOut]
    source_stale: bool  # a citation's clause is gone in the current version (ADR-0005 §5)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ref(self) -> str:
        return f"F-{self.seq}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_status(self) -> str:
        """`forsinket` is derived, never stored (ADR-0001)."""
        if (
            self.status == ObligationStatus.aaben
            and self.deadline is not None
            and self.deadline < date.today()
        ):
            return "forsinket"
        return self.status.value


class ObligationCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    party: ObligationParty = ObligationParty.leverandoer
    frequency: ObligationFrequency = ObligationFrequency.engang
    deadline: date | None = None
    criticality: Criticality = Criticality.mellem
    consequence: str | None = None
    note: str | None = None
    responsible_id: uuid.UUID | None = None


class ObligationPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    party: ObligationParty | None = None
    frequency: ObligationFrequency | None = None
    deadline: date | None = None
    criticality: Criticality | None = None
    consequence: str | None = None
    note: str | None = None
    responsible_id: uuid.UUID | None = None
    status: ObligationStatus | None = None


# ---- risks (ADR-0001 child; score and level derived) ---------------------------------------


class RiskOut(BaseModel):
    STORED_FIELDS: ClassVar[tuple[str, ...]] = (
        "id",
        "contract_id",
        "seq",
        "title",
        "description",
        "category",
        "probability",
        "consequence",
        "status",
        "responsible_id",
        "deadline",
        "mitigation",
        "note",
        "origin",
        "suggestion_id",
        "created_by",
        "approved_by",
        "created_at",
        "updated_at",
        "closed_at",
    )

    id: uuid.UUID
    contract_id: uuid.UUID
    seq: int
    title: str
    description: str | None
    category: RiskCategory
    probability: int
    consequence: int
    status: RiskStatus
    responsible_id: uuid.UUID | None
    deadline: date | None
    mitigation: str | None
    note: str | None
    origin: Origin
    suggestion_id: uuid.UUID | None
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None
    citations: list[CitationOut]
    source_stale: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ref(self) -> str:
        return f"R-{self.seq}"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> int:
        return self.probability * self.consequence

    @computed_field  # type: ignore[prop-decorator]
    @property
    def level(self) -> RiskLevel:
        return risk_level_for(self.probability * self.consequence)


class RiskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str | None = None
    category: RiskCategory = RiskCategory.andet
    probability: int = Field(default=3, ge=1, le=5)
    consequence: int = Field(default=3, ge=1, le=5)
    deadline: date | None = None
    mitigation: str | None = None
    note: str | None = None
    responsible_id: uuid.UUID | None = None


class RiskPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    category: RiskCategory | None = None
    probability: int | None = Field(default=None, ge=1, le=5)
    consequence: int | None = Field(default=None, ge=1, le=5)
    deadline: date | None = None
    mitigation: str | None = None
    note: str | None = None
    responsible_id: uuid.UUID | None = None
    status: RiskStatus | None = None


# ---- KPI / SLA, penalty terms, claims (ADR-0019, ADR-0013) ------------------------------------


class MeasurementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kpi_id: uuid.UUID
    period_start: date
    period_end: date
    value: Decimal
    source_kind: MeasurementSource
    entered_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    note: str | None
    supersedes_measurement_id: uuid.UUID | None
    superseded_by_id: uuid.UUID | None
    created_at: datetime


class KpiStatusOut(BaseModel):
    color: str  # groen · gul · roed · graa
    reason: str
    measured_period_start: date | None
    value: Decimal | None


class KpiOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    seq: int
    ref: str
    name: str
    unit: KpiUnit
    target_operator: TargetOperator
    target_value: Decimal
    target_value_high: Decimal | None
    target_text: str
    period: KpiPeriod
    warn_band: Decimal
    penalty_term_id: uuid.UUID | None
    measurement_obligation_id: uuid.UUID | None
    active: bool
    origin: Origin
    status: KpiStatusOut
    measurements: list[MeasurementOut]
    citations: list[CitationOut]


class KpiCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    unit: KpiUnit = KpiUnit.pct
    target_operator: TargetOperator = TargetOperator.gte
    target_value: Decimal
    target_value_high: Decimal | None = None
    period: KpiPeriod = KpiPeriod.maaned
    warn_band: Decimal | None = None
    penalty_term_id: uuid.UUID | None = None
    measurement_obligation_id: uuid.UUID | None = None


class KpiPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    warn_band: Decimal | None = None
    penalty_term_id: uuid.UUID | None = None
    measurement_obligation_id: uuid.UUID | None = None
    active: bool | None = None


class MeasurementIn(BaseModel):
    period_start: date
    value: Decimal
    note: str | None = Field(default=None, max_length=2000)


class SlaBreachOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kpi_id: uuid.UUID
    measurement_id: uuid.UUID
    period_start: date
    period_end: date
    target_value: Decimal
    actual_value: Decimal
    penalty_term_id: uuid.UUID | None
    claim_id: uuid.UUID | None
    note: str | None
    created_at: datetime


class PenaltyTermOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    seq: int
    ref: str
    name: str
    term_type: TermType
    trigger_description: str | None
    applies_to: str | None
    rate: Decimal | None
    tiers: list[dict[str, Any]] | None
    basis: PenaltyBasis
    basis_amount: Decimal | None
    time_unit: PenaltyTimeUnit
    cap_rate: Decimal | None
    cap_amount: Decimal | None
    status: TermStatus
    origin: Origin
    citations: list[CitationOut]


class PenaltyTermCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    term_type: TermType
    trigger_description: str | None = None
    applies_to: str | None = None
    rate: Decimal | None = None
    tiers: list[dict[str, Any]] | None = None
    basis: PenaltyBasis = PenaltyBasis.maanedligt_driftsvederlag
    basis_amount: Decimal | None = None
    time_unit: PenaltyTimeUnit = PenaltyTimeUnit.maaned
    cap_rate: Decimal | None = None
    cap_amount: Decimal | None = None


class PenaltyTermPatch(BaseModel):
    name: str | None = None
    applies_to: str | None = None
    rate: Decimal | None = None
    basis_amount: Decimal | None = None
    cap_rate: Decimal | None = None
    cap_amount: Decimal | None = None
    status: TermStatus | None = None


class ClaimOut(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    seq: int
    ref: str
    claim_type: ClaimType
    period_start: date | None
    period_end: date | None
    penalty_term_id: uuid.UUID | None
    breach_id: uuid.UUID | None
    # Money is None (not 0) without `okonomi` — ADR-0003 §2.
    amount: Decimal | None
    amount_uncapped: Decimal | None
    cap_applied: bool
    basis_text: str | None
    formula_version: str
    currency: str
    status: ClaimStatus
    requires_second_signature: bool
    created_by: uuid.UUID | None
    approved_by: uuid.UUID | None
    approved_at: datetime | None
    second_approved_by: uuid.UUID | None
    submitted_at: datetime | None
    decision_comment: str | None
    created_at: datetime
    updated_at: datetime
    citations: list[CitationOut]


class ClaimActionIn(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)


class ClaimSettleIn(BaseModel):
    status: ClaimStatus
    comment: str | None = Field(default=None, max_length=2000)


class MeasurementResultOut(BaseModel):
    measurement: MeasurementOut
    breach: SlaBreachOut | None
    claim: ClaimOut | None


class RecomputeOut(BaseModel):
    amount: Decimal
    amount_uncapped: Decimal
    basis_text: str
    formula_version: str
    matches_stored: bool


# ---- dashboard (ADR-0001 §Overblik: a roll-up, nothing stored) ------------------------------


class DashboardCounts(BaseModel):
    contracts_total: int
    kpis_total: int
    kpis_gray: int  # "KPI'er uden data" (ADR-0019 §3)
    claims_pending: int  # beregnet + afventer 2. signatur (ADR-0013 §4)
    contracts_active: int
    contracts_draft: int
    contracts_fortrolig: int
    obligations_open: int
    obligations_overdue: int
    risks_open: int
    risks_high: int
    suggestions_open: int
    agent_runs_failed_7d: int


class ActionItem(BaseModel):
    suggestion_id: uuid.UUID  # the suggestion's id, or the claim's when kind == "claim"
    kind: str = "suggestion"  # suggestion · claim
    contract_id: uuid.UUID
    contract_ref: str
    contract_name: str
    subject_kind: SuggestionSubject
    title: str
    confidence: Confidence
    agent_key: str
    created_at: datetime
    can_decide: bool


class DeadlineItem(BaseModel):
    kind: str  # opsigelse · udloeb · forpligtelse · risiko (ADR-0017 §1 subset)
    contract_id: uuid.UUID
    contract_ref: str
    contract_name: str
    label: str
    due_date: date
    days_left: int  # negative = overdue
    severity: str  # lav · mellem · hoej
    subject_id: uuid.UUID | None


class AgentStatus(BaseModel):
    agent_key: str
    label: str
    enabled: bool
    last_run_at: datetime | None
    last_status: AgentRunStatus | None
    last_findings: int | None
    runs_failed_7d: int


class AiSpend(BaseModel):
    month_dkk: Decimal
    month_usd: Decimal
    by_task: dict[str, Decimal]


class DashboardOut(BaseModel):
    counts: DashboardCounts
    actions: list[ActionItem]
    deadlines: list[DeadlineItem]
    agents: list[AgentStatus]
    portfolio_annual_value: Decimal | None  # None without okonomi (ADR-0003 §2)
    ai_spend: AiSpend | None  # None without okonomi (ADR-0014 §4)
    window_days: int
