import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type Contract, type ContractList, type ImportErrorRow, type ImportReport, type Invoice, type Supplier } from "../api/client";
import { useAuth } from "../auth";

// Økonomi (ADR-0018): the inbound feed. Import a file, watch the report, put
// unmatched invoices on a contract, see what could not be parsed. Nothing here
// talks to the ERP — the human acts there, after deciding here.

const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });
const dkDay = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium" });
export const INVOICE_STATUS: Record<Invoice["status"], [string, string]> = {
  modtaget: ["Modtaget", "bg-none-bg text-none"],
  matchet: ["Matchet", "bg-blue-bg text-accent"],
  kontrolleret: ["Kontrolleret", "bg-warn-bg text-warn"],
  godkendt: ["Godkendt", "bg-ok-bg text-ok"],
  afvist: ["Afvist", "bg-crit-bg text-crit"],
  erstattet: ["Erstattet", "bg-none-bg text-none"],
};

export function ControlPill({ inv }: { inv: Invoice }) {
  if (inv.status === "godkendt" || inv.status === "afvist" || inv.status === "erstattet") {
    const [l, c] = INVOICE_STATUS[inv.status];
    return <span className={`pill ${c}`}>{l}</span>;
  }
  if (inv.control_result === "bestaaet") return <span className="pill bg-ok-bg text-ok" title={inv.control_note ?? ""}>Kontrol bestået — klar til godkendelse</span>;
  if (inv.control_result === "afvigelse") return <span className="pill bg-crit-bg text-crit" title={inv.control_note ?? ""}>Afvigelse fundet</span>;
  if (inv.control_result === "ingen_prisgrundlag") return <span className="pill bg-warn-bg text-warn" title={inv.control_note ?? ""}>Intet prisgrundlag</span>;
  const [l, c] = INVOICE_STATUS[inv.status];
  return <span className={`pill ${c}`}>{l}</span>;
}

function SupplierForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [cvr, setCvr] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<Supplier>("/api/suppliers", { method: "POST", body: JSON.stringify({ cvr, name }) }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["suppliers"] }); onDone(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke oprette."),
  });
  return (
    <form onSubmit={(e: FormEvent) => { e.preventDefault(); create.mutate(); }} className="mb-3 flex flex-wrap items-end gap-2 rounded-cc border border-line bg-card p-3">
      <label className="text-xs text-muted">CVR<br /><input required pattern="\d{8}" value={cvr} onChange={(e) => setCvr(e.target.value)} className="w-32 rounded-cc-sm border border-line px-2 py-1.5 font-mono text-sm" /></label>
      <label className="min-w-[16rem] flex-1 text-xs text-muted">Navn<br /><input required value={name} onChange={(e) => setName(e.target.value)} className="w-full rounded-cc-sm border border-line px-2 py-1.5 text-sm" /></label>
      <button type="submit" disabled={create.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Opret leverandør</button>
      <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Annullér</button>
      {error && <p role="alert" className="w-full text-sm text-crit">{error}</p>}
    </form>
  );
}

