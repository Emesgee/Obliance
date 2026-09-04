import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import {
  api,
  ApiError,
  type BulkApproveOut,
  type CitationRow,
  type Citation,
  type Contract,
  type Obligation,
  type ObligationPayload,
  type Suggestion,
} from "../../api/client";
import { useAuth } from "../../auth";

// Forpligtelser: the register (obligations) unioned with the open AI proposals
// (ai_suggestions, subject obligation) — ADR-0004 §Konsekvenser. The register
// stays clean; the AI badge lives on the proposal row until a human decides.

const PARTY: Record<string, string> = { kunde: "Kunde", leverandoer: "Leverandør", begge: "Begge" };
const FREQ: Record<string, string> = {
  engang: "Engang", loebende: "Løbende", maanedlig: "Månedlig", kvartalsvis: "Kvartalsvis",
  halvaarlig: "Halvårlig", aarlig: "Årlig", ved_haendelse: "Ved hændelse",
};
const CRIT: Record<string, string> = { lav: "Lav", mellem: "Mellem", hoej: "Høj", kritisk: "Kritisk" };
const dkDay = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium" });

function CritPill({ v }: { v: string }) {
  const cls = v === "kritisk" ? "bg-crit-bg text-crit" : v === "hoej" ? "bg-warn-bg text-warn" : v === "mellem" ? "bg-blue-bg text-accent" : "bg-none-bg text-none";
  return <span className={`pill ${cls}`}>{CRIT[v] ?? v}</span>;
}

function StatusPill({ v }: { v: Obligation["effective_status"] }) {
  const cls = v === "forsinket" ? "bg-crit-bg text-crit" : v === "opfyldt" ? "bg-ok-bg text-ok" : v === "lukket" ? "bg-none-bg text-none" : "bg-blue-bg text-accent";
  const label = v === "forsinket" ? "Forsinket" : v === "opfyldt" ? "Opfyldt" : v === "lukket" ? "Lukket" : "Åben";
  return <span className={`pill ${cls}`}>{label}</span>;
}

function ConfPill({ v }: { v: Suggestion["confidence"] }) {
  const cls = v === "hoej" ? "bg-ok-bg text-ok" : v === "mellem" ? "bg-warn-bg text-warn" : "bg-crit-bg text-crit";
  return <span className={`pill ${cls}`}>Sikkerhed: {v === "hoej" ? "Høj" : v === "mellem" ? "Mellem" : "Lav"}</span>;
}

function Chip({ c }: { c: Citation | CitationRow }) {
  const stale = "successor_status" in c && c.successor_status === "ikke_fundet";
  const warn = !c.verified || stale;
  return (
    <span title={c.quote ?? ""} className={`mr-1 inline-block rounded-cc-sm border px-2 py-0.5 text-xs ${warn ? "border-warn bg-warn-bg text-warn" : "border-line bg-bg"}`}>
      {c.label}{!c.verified && " · citat ikke fundet"}{stale && " · kilde forældet"}
    </span>
  );
}

