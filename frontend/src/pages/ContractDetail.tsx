import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  apiBlob,
  ApiError,
  type AgentRun,
  type Citation,
  type Clause,
  type Contract,
  type ContractDocument,
  type DocumentVersion,
  type IntakePayload,
  type Page,
  type Suggestion,
} from "../api/client";
import { useAuth } from "../auth";
import ClaimsSection from "./contract/Claims";
import KpisSection from "./contract/Kpis";
import ObligationsSection from "./contract/Obligations";
import RisksSection from "./contract/Risks";

// Contract detail: master data, documents with immutable versions (ADR-0006),
// and the HITL queue (ADR-0004) — the Contract Intake Agent's proposal is shown
// next to the current values with its citations (ADR-0005); a human approves or
// rejects with a reason. Nothing here writes the register except that verdict.

const DOC_TYPES: [string, string][] = [
  ["hovedkontrakt", "Hovedkontrakt"],
  ["bilag", "Bilag"],
  ["prisbilag", "Prisbilag"],
  ["databehandleraftale", "Databehandleraftale"],
  ["tillaeg", "Tillæg"],
  ["rapport", "Rapport"],
  ["korrespondance", "Korrespondance"],
  ["andet", "Andet"],
];

const ACCEPT = ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx";
const dkDate = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium", timeStyle: "short" });
const dkDay = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium" });
const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });

function fmtDay(s: string | null | undefined): string {
  return s ? dkDay.format(new Date(s)) : "—";
}

function Amount({ value }: { value: string | null }) {
  if (value === null) return <span className="text-muted">—</span>;
  return <span className="font-mono tabular-nums">{dkk.format(Number(value))}</span>;
}

function StatusPill({ status }: { status: DocumentVersion["status"] }) {
  const cls =
    status === "gaeldende" ? "bg-ok-bg text-ok" : status === "kladde" ? "bg-warn-bg text-warn" : "bg-none-bg text-none";
  const label = status === "gaeldende" ? "Gældende" : status === "kladde" ? "Kladde" : "Historisk";
  return <span className={`pill ${cls}`}>{label}</span>;
}

// ---- master data ----------------------------------------------------------------------------

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

function MasterData({ c }: { c: Contract }) {
  return (
    <section className="mb-6 grid grid-cols-2 gap-x-6 gap-y-3 rounded-cc border border-line bg-card p-4 sm:grid-cols-4">
      <Field label="Status">
        <span className={`pill ${c.status === "kladde" ? "bg-warn-bg text-warn" : "bg-ok-bg text-ok"}`}>{c.status}</span>
      </Field>
      <Field label="Aftaleform">{c.agreement_form ?? "—"}</Field>
      <Field label="Kontraktnummer"><span className="font-mono">{c.contract_number ?? "—"}</span></Field>
      <Field label="Kategori">{c.category ?? "—"}</Field>
      <Field label="Ikrafttræden">{fmtDay(c.start_date)}</Field>
      <Field label="Udløb">{fmtDay(c.end_date)}</Field>
      <Field label="Opsigelsesvarsel">{c.notice_period_days !== null ? `${Math.round(c.notice_period_days / 30)} mdr.` : "—"}</Field>
      <Field label="Sidste opsigelsesdato">{fmtDay(c.last_termination_date)}</Field>
      <Field label="Samlet værdi"><Amount value={c.total_value} /></Field>
      <Field label="Årlig værdi"><Amount value={c.annual_value} /></Field>
      <Field label="Prisregulering">{c.price_regulation ?? "—"}</Field>
      <Field label="Optioner">
        {c.options.length === 0 ? "—" : c.options.map((o, i) => <div key={i}>{o.beskrivelse}{o.maaneder ? ` (${o.maaneder} mdr.)` : ""}</div>)}
      </Field>
      {c.description && <div className="col-span-2 sm:col-span-4"><Field label="Beskrivelse">{c.description}</Field></div>}
    </section>
  );
}

