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

export type Suggestion = {
  id: string;
  contract_id: string;
  agent_key: string;
  agent_run_id: string | null;
  kind: "create" | "update";
  subject_kind: string;
  subject_id: string | null;
  payload: IntakePayload;
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
