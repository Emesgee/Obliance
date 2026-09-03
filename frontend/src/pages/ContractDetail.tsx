import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  api,
  apiBlob,
  ApiError,
  type Clause,
  type Contract,
  type ContractDocument,
  type DocumentVersion,
  type Page,
} from "../api/client";
import { useAuth } from "../auth";

// Contract detail (ADR-0006 in the UI): documents with their immutable versions,
// upload of a new document or a new version, and the one human act — "Gør
// gældende". Page text is shown as extracted (ADR-0005: the page is derived,
// the citation is the truth) so a reader can check what the system will cite.

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

function StatusPill({ status }: { status: DocumentVersion["status"] }) {
  const cls =
    status === "gaeldende" ? "bg-ok-bg text-ok" : status === "kladde" ? "bg-warn-bg text-warn" : "bg-none-bg text-none";
  const label = status === "gaeldende" ? "Gældende" : status === "kladde" ? "Kladde" : "Historisk";
  return <span className={`pill ${cls}`}>{label}</span>;
}

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
  const [viewing, setViewing] = useState<string | null>(doc.current_version_id);
  const [error, setError] = useState<string | null>(null);

  const makeCurrent = useMutation({
    mutationFn: (versionId: string) => api<DocumentVersion>(`/api/documents/versions/${versionId}/make-current`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["documents", contractId] }),
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
                <button onClick={() => setViewing(v.id)} className="mr-2 text-xs text-accent underline">{viewing === v.id ? "Vises" : "Vis"}</button>
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
      <p className="mb-2 text-sm"><Link to="/" className="text-accent underline">← Kontrakter</Link></p>
      <div className="mb-6 flex items-baseline gap-3">
        <h1 className="text-2xl font-bold tracking-tight">{c.name}</h1>
        <span className="font-mono text-sm text-muted">{c.reference}</span>
        <span className={`pill ${c.confidentiality === "fortrolig" ? "bg-warn-bg text-warn" : "bg-none-bg text-none"}`}>{c.confidentiality}</span>
      </div>

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