// ---- HITL: suggestions (ADR-0004) ---------------------------------------------------------------

const FIELD_LABELS: Record<string, string> = {
  name: "Navn",
  contract_number: "Kontraktnummer",
  agreement_form: "Aftaleform",
  category: "Kategori",
  description: "Beskrivelse",
  start_date: "Ikrafttræden",
  end_date: "Udløb",
  notice_period_months: "Opsigelsesvarsel (mdr.)",
  last_termination_date: "Sidste opsigelsesdato",
  price_regulation: "Prisregulering",
  total_value_dkk: "Samlet værdi (DKK)",
  annual_value_dkk: "Årlig værdi (DKK)",
};

function currentValue(c: Contract, field: string): string | null {
  switch (field) {
    case "notice_period_months":
      return c.notice_period_days !== null ? String(Math.round(c.notice_period_days / 30)) : null;
    case "total_value_dkk":
      return c.total_value;
    case "annual_value_dkk":
      return c.annual_value;
    default: {
      const v = (c as unknown as Record<string, unknown>)[field];
      return v === null || v === undefined ? null : String(v);
    }
  }
}

function ConfidencePill({ value }: { value: Suggestion["confidence"] }) {
  const cls = value === "hoej" ? "bg-ok-bg text-ok" : value === "mellem" ? "bg-warn-bg text-warn" : "bg-crit-bg text-crit";
  const label = value === "hoej" ? "Høj" : value === "mellem" ? "Mellem" : "Lav";
  return <span className={`pill ${cls}`}>Sikkerhed: {label}</span>;
}

function CitationChip({ c }: { c: Citation | null }) {
  if (!c) return <span className="text-xs text-muted">ingen kilde</span>;
  return (
    <span title={c.quote} className={`inline-block rounded-cc-sm border px-2 py-0.5 text-xs ${c.verified ? "border-line bg-bg" : "border-warn bg-warn-bg text-warn"}`}>
      {c.label}{!c.verified && " · citat ikke fundet"}
    </span>
  );
}