export default function Economy() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [addingSupplier, setAddingSupplier] = useState(false);
  const [choice, setChoice] = useState<Record<string, string>>({});
  const money = can("okonomi");

  const unmatched = useQuery({ queryKey: ["invoices", "unmatched"], queryFn: () => api<Invoice[]>("/api/invoices?queue=unmatched"), enabled: money });
  const pending = useQuery({ queryKey: ["invoices", "pending"], queryFn: () => api<Invoice[]>("/api/invoices?queue=pending"), enabled: money });
  const errors = useQuery({ queryKey: ["import-errors"], queryFn: () => api<ImportErrorRow[]>("/api/import-errors"), enabled: money });
  const suppliers = useQuery({ queryKey: ["suppliers"], queryFn: () => api<Supplier[]>("/api/suppliers") });
  const contracts = useQuery({ queryKey: ["contracts"], queryFn: () => api<ContractList>("/api/contracts") });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["invoices"] });
    void qc.invalidateQueries({ queryKey: ["import-errors"] });
    void qc.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const upload = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.append("file", f); return api<ImportReport>("/api/invoices/import", { method: "POST", body: fd }); },
    onSuccess: (r) => { setReport(r); setFile(null); refresh(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Import mislykkedes."),
  });
  const match = useMutation({
    mutationFn: ({ id, contract_id }: { id: string; contract_id: string }) => api<Invoice>(`/api/invoices/${id}/match`, { method: "POST", body: JSON.stringify({ contract_id }) }),
    onSuccess: refresh,
  });

  if (!money) return <p className="text-slate">Økonomi kræver tilladelsen okonomi.</p>;
  const allContracts: Contract[] = contracts.data?.items ?? [];

  return (
    <section>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Økonomi</h1>
        <span className="text-sm text-muted">Indgående fakturafeed · Obliance skriver aldrig til økonomisystemet</span>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-cc border border-line bg-card p-4">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wider text-muted">Importér fakturaer (CSV eller Excel)</h2>
          <p className="mb-2 text-xs text-muted">Kolonner: fakturanr · fakturadato · forfaldsdato · leverandoer_cvr · kontraktreference · linje · beskrivelse · antal · enhed · enhedspris · linjetotal · periode_fra · periode_til · produktref. Én række pr. fakturalinje, semikolon og komma-decimal.</p>
          <form onSubmit={(e: FormEvent) => { e.preventDefault(); setError(null); if (file) upload.mutate(file); }} className="flex flex-wrap items-center gap-2">
            <input type="file" accept=".csv,.xlsx" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="text-sm" />
            <button type="submit" disabled={!file || upload.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">{upload.isPending ? "Importerer …" : "Importér"}</button>
          </form>
          {error && <p role="alert" className="mt-2 text-sm text-crit">{error}</p>}
          {report && (
            <div className="mt-3 text-sm">
              <p><span className="font-semibold">Rapport:</span> {report.received} rækker · {report.new} nye · {report.updated} kendte · {report.superseded} erstattede · {report.rejected} afviste · {report.matched} matchet · {report.queued} i matchkø</p>
              {report.errors.length > 0 && <ul className="mt-1 text-xs text-crit">{report.errors.slice(0, 10).map((e, i) => <li key={i}>Række {e.row_no}: {e.reason}</li>)}</ul>}
            </div>
          )}
        </section>

        <section className="rounded-cc border border-line bg-card p-4">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted">Leverandører</h2>
            {!addingSupplier && <button onClick={() => setAddingSupplier(true)} className="rounded-cc-sm border border-line px-3 py-1 text-xs">Ny leverandør</button>}
          </div>
          {addingSupplier && <SupplierForm onDone={() => setAddingSupplier(false)} />}
          {(suppliers.data ?? []).length === 0 ? <p className="text-sm text-slate">Ingen leverandører. En faktura med ukendt CVR afvises — opret leverandøren først.</p> : (
            <ul className="text-sm">{suppliers.data?.map((s) => <li key={s.id} className="flex justify-between border-t border-line py-1 first:border-0"><span>{s.name}</span><span className="font-mono text-xs text-muted">{s.cvr}</span></li>)}</ul>
          )}
        </section>
      </div>

      <section className="mb-6 rounded-cc border border-line bg-card">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">Matchkø · fakturaer uden kontrakt</h2>
        {(unmatched.data ?? []).length === 0 ? <p className="px-4 py-4 text-sm text-slate">Tom.</p> : (
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted"><th className="px-4 py-2">Faktura</th><th className="px-4 py-2">Leverandør</th><th className="px-4 py-2">Dato</th><th className="px-4 py-2 text-right">Beløb</th><th className="px-4 py-2">Kontrakt</th></tr></thead>
            <tbody>
              {unmatched.data?.map((inv) => {
                const options = inv.candidates.length ? inv.candidates.map((c) => ({ id: c.contract_id, label: `${c.reference} ${c.name}` })) : allContracts.map((c) => ({ id: c.id, label: `${c.reference} ${c.name}` }));
                return (
                  <tr key={inv.id} className="border-b border-line last:border-0">
                    <td className="px-4 py-2 font-mono">{inv.invoice_number}</td>
                    <td className="px-4 py-2">{inv.supplier_name}</td>
                    <td className="px-4 py-2 text-muted">{dkDay.format(new Date(inv.invoice_date))}</td>
                    <td className="px-4 py-2 text-right font-mono tabular-nums">{dkk.format(Number(inv.total_amount))}</td>
                    <td className="px-4 py-2">
                      <select value={choice[inv.id] ?? ""} onChange={(e) => setChoice({ ...choice, [inv.id]: e.target.value })} className="mr-2 rounded-cc-sm border border-line px-2 py-1 text-sm">
                        <option value="">{inv.candidates.length ? `Vælg blandt ${inv.candidates.length} kandidater` : "Vælg kontrakt"}</option>
                        {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
                      </select>
                      <button disabled={!choice[inv.id] || match.isPending} onClick={() => match.mutate({ id: inv.id, contract_id: choice[inv.id] })} className="rounded-cc-sm bg-accent px-3 py-1 text-xs font-semibold text-card disabled:opacity-60">Match</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      <section className="mb-6 rounded-cc border border-line bg-card">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">Afventer beslutning</h2>
        {(pending.data ?? []).length === 0 ? <p className="px-4 py-4 text-sm text-slate">Ingen fakturaer afventer.</p> : (
          <ul>
            {pending.data?.map((inv) => (
              <li key={inv.id} className="border-b border-line px-4 py-2 last:border-0">
                <Link to={`/contracts/${inv.contract_id}`} className="flex items-center gap-3 text-sm hover:text-accent">
                  <span className="font-mono">{inv.invoice_number}</span>
                  <span className="min-w-0 flex-1 truncate">{inv.supplier_name}</span>
                  <span className="font-mono text-xs text-muted">{inv.contract_ref}</span>
                  <span className="font-mono tabular-nums">{dkk.format(Number(inv.total_amount))}</span>
                  <ControlPill inv={inv} />
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-cc border border-line bg-card">
        <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">Fejlkø · rækker der ikke kunne importeres</h2>
        {(errors.data ?? []).length === 0 ? <p className="px-4 py-4 text-sm text-slate">Ingen fejl.</p> : (
          <ul className="text-sm">{errors.data?.map((e) => <li key={e.id} className="border-b border-line px-4 py-2 last:border-0"><span className="font-mono text-xs text-muted">{e.file_name} · række {e.row_no}</span> — {e.reason}</li>)}</ul>
        )}
      </section>
    </section>
  );
}
