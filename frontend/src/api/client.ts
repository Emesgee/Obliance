// Thin fetch wrapper: same-origin /api only (gate G-01 — the browser never
// talks to a provider), Bearer token from the auth store, Danish error text
// from the backend's {detail: {error, code}} shape (ADR-0036).

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

type Detail = { error?: string; code?: string } | string | undefined;

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  // FormData sets its own multipart boundary; only JSON bodies get a Content-Type here.
  if (!headers.has("Content-Type") && init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);

  const res = await fetch(path, { ...init, headers });
  if (res.status === 204) return undefined as T;

  const body: unknown = await res.json().catch(() => undefined);
  if (!res.ok) {
    const detail = (body as { detail?: Detail } | undefined)?.detail;
    const error = typeof detail === "object" && detail?.error ? detail.error : `Fejl ${res.status}`;
    const code = typeof detail === "object" && detail?.code ? detail.code : "error";
    throw new ApiError(res.status, code, error);
  }
  return body as T;
}

/** Binary GET (document originals). Same Bearer, same same-origin rule. */
export async function apiBlob(path: string): Promise<Blob> {
  const headers = new Headers();
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  const res = await fetch(path, { headers });
  if (!res.ok) throw new ApiError(res.status, "error", `Fejl ${res.status}`);
  return res.blob();
}

// ---- types mirrored from backend/app/api/schemas.py (replaced by the generated
// client once `npm run api:generate` is wired into the build) -------------------

export type Me = {
  user_id: string;
  email: string;
  name: string;
  org_id: string;
  org_name: string;
  role: string;
  permissions: string[];
};

export type Contract = {
  id: string;
  reference: string;
  contract_number: string | null;
  name: string;
  agreement_form: string | null;
  category: string | null;
  department: string | null;
  phase: string;
  status: string;
  tier: string | null;
  confidentiality: "intern" | "fortrolig";
  owner_id: string | null;
  manager_id: string | null;
  start_date: string | null;
  end_date: string | null;
  description: string | null;
  notice_period_days: number | null;
  last_termination_date: string | null;
  options: { beskrivelse?: string; maaneder?: number | null }[];
  price_regulation: string | null;
  price_regulation_date: string | null;
  total_value: string | null;
  annual_value: string | null;
};

export type ContractList = { items: Contract[]; total: number };

// ---- AI (ADR-0004/0005/0010/0011) ----------------------------------------------

export type Citation = {
  kind: "document";
  document_id: string;
  document_version_id: string | null;
  page_pdf: number | null;
  page_printed: string | null;
  clause_ref: string | null;
  quote: string;
  verified: boolean;
  label: string;
};

export type SuggestedField = { value: string; citation: Citation | null; verified: boolean };

export type IntakePayload = {
  fields: Record<string, SuggestedField>;
  options: { description: string; months: number | null; citation: Citation | null; verified: boolean }[];
  before: Record<string, unknown>;
  model_confidence: string;
};

export type ObligationPayload = {
  title: string;
  description: string;
  party: "kunde" | "leverandoer" | "begge";
  frequency: string;
  deadline: string | null;
  criticality: "lav" | "mellem" | "hoej" | "kritisk";
  consequence: string | null;
  model_confidence: string;
};

export type RiskPayload = {
  title: string;
  description: string;
  category: string;
  probability: number;
  consequence: number;
  mitigation: string;
  model_confidence: string;
};

export type Risk = {
  id: string;
  contract_id: string;
  seq: number;
  ref: string;
  title: string;
  description: string | null;
  category: string;
  probability: number;
  consequence: number;
  score: number;
  level: "lav" | "mellem" | "hoej";
  status: "aaben" | "under_haandtering" | "lukket";
  responsible_id: string | null;
  deadline: string | null;
  mitigation: string | null;
  note: string | null;
  origin: "human" | "ai";
  suggestion_id: string | null;
  created_at: string;
  updated_at: string;
  closed_at: string | null;
  citations: CitationRow[];
  source_stale: boolean;
};