function SuggestionCard({ s, contract }: { s: Suggestion<IntakePayload>; contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [comment, setComment] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["suggestions", contract.id] });
    void qc.invalidateQueries({ queryKey: ["contract", contract.id] });
  };
  const approve = useMutation({
    mutationFn: () => api<Suggestion>(`/api/suggestions/${s.id}/approve`, { method: "POST", body: JSON.stringify({ comment: comment || null }) }),
    onSuccess: refresh,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke godkende."),
  });
  const reject = useMutation({
    mutationFn: () => api<Suggestion>(`/api/suggestions/${s.id}/reject`, { method: "POST", body: JSON.stringify({ comment }) }),
    onSuccess: refresh,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke afvise."),
  });

  const open = s.status === "foreslaaet" || s.status === "afventer_2_signatur";
  const fields = Object.entries(s.payload.fields ?? {});
  const decided = !open && (
    <p className="text-sm text-muted">
      {s.status === "godkendt" ? "Godkendt" : s.status === "afvist" ? "Afvist" : "Forældet"}
      {s.decided_at ? ` · ${dkDate.format(new Date(s.decided_at))}` : ""}
      {s.decision_comment ? ` · ${s.decision_comment}` : ""}
    </p>
  );

  return (
    <article className={`mb-4 rounded-cc border p-4 ${open ? "border-accent bg-card" : "border-line bg-card opacity-80"}`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="pill bg-blue-bg text-accent">AI-forslag</span>
        <span className="font-semibold">Contract Intake Agent</span>
        <ConfidencePill value={s.confidence} />
        <span className="ml-auto text-xs text-muted">{dkDate.format(new Date(s.created_at))}</span>
      </div>
      {s.rationale && <p className="mb-3 text-sm text-slate">{s.rationale}</p>}
      {decided}
      {open && (
        <>
          <table className="mb-3 w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="py-2 pr-3">Felt</th><th className="py-2 pr-3">Nuværende</th><th className="py-2 pr-3">Foreslået</th><th className="py-2">Kilde</th>
              </tr>
            </thead>
            <tbody>
              {fields.map(([name, f]) => {
                const cur = currentValue(contract, name);
                const kept = cur !== null && cur !== "";
                return (
                  <tr key={name} className="border-b border-line last:border-0 align-top">
                    <td className="py-2 pr-3 font-medium">{FIELD_LABELS[name] ?? name}</td>
                    <td className="py-2 pr-3 text-muted">{cur ?? "—"}{kept && <span className="ml-1 text-xs">(beholdes)</span>}</td>
                    <td className={`py-2 pr-3 ${kept ? "text-muted line-through" : ""}`}>{f.value}</td>
                    <td className="py-2"><CitationChip c={f.citation} /></td>
                  </tr>
                );
              })}
              {(s.payload.options ?? []).map((o, i) => (
                <tr key={`opt-${i}`} className="border-b border-line last:border-0 align-top">
                  <td className="py-2 pr-3 font-medium">Option</td>
                  <td className="py-2 pr-3 text-muted">{contract.options.length ? "(beholdes)" : "—"}</td>
                  <td className="py-2 pr-3">{o.description}{o.months ? ` (${o.months} mdr.)` : ""}</td>
                  <td className="py-2"><CitationChip c={o.citation} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          {can("hitl") && can("kontrakt_red") ? (
            <div className="flex flex-wrap items-start gap-2">
              <textarea
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                placeholder={rejecting ? "Begrundelse (påkrævet ved afvisning)" : "Kommentar (valgfri)"}
                className="min-h-[2.5rem] flex-1 rounded-cc-sm border border-line px-3 py-2 text-sm"
              />
              {!rejecting ? (
                <>
                  <button onClick={() => approve.mutate()} disabled={approve.isPending} className="rounded-cc-sm bg-accent px-4 py-2 text-sm font-semibold text-card disabled:opacity-60">
                    Godkend
                  </button>
                  <button onClick={() => setRejecting(true)} className="rounded-cc-sm border border-line px-4 py-2 text-sm">Afvis …</button>
                </>
              ) : (
                <>
                  <button onClick={() => reject.mutate()} disabled={reject.isPending || comment.trim().length < 3} className="rounded-cc-sm bg-crit px-4 py-2 text-sm font-semibold text-card disabled:opacity-60">
                    Afvis med begrundelse
                  </button>
                  <button onClick={() => setRejecting(false)} className="rounded-cc-sm border border-line px-4 py-2 text-sm">Fortryd</button>
                </>
              )}
            </div>
          ) : (
            <p className="text-xs text-muted">Godkendelse kræver tilladelserne hitl og kontrakt_red.</p>
          )}
          {error && <p role="alert" className="mt-2 rounded-cc-sm bg-crit-bg px-3 py-2 text-sm text-crit">{error}</p>}
        </>
      )}
    </article>
  );
}

function runLabel(r: AgentRun): string {
  const when = dkDate.format(new Date(r.started_at));
  switch (r.status) {
    case "koerer": return `Contract Intake Agent læser dokumenterne … (startet ${when})`;
    case "ok": return `Sidst kørt ${when} · ${r.suggestions_created + r.suggestions_updated} forslag`;
    case "fejlet": return `Fejlede ${when}: ${r.error ?? "ukendt fejl"}`;
    default: return `Sprunget over ${when}${r.error ? `: ${r.error}` : ""}`;
  }
}

function AiSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: ["agent-runs", contract.id],
    queryFn: () => api<AgentRun[]>(`/api/contracts/${contract.id}/agent-runs`),
    refetchInterval: (q) => (q.state.data?.some((r) => r.status === "koerer") ? 2000 : false),
  });
  const running = runs.data?.some((r) => r.status === "koerer") ?? false;
  const suggestions = useQuery({
    queryKey: ["suggestions", contract.id],
    queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`),
    refetchInterval: running ? 2000 : false,
  });
  const intake = (suggestions.data ?? []).filter(
    (s): s is Suggestion<IntakePayload> => s.subject_kind === "contract_intake",
  );
  const run = useMutation({
    mutationFn: () => api<{ status: string }>(`/api/contracts/${contract.id}/agents/contract_intake/run`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["agent-runs", contract.id] });
      void qc.invalidateQueries({ queryKey: ["suggestions", contract.id] });
    },
  });
  const latest = runs.data?.[0];

  return (
    <section className="mb-6">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Stamdata fra AI</h2>
        {can("agenter") && (
          <button onClick={() => run.mutate()} disabled={run.isPending || running} className="rounded-cc-sm border border-line px-3 py-1 text-sm disabled:opacity-60">
            Kør Contract Intake Agent
          </button>
        )}
      </div>
      {latest && (
        <p className={`mb-3 text-sm ${latest.status === "fejlet" ? "text-crit" : latest.status === "koerer" ? "text-accent" : "text-muted"}`}>
          {runLabel(latest)}
        </p>
      )}
      {suggestions.data && intake.length === 0 && !latest && (
        <p className="rounded-cc border border-line bg-card p-4 text-sm text-slate">
          Ingen forslag endnu. Upload en hovedkontrakt, så læser Contract Intake Agent den.
        </p>
      )}
      {intake.map((s) => <SuggestionCard key={s.id} s={s} contract={contract} />)}
    </section>
  );
}

// ---- documents (ADR-0006) ---------------------------------------------------------------------

function UploadForm({ contractId, documentId, onDone }: { contractId: string; documentId?: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("hovedkontrakt");
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async (fd: FormData): Promise<void> => {
      if (documentId) {
        await api<DocumentVersion>(`/api/documents/${documentId}/versions`, { method: "POST", body: fd });
      } else {
        await api<ContractDocument>(`/api/contracts/${contractId}/documents`, { method: "POST", body: fd });
      }
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents", contractId] });
      void qc.invalidateQueries({ queryKey: ["agent-runs", contractId] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Upload mislykkedes."),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    if (!file) return;
    setError(null);
    const fd = new FormData();
    fd.append("file", file);
    if (!documentId) {
      fd.append("doc_type", docType);
      fd.append("title", title);
    }
    upload.mutate(fd);
  }

  return (
    <form onSubmit={submit} className="mb-4 grid grid-cols-1 gap-3 rounded-cc border border-line bg-card p-4 sm:grid-cols-4">
      <div className={documentId ? "sm:col-span-3" : "sm:col-span-1"}>
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="file">Fil</label>
        <input id="file" type="file" required accept={ACCEPT} onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="w-full text-sm" />
      </div>
      {!documentId && (
        <>
          <div>
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="doctype">Type</label>
            <select id="doctype" value={docType} onChange={(e) => setDocType(e.target.value)} className="w-full rounded-cc-sm border border-line px-3 py-2 text-sm">
              {DOC_TYPES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="title">Titel (valgfri)</label>
            <input id="title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Filnavnet bruges, hvis tom" className="w-full rounded-cc-sm border border-line px-3 py-2 text-sm" />
          </div>
        </>
      )}
      {error && <p role="alert" className="sm:col-span-4 rounded-cc-sm bg-crit-bg px-3 py-2 text-sm text-crit">{error}</p>}
      <div className="flex gap-2 sm:col-span-4">
        <button type="submit" disabled={upload.isPending || !file} className="rounded-cc-sm bg-accent px-4 py-2 text-sm font-semibold text-card disabled:opacity-60">
          {upload.isPending ? "Uploader og indlæser …" : documentId ? "Upload ny version" : "Upload dokument"}
        </button>
        <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-4 py-2 text-sm">Annullér</button>
      </div>
    </form>
  );
}

function PageViewer({ version }: { version: DocumentVersion }) {
  const pages = useQuery({ queryKey: ["pages", version.id], queryFn: () => api<Page[]>(`/api/documents/versions/${version.id}/pages`) });
  const clauses = useQuery({ queryKey: ["clauses", version.id], queryFn: () => api<Clause[]>(`/api/documents/versions/${version.id}/clauses`) });
  const [selected, setSelected] = useState(1);

  async function openFile() {
    const blob = await apiBlob(`/api/documents/versions/${version.id}/file`);
    window.open(URL.createObjectURL(blob), "_blank", "noopener");
  }

  const page = pages.data?.find((p) => p.page_pdf === selected);
  return (
    <div className="mt-3 grid grid-cols-1 gap-3 rounded-cc border border-line bg-card p-4 md:grid-cols-[220px_1fr]">
      <aside className="text-sm">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">Version {version.version_no}</span>
          <button onClick={() => void openFile()} className="text-xs text-accent underline">Åbn original</button>
        </div>
        {version.ingest_error && <p className="mb-2 rounded-cc-sm bg-warn-bg px-2 py-1 text-xs text-warn">{version.ingest_error}</p>}
        <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Klausuler</p>
        {clauses.data && clauses.data.length === 0 && <p className="text-xs text-muted">Ingen fundet.</p>}
        <ul className="max-h-72 overflow-y-auto">
          {clauses.data?.map((c) => (
            <li key={`${c.page_pdf}-${c.char_start}`}>
              <button onClick={() => setSelected(c.page_pdf)} className="w-full truncate py-0.5 text-left hover:text-accent">
                <span className="font-mono text-xs">{c.clause_ref}</span> {c.heading}
              </button>
            </li>
          ))}
        </ul>
      </aside>
      <div>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Side</span>
          {pages.data?.map((p) => (
            <button key={p.page_pdf} onClick={() => setSelected(p.page_pdf)}
              className={`rounded-cc-sm px-2 py-0.5 font-mono text-xs ${p.page_pdf === selected ? "bg-accent text-card" : "border border-line"}`}>
              {p.page_pdf}{p.page_printed && p.page_printed !== String(p.page_pdf) ? ` (${p.page_printed})` : ""}
            </button>
          ))}
        </div>
        {pages.isLoading && <p className="text-slate">Henter sider …</p>}
        {page && (
          <pre className="max-h-[32rem] overflow-auto whitespace-pre-wrap rounded-cc-sm bg-bg p-3 font-sans text-sm leading-relaxed">
            {page.text || "(ingen tekst på siden)"}
          </pre>
        )}
      </div>
    </div>
  );
}

function DocumentCard({ doc, contractId }: { doc: ContractDocument; contractId: string }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [addingVersion, setAddingVersion] = useState(false);
  const [viewing, setViewing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const makeCurrent = useMutation({
    mutationFn: (versionId: string) => api<DocumentVersion>(`/api/documents/versions/${versionId}/make-current`, { method: "POST" }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["documents", contractId] });
      void qc.invalidateQueries({ queryKey: ["agent-runs", contractId] });
      void qc.invalidateQueries({ queryKey: ["suggestions", contractId] });
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke gøre versionen gældende."),
  });

  const viewed = doc.versions.find((v) => v.id === viewing);
  const typeLabel = DOC_TYPES.find(([v]) => v === doc.doc_type)?.[1] ?? doc.doc_type;
  return (
    <article className="mb-4 rounded-cc border border-line bg-card p-4">
      <div className="mb-2 flex items-center justify-between">
        <div>
          <span className="pill mr-2 bg-blue-bg text-accent">{typeLabel}</span>
          <span className="font-semibold">{doc.title}</span>
        </div>
        {can("kontrakt_red") && !addingVersion && (
          <button onClick={() => setAddingVersion(true)} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Ny version</button>
        )}
      </div>
      {addingVersion && <UploadForm contractId={contractId} documentId={doc.id} onDone={() => setAddingVersion(false)} />}
      {error && <p role="alert" className="mb-2 rounded-cc-sm bg-crit-bg px-3 py-2 text-sm text-crit">{error}</p>}
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
            <th className="py-2 pr-3">Ver.</th>
            <th className="py-2 pr-3">Status</th>
            <th className="py-2 pr-3">Fil</th>
            <th className="py-2 pr-3">Sider</th>
            <th className="py-2 pr-3">Uploadet</th>
            <th className="py-2"></th>
          </tr>
        </thead>
        <tbody>
          {doc.versions.map((v) => (
            <tr key={v.id} className="border-b border-line last:border-0">
              <td className="py-2 pr-3 font-mono">{v.version_no}</td>
              <td className="py-2 pr-3">
                <StatusPill status={v.status} />
                {v.ingest_status !== "ok" && <span className="ml-2 text-xs text-warn">{v.ingest_status}</span>}
              </td>
              <td className="max-w-[16rem] truncate py-2 pr-3">{v.original_filename}</td>
              <td className="py-2 pr-3 font-mono">{v.page_count ?? "—"}</td>
              <td className="py-2 pr-3 text-muted">{dkDate.format(new Date(v.uploaded_at))}</td>
              <td className="whitespace-nowrap py-2 text-right">
                <button onClick={() => setViewing(viewing === v.id ? null : v.id)} className="mr-2 text-xs text-accent underline">{viewing === v.id ? "Skjul" : "Vis"}</button>
                {can("kontrakt_red") && v.status !== "gaeldende" && v.ingest_status === "ok" && (
                  <button onClick={() => makeCurrent.mutate(v.id)} disabled={makeCurrent.isPending} className="rounded-cc-sm bg-accent px-2 py-1 text-xs font-semibold text-card disabled:opacity-60">
                    Gør gældende
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {viewed && <PageViewer key={viewed.id} version={viewed} />}
    </article>
  );
}

// ---- page -----------------------------------------------------------------------------------

export default function ContractDetail() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const [uploading, setUploading] = useState(false);
  const contract = useQuery({ queryKey: ["contract", id], queryFn: () => api<Contract>(`/api/contracts/${id}`) });
  const docs = useQuery({ queryKey: ["documents", id], queryFn: () => api<ContractDocument[]>(`/api/contracts/${id}/documents`) });

  if (contract.isLoading) return <p className="text-slate">Henter …</p>;
  if (contract.isError || !contract.data) {
    return <p role="alert" className="text-crit">Kontrakten findes ikke, eller du har ikke adgang.</p>;
  }
  const c = contract.data;

  return (
    <section>
      <p className="mb-2 text-sm"><Link to="/contracts" className="text-accent underline">← Kontrakter</Link></p>
      <div className="mb-4 flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl font-bold tracking-tight">{c.name}</h1>
        <span className="font-mono text-sm text-muted">{c.reference}</span>
        <span className={`pill ${c.confidentiality === "fortrolig" ? "bg-warn-bg text-warn" : "bg-none-bg text-none"}`}>{c.confidentiality}</span>
      </div>

      <MasterData c={c} />
      <AiSection contract={c} />
      <ObligationsSection contract={c} />
      <KpisSection contract={c} />
      <ClaimsSection contract={c} />
      <RisksSection contract={c} />

      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold">Dokumenter</h2>
        {can("kontrakt_red") && !uploading && (
          <button onClick={() => setUploading(true)} className="rounded-cc-sm bg-accent px-4 py-2 text-sm font-semibold text-card">Upload dokument</button>
        )}
      </div>
      {uploading && <UploadForm contractId={id} onDone={() => setUploading(false)} />}

      {docs.isLoading && <p className="text-slate">Henter dokumenter …</p>}
      {docs.data && docs.data.length === 0 && (
        <p className="rounded-cc border border-line bg-card p-6 text-slate">
          Ingen dokumenter endnu{can("kontrakt_red") ? " — upload hovedkontrakten." : "."}
        </p>
      )}
      {docs.data?.map((d) => <DocumentCard key={d.id} doc={d} contractId={id} />)}
    </section>
  );
}