function Verdict({ s, onDone }: { s: Suggestion<ObligationPayload>; onDone: () => void }) {
  const [comment, setComment] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const approve = useMutation({
    mutationFn: () => api<Suggestion>(`/api/suggestions/${s.id}/approve`, { method: "POST", body: JSON.stringify({ comment: comment || null }) }),
    onSuccess: onDone,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke godkende."),
  });
  const reject = useMutation({
    mutationFn: () => api<Suggestion>(`/api/suggestions/${s.id}/reject`, { method: "POST", body: JSON.stringify({ comment }) }),
    onSuccess: onDone,
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke afvise."),
  });
  return (
    <div className="mt-2 flex flex-wrap items-start gap-2">
      <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder={rejecting ? "Begrundelse (påkrævet)" : "Kommentar (valgfri)"}
        className="min-w-[16rem] flex-1 rounded-cc-sm border border-line px-3 py-1.5 text-sm" />
      {!rejecting ? (
        <>
          <button onClick={() => approve.mutate()} disabled={approve.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Godkend</button>
          <button onClick={() => setRejecting(true)} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Afvis …</button>
        </>
      ) : (
        <>
          <button onClick={() => reject.mutate()} disabled={reject.isPending || comment.trim().length < 3} className="rounded-cc-sm bg-crit px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Afvis med begrundelse</button>
          <button onClick={() => setRejecting(false)} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Fortryd</button>
        </>
      )}
      {error && <p role="alert" className="w-full rounded-cc-sm bg-crit-bg px-3 py-1.5 text-sm text-crit">{error}</p>}
    </div>
  );
}

function CreateForm({ contractId, onDone }: { contractId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [party, setParty] = useState("leverandoer");
  const [frequency, setFrequency] = useState("engang");
  const [deadline, setDeadline] = useState("");
  const [criticality, setCriticality] = useState("mellem");
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<Obligation>(`/api/contracts/${contractId}/obligations`, {
      method: "POST",
      body: JSON.stringify({ title, party, frequency, deadline: deadline || null, criticality }),
    }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["obligations", contractId] }); onDone(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke oprette."),
  });
  function submit(e: FormEvent) { e.preventDefault(); setError(null); create.mutate(); }
  const sel = "w-full rounded-cc-sm border border-line px-2 py-1.5 text-sm";
  return (
    <form onSubmit={submit} className="mb-3 grid grid-cols-2 gap-2 rounded-cc border border-line bg-card p-3 sm:grid-cols-6">
      <input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titel" className={`${sel} col-span-2`} />
      <select value={party} onChange={(e) => setParty(e.target.value)} className={sel}>{Object.entries(PARTY).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <select value={frequency} onChange={(e) => setFrequency(e.target.value)} className={sel}>{Object.entries(FREQ).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <input type="date" value={deadline} onChange={(e) => setDeadline(e.target.value)} className={sel} />
      <select value={criticality} onChange={(e) => setCriticality(e.target.value)} className={sel}>{Object.entries(CRIT).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      {error && <p role="alert" className="col-span-2 sm:col-span-6 rounded-cc-sm bg-crit-bg px-3 py-1.5 text-sm text-crit">{error}</p>}
      <div className="col-span-2 flex gap-2 sm:col-span-6">
        <button type="submit" disabled={create.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Opret forpligtelse</button>
        <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Annullér</button>
      </div>
    </form>
  );
}

export default function ObligationsSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);

  const obligations = useQuery({ queryKey: ["obligations", contract.id], queryFn: () => api<Obligation[]>(`/api/contracts/${contract.id}/obligations`) });
  const suggestions = useQuery({ queryKey: ["suggestions", contract.id], queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`) });
  const proposals = (suggestions.data ?? []).filter((s): s is Suggestion<ObligationPayload> => s.subject_kind === "obligation" && s.status === "foreslaaet");
  const eligible = proposals.filter((p) => p.confidence === "hoej" && p.amount_dkk === null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["obligations", contract.id] });
    void qc.invalidateQueries({ queryKey: ["suggestions", contract.id] });
  };
  const bulk = useMutation({
    mutationFn: () => api<BulkApproveOut>("/api/suggestions/bulk-approve", { method: "POST", body: JSON.stringify({ ids: eligible.slice(0, 50).map((p) => p.id) }) }),
    onSuccess: (r) => { setBulkMsg(`${r.approved.length} godkendt${r.failed.length ? `, ${r.failed.length} sprunget over` : ""}.`); refresh(); },
    onError: (e) => setBulkMsg(e instanceof ApiError ? e.message : "Samlet godkendelse mislykkedes."),
  });
  const patch = useMutation({
    mutationFn: ({ id, status }: { id: string; status: Obligation["status"] }) => api<Obligation>(`/api/obligations/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: refresh,
  });
  const run = useMutation({
    mutationFn: () => api<{ status: string }>(`/api/contracts/${contract.id}/agents/obligation_extract/run`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["agent-runs", contract.id] }); refresh(); },
  });

  const decide = can("hitl") && can("kontrakt_red");
  const rows = obligations.data ?? [];

  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Forpligtelser
          {proposals.length > 0 && <span className="ml-2 pill bg-blue-bg text-accent">{proposals.length} kræver handling</span>}
        </h2>
        <div className="flex gap-2">
          {decide && eligible.length > 0 && (
            <button onClick={() => bulk.mutate()} disabled={bulk.isPending} className="rounded-cc-sm bg-accent px-3 py-1 text-sm font-semibold text-card disabled:opacity-60">
              Godkend alle med høj sikkerhed ({Math.min(eligible.length, 50)})
            </button>
          )}
          {can("kontrakt_red") && !creating && <button onClick={() => setCreating(true)} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Ny forpligtelse</button>}
          {can("agenter") && <button onClick={() => run.mutate()} disabled={run.isPending} className="rounded-cc-sm border border-line px-3 py-1 text-sm disabled:opacity-60">Kør Obligation Extraction Agent</button>}
        </div>
      </div>
      {bulkMsg && <p className="mb-2 text-sm text-slate">{bulkMsg}</p>}
      {creating && <CreateForm contractId={contract.id} onDone={() => setCreating(false)} />}

      {rows.length === 0 && proposals.length === 0 && (
        <p className="rounded-cc border border-line bg-card p-4 text-sm text-slate">Ingen forpligtelser endnu. Upload hovedkontrakten, så læser Obligation Extraction Agent den.</p>
      )}
      {(rows.length > 0 || proposals.length > 0) && (
        <div className="overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">Titel</th><th className="px-3 py-2">Part</th><th className="px-3 py-2">Frekvens</th>
                <th className="px-3 py-2">Frist</th><th className="px-3 py-2">Kritikalitet</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Kilde</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => (
                <>
                  <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI</span></td>
                    <td className="px-3 py-2 font-medium">{p.payload.title}</td>
                    <td className="px-3 py-2">{PARTY[p.payload.party]}</td>
                    <td className="px-3 py-2">{FREQ[p.payload.frequency] ?? p.payload.frequency}</td>
                    <td className="px-3 py-2">{p.payload.deadline ? dkDay.format(new Date(p.payload.deadline)) : "—"}</td>
                    <td className="px-3 py-2"><CritPill v={p.payload.criticality} /></td>
                    <td className="px-3 py-2"><ConfPill v={p.confidence} /></td>
                    <td className="px-3 py-2">{p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                  </tr>
                  {open === p.id && (
                    <tr key={`${p.id}-x`} className="border-b border-line bg-bg">
                      <td colSpan={8} className="px-4 py-3 text-sm">
                        <p className="mb-1">{p.payload.description}</p>
                        {p.payload.consequence && <p className="mb-1 text-slate"><span className="font-semibold">Konsekvens:</span> {p.payload.consequence}</p>}
                        {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                        {p.rationale && <p className="text-xs text-muted">{p.rationale}</p>}
                        {decide ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og kontrakt_red.</p>}
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {rows.map((o) => (
                <>
                  <tr key={o.id} className="cursor-pointer border-b border-line align-top last:border-0" onClick={() => setOpen(open === o.id ? null : o.id)}>
                    <td className="px-3 py-2 font-mono">{o.ref}{o.origin === "ai" && <span className="ml-1 text-xs text-muted" title="Foreslået af AI, godkendt af et menneske">·AI</span>}</td>
                    <td className="px-3 py-2 font-medium">{o.title}</td>
                    <td className="px-3 py-2">{PARTY[o.party]}</td>
                    <td className="px-3 py-2">{FREQ[o.frequency] ?? o.frequency}</td>
                    <td className="px-3 py-2">{o.deadline ? dkDay.format(new Date(o.deadline)) : "—"}</td>
                    <td className="px-3 py-2"><CritPill v={o.criticality} /></td>
                    <td className="px-3 py-2"><StatusPill v={o.effective_status} /></td>
                    <td className="px-3 py-2">{o.citations.map((c) => <Chip key={c.id} c={c} />)}</td>
                  </tr>
                  {open === o.id && (
                    <tr key={`${o.id}-x`} className="border-b border-line bg-bg last:border-0">
                      <td colSpan={8} className="px-4 py-3 text-sm">
                        {o.description && <p className="mb-1">{o.description}</p>}
                        {o.consequence && <p className="mb-1 text-slate"><span className="font-semibold">Konsekvens:</span> {o.consequence}</p>}
                        {o.citations.filter((c) => c.quote).map((c) => <blockquote key={c.id} className="mb-1 border-l-2 border-line pl-3 text-slate">“{c.quote}” <span className="text-xs text-muted">({c.label})</span></blockquote>)}
                        {o.source_stale && <p className="mb-1 text-xs text-warn">Grundlaget findes ikke i den gældende version — tag stilling: luk, omcitér eller behold.</p>}
                        {o.note && <p className="text-xs text-muted">Note: {o.note}</p>}
                        {can("kontrakt_red") && o.status === "aaben" && (
                          <div className="mt-2 flex gap-2">
                            <button onClick={(e) => { e.stopPropagation(); patch.mutate({ id: o.id, status: "opfyldt" }); }} className="rounded-cc-sm border border-line px-3 py-1 text-xs">Markér opfyldt</button>
                            <button onClick={(e) => { e.stopPropagation(); patch.mutate({ id: o.id, status: "lukket" }); }} className="rounded-cc-sm border border-line px-3 py-1 text-xs">Luk</button>
                          </div>
                        )}
                        {can("kontrakt_red") && o.status !== "aaben" && (
                          <button onClick={(e) => { e.stopPropagation(); patch.mutate({ id: o.id, status: "aaben" }); }} className="mt-2 rounded-cc-sm border border-line px-3 py-1 text-xs">Genåbn</button>
                        )}
                      </td>
                    </tr>
                  )}
                </>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