export type Suggestion<P = Record<string, unknown>> = {
  id: string;
  contract_id: string;
  agent_key: string;
  agent_run_id: string | null;
  kind: "create" | "update";
  subject_kind: string;
  subject_id: string | null;
  payload: P;
  confidence: "hoej" | "mellem" | "lav";
  rationale: string;
  citations: Citation[];
  amount_dkk: string | null;
  status: "foreslaaet" | "afventer_2_signatur" | "godkendt" | "afvist" | "foraeldet";
  decided_by: string | null;
  decided_at: string | null;
  decision_comment: string | null;
  created_at: string;
  updated_at: string;
};

export type AgentRun = {
  id: string;
  agent_key: string;
  contract_id: string | null;
  trigger: "schedule" | "event" | "manual";
  status: "koerer" | "ok" | "fejlet" | "sprunget_over";
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  suggestions_created: number;
  suggestions_updated: number;
  task: string | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_dkk: string | null;
  error: string | null;
};

export type BulkApproveOut = { approved: string[]; failed: { id: string; code: string; error: string }[] };

// ---- obligations + citations (ADR-0001, ADR-0005) --------------------------------

export type CitationRow = {
  id: string;
  kind: "document" | "record";
  document_id: string | null;
  document_version_id: string | null;
  page_pdf: number | null;
  page_printed: string | null;
  clause_ref: string | null;
  quote: string | null;
  verified: boolean;
  label: string;
  successor_status: "uaendret" | "flyttet" | "ikke_fundet" | null;
  successor_id: string | null;
};

export type Obligation = {
  id: string;
  contract_id: string;
  seq: number;
  ref: string;
  title: string;
  description: string | null;
  party: "kunde" | "leverandoer" | "begge";
  responsible_id: string | null;
  frequency: string;
  deadline: string | null;
  criticality: "lav" | "mellem" | "hoej" | "kritisk";
  status: "aaben" | "opfyldt" | "lukket";
  effective_status: "aaben" | "forsinket" | "opfyldt" | "lukket";
  consequence: string | null;
  note: string | null;
  origin: "human" | "ai";
  suggestion_id: string | null;
  created_at: string;
  updated_at: string;
  fulfilled_at: string | null;
  citations: CitationRow[];
  source_stale: boolean;
};

// ---- KPI / SLA, penalty terms, claims (ADR-0019, ADR-0013) ---------------------------

export type Measurement = {
  id: string;
  kpi_id: string;
  period_start: string;
  period_end: string;
  value: string;
  source_kind: "manual" | "import" | "document" | "integration";
  approved_at: string | null;
  note: string | null;
  supersedes_measurement_id: string | null;
  superseded_by_id: string | null;
  created_at: string;
};

export type Kpi = {
  id: string;
  contract_id: string;
  seq: number;
  ref: string;
  name: string;
  unit: "pct" | "antal" | "timer" | "dkk" | "score";
  target_operator: "gte" | "lte" | "eq" | "between";
  target_value: string;
  target_value_high: string | null;
  target_text: string;
  period: "maaned" | "kvartal" | "halvaar" | "aar";
  warn_band: string;
  penalty_term_id: string | null;
  measurement_obligation_id: string | null;
  active: boolean;
  origin: "human" | "ai";
  status: { color: "groen" | "gul" | "roed" | "graa"; reason: string; measured_period_start: string | null; value: string | null };
  measurements: Measurement[];
  citations: CitationRow[];
};

export type PenaltyTerm = {
  id: string;
  contract_id: string;
  seq: number;
  ref: string;
  name: string;
  term_type: string;
  trigger_description: string | null;
  applies_to: string | null;
  rate: string | null;
  tiers: { below: string; rate: string }[] | null;
  basis: string;
  basis_amount: string | null;
  time_unit: string;
  cap_rate: string | null;
  cap_amount: string | null;
  status: "aktiv" | "kraever_godkendelse";
  origin: "human" | "ai";
  citations: CitationRow[];
};

export type SlaBreach = {
  id: string;
  kpi_id: string;
  measurement_id: string;
  period_start: string;
  period_end: string;
  target_value: string;
  actual_value: string;
  penalty_term_id: string | null;
  claim_id: string | null;
  note: string | null;
};

export type Claim = {
  id: string;
  contract_id: string;
  seq: number;
  ref: string;
  claim_type: "service_credit" | "bod" | "prisafvigelse";
  period_start: string | null;
  period_end: string | null;
  penalty_term_id: string | null;
  breach_id: string | null;
  amount: string | null;
  amount_uncapped: string | null;
  cap_applied: boolean;
  basis_text: string | null;
  formula_version: string;
  status: "beregnet" | "afventer_2_signatur" | "godkendt" | "fremsat" | "modregnet" | "betalt" | "afvist_af_leverandoer" | "frafaldet";
  requires_second_signature: boolean;
  approved_by: string | null;
  second_approved_by: string | null;
  submitted_at: string | null;
  decision_comment: string | null;
  created_at: string;
  citations: CitationRow[];
};

export type KpiPayload = { name: string; unit: string; target_operator: string; target_value: string; period: string; target_text: string };
export type MeasurementPayload = { kpi_id: string; kpi_name: string; unit: string; period_start: string; period_end: string; value: string; target_text: string };
export type PenaltyTermPayload = { name: string; term_type: string; trigger_description: string; applies_to: string | null; rate: string | null; basis: string; basis_amount: string | null; time_unit: string; cap_rate: string | null };

// ---- dashboard (ADR-0001 §Overblik) ----------------------------------------------

export type Dashboard = {
  counts: {
    contracts_total: number;
    kpis_total: number;
    kpis_gray: number;
    claims_pending: number;
    contracts_active: number;
    contracts_draft: number;
    contracts_fortrolig: number;
    obligations_open: number;
    obligations_overdue: number;
    risks_open: number;
    risks_high: number;
    suggestions_open: number;
    agent_runs_failed_7d: number;
  };
  actions: {
    suggestion_id: string;
    kind: "suggestion" | "claim";
    contract_id: string;
    contract_ref: string;
    contract_name: string;
    subject_kind: string;
    title: string;
    confidence: "hoej" | "mellem" | "lav";
    agent_key: string;
    created_at: string;
    can_decide: boolean;
  }[];
  deadlines: {
    kind: string;
    contract_id: string;
    contract_ref: string;
    contract_name: string;
    label: string;
    due_date: string;
    days_left: number;
    severity: "lav" | "mellem" | "hoej";
    subject_id: string | null;
  }[];
  agents: {
    agent_key: string;
    label: string;
    enabled: boolean;
    last_run_at: string | null;
    last_status: "koerer" | "ok" | "fejlet" | "sprunget_over" | null;
    last_findings: number | null;
    runs_failed_7d: number;
  }[];
  portfolio_annual_value: string | null;
  ai_spend: { month_dkk: string; month_usd: string; by_task: Record<string, string> } | null;
  window_days: number;
};

export type AuditEntry = {
  id: string;
  occurred_at: string;
  actor_type: "human" | "agent" | "system";
  actor_label: string;
  actor_role: string | null;
  action: string;
  object_kind: string;
  object_id: string | null;
  object_label: string;
  contract_id: string | null;
  details: Record<string, unknown>;
};

// ---- documents (ADR-0005/0006) ----------------------------------------------

export type DocumentVersion = {
  id: string;
  document_id: string;
  version_no: number;
  status: "kladde" | "gaeldende" | "historisk";
  ingest_status: "afventer" | "koerer" | "ok" | "fejlet";
  ingest_error: string | null;
  original_filename: string;
  mime: string;
  size_bytes: number;
  sha256: string;
  page_count: number | null;
  ocr_applied: boolean;
  uploaded_by: string | null;
  uploaded_at: string;
  made_current_by: string | null;
  made_current_at: string | null;
  effective_note: string | null;
};

export type ContractDocument = {
  id: string;
  contract_id: string;
  doc_type: string;
  title: string;
  current_version_id: string | null;
  amends_document_id: string | null;
  created_at: string;
  versions: DocumentVersion[];
};

export type Page = { page_pdf: number; page_printed: string | null; text: string };

export type Clause = { clause_ref: string; heading: string; page_pdf: number; char_start: number; char_end: number };
