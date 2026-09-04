import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, type BulkApproveOut, type Contract, type Risk, type RiskPayload, type Suggestion } from "../../api/client";
import { useAuth } from "../../auth";
import { Chip, ConfPill, Verdict, dkDay } from "./shared";

// Risici: the register unioned with the Risk Agent's open proposals. Score and
// level are derived from sandsynlighed × konsekvens by the backend — the model
// proposes the two numbers, a human approves them, code computes the rest.

const CAT: Record<string, string> = {
  operationel: "Operationel", gdpr: "GDPR", kommerciel: "Kommerciel", udbudsretlig: "Udbudsretlig",
  compliance: "Compliance", juridisk: "Juridisk", leverandoer: "Leverandør", andet: "Andet",
};

function LevelPill({ score }: { score: number }) {
  const level = score >= 13 ? "hoej" : score >= 6 ? "mellem" : "lav";
  const cls = level === "hoej" ? "bg-crit-bg text-crit" : level === "mellem" ? "bg-warn-bg text-warn" : "bg-ok-bg text-ok";
  return <span className={`pill ${cls}`}>{level === "hoej" ? "Høj" : level === "mellem" ? "Mellem" : "Lav"} · {score}</span>;
}

function StatusPill({ v }: { v: Risk["status"] }) {
  const cls = v === "lukket" ? "bg-none-bg text-none" : v === "under_haandtering" ? "bg-warn-bg text-warn" : "bg-blue-bg text-accent";
  return <span className={`pill ${cls}`}>{v === "lukket" ? "Lukket" : v === "under_haandtering" ? "Under håndtering" : "Åben"}</span>;
}

function CreateForm({ contractId, onDone }: { contractId: string; onDone: () => void }) {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [category, setCategory] = useState("operationel");
  const [probability, setProbability] = useState(3);
  const [consequence, setConsequence] = useState(3);
  const [error, setError] = useState<string | null>(null);
  const create = useMutation({
    mutationFn: () => api<Risk>(`/api/contracts/${contractId}/risks`, { method: "POST", body: JSON.stringify({ title, category, probability, consequence }) }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["risks", contractId] }); onDone(); },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke oprette."),
  });
  function submit(e: FormEvent) { e.preventDefault(); setError(null); create.mutate(); }
  const sel = "w-full rounded-cc-sm border border-line px-2 py-1.5 text-sm";
  const scale = [1, 2, 3, 4, 5];
  return (
    <form onSubmit={submit} className="mb-3 grid grid-cols-2 gap-2 rounded-cc border border-line bg-card p-3 sm:grid-cols-6">
      <input required value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Titel" className={`${sel} col-span-2 sm:col-span-3`} />
      <select value={category} onChange={(e) => setCategory(e.target.value)} className={sel}>{Object.entries(CAT).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      <select value={probability} onChange={(e) => setProbability(Number(e.target.value))} className={sel} title="Sandsynlighed">{scale.map((n) => <option key={n} value={n}>S {n}</option>)}</select>
      <select value={consequence} onChange={(e) => setConsequence(Number(e.target.value))} className={sel} title="Konsekvens">{scale.map((n) => <option key={n} value={n}>K {n}</option>)}</select>
      {error && <p role="alert" className="col-span-2 sm:col-span-6 rounded-cc-sm bg-crit-bg px-3 py-1.5 text-sm text-crit">{error}</p>}
      <div className="col-span-2 flex gap-2 sm:col-span-6">
        <button type="submit" disabled={create.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Opret risiko</button>
        <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Annullér</button>
      </div>
    </form>
  );
}

export default function RisksSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);

  const risks = useQuery({ queryKey: ["risks", contract.id], queryFn: () => api<Risk[]>(`/api/contracts/${contract.id}/risks`) });
  const suggestions = useQuery({ queryKey: ["suggestions", contract.id], queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`) });
  const proposals = (suggestions.data ?? []).filter((s): s is Suggestion<RiskPayload> => s.subject_kind === "risk" && s.status === "foreslaaet");
  const eligible = proposals.filter((p) => p.confidence === "hoej" && p.amount_dkk === null);

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["risks", contract.id] });
    void qc.invalidateQueries({ queryKey: ["suggestions", contract.id] });
  };
  const bulk = useMutation({
    mutationFn: () => api<BulkApproveOut>("/api/suggestions/bulk-approve", { method: "POST", body: JSON.stringify({ ids: eligible.slice(0, 50).map((p) => p.id) }) }),
    onSuccess: (r) => { setBulkMsg(`${r.approved.length} godkendt${r.failed.length ? `, ${r.failed.length} sprunget over` : ""}.`); refresh(); },
    onError: (e) => setBulkMsg(e instanceof ApiError ? e.message : "Samlet godkendelse mislykkedes."),
  });
  const patch = useMutation({
    mutationFn: ({ id, status }: { id: string; status: Risk["status"] }) => api<Risk>(`/api/risks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: refresh,
  });
  const run = useMutation({
    mutationFn: () => api<{ status: string }>(`/api/contracts/${contract.id}/agents/risk_assess/run`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["agent-runs", contract.id] }); refresh(); },
  });

  const decide = can("hitl") && can("kontrakt_red");
  const rows = risks.data ?? [];
  const btn = "rounded-cc-sm border border-line px-3 py-1 text-xs";

  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Risici
          {proposals.length > 0 && <span className="ml-2 pill bg-blue-bg text-accent">{proposals.length} kræver handling</span>}
        </h2>
        <div className="flex gap-2">
          {decide && eligible.length > 0 && (
            <button onClick={() => bulk.mutate()} disabled={bulk.isPending} className="rounded-cc-sm bg-accent px-3 py-1 text-sm font-semibold text-card disabled:opacity-60">
              Godkend alle med høj sikkerhed ({Math.min(eligible.length, 50)})
            </button>
          )}
          {can("kontrakt_red") && !creating && <button onClick={() => setCreating(true)} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Ny risiko</button>}
          {can("agenter") && <button onClick={() => run.mutate()} disabled={run.isPending} className="rounded-cc-sm border border-line px-3 py-1 text-sm disabled:opacity-60">Kør Risk Agent</button>}
        </div>
      </div>
      {bulkMsg && <p className="mb-2 text-sm text-slate">{bulkMsg}</p>}
      {creating && <CreateForm contractId={contract.id} onDone={() => setCreating(false)} />}

      {rows.length === 0 && proposals.length === 0 && (
        <p className="rounded-cc border border-line bg-card p-4 text-sm text-slate">Ingen risici endnu. Risk Agent læser aftalegrundlaget, når det uploades.</p>
      )}
      {(rows.length > 0 || proposals.length > 0) && (
        <div className="overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">Titel</th><th className="px-3 py-2">Kategori</th><th className="px-3 py-2">S × K</th>
                <th className="px-3 py-2">Niveau</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Frist</th><th className="px-3 py-2">Kilde</th>
              </tr>
            </thead>
            <tbody>
              {proposals.map((p) => (
                <>
                  <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI</span></td>
                    <td className="px-3 py-2 font-medium">{p.payload.title}</td>
                    <td className="px-3 py-2">{CAT[p.payload.category] ?? p.payload.category}</td>
                    <td className="px-3 py-2 font-mono">{p.payload.probability} × {p.payload.consequence}</td>
                    <td className="px-3 py-2"><LevelPill score={p.payload.probability * p.payload.consequence} /></td>
                    <td className="px-3 py-2"><ConfPill v={p.confidence} /></td>
                    <td className="px-3 py-2">—</td>
                    <td className="px-3 py-2">{p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                  </tr>
                  {open === p.id && (
                    <tr key={`${p.id}-x`} className="border-b border-line bg-bg">
                      <td colSpan={8} className="px-4 py-3 text-sm">
                        <p className="mb-1">{p.payload.description}</p>
                        <p className="mb-1 text-slate"><span className="font-semibold">Afværgelse:</span> {p.payload.mitigation}</p>
                        {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                        {p.rationale && <p className="text-xs text-muted">{p.rationale}</p>}
                        {decide ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og kontrakt_red.</p>}
                      </td>
                    </tr>
                  )}
                </>
              ))}
              {rows.map((r) => (
                <>
                  <tr key={r.id} className="cursor-pointer border-b border-line align-top last:border-0" onClick={() => setOpen(open === r.id ? null : r.id)}>
                    <td className="px-3 py-2 font-mono">{r.ref}{r.origin === "ai" && <span className="ml-1 text-xs text-muted" title="Foreslået af AI, godkendt af et menneske">·AI</span>}</td>
                    <td className="px-3 py-2 font-medium">{r.title}</td>
                    <td className="px-3 py-2">{CAT[r.category] ?? r.category}</td>
                    <td className="px-3 py-2 font-mono">{r.probability} × {r.consequence}</td>
                    <td className="px-3 py-2"><LevelPill score={r.score} /></td>
                    <td className="px-3 py-2"><StatusPill v={r.status} /></td>
                    <td className="px-3 py-2">{r.deadline ? dkDay.format(new Date(r.deadline)) : "—"}</td>
                    <td className="px-3 py-2">{r.citations.map((c) => <Chip key={c.id} c={c} />)}</td>
                  </tr>
                  {open === r.id && (
                    <tr key={`${r.id}-x`} className="border-b border-line bg-bg last:border-0">
                      <td colSpan={8} className="px-4 py-3 text-sm">
                        {r.description && <p className="mb-1">{r.description}</p>}
                        {r.mitigation && <p className="mb-1 text-slate"><span className="font-semibold">Afværgelse:</span> {r.mitigation}</p>}
                        {r.citations.filter((c) => c.quote).map((c) => <blockquote key={c.id} className="mb-1 border-l-2 border-line pl-3 text-slate">“{c.quote}” <span className="text-xs text-muted">({c.label})</span></blockquote>)}
                        {r.source_stale && <p className="mb-1 text-xs text-warn">Grundlaget findes ikke i den gældende version.</p>}
                        {r.note && <p className="text-xs text-muted">Note: {r.note}</p>}
                        {can("kontrakt_red") && (
                          <div className="mt-2 flex gap-2" onClick={(e) => e.stopPropagation()}>
                            {r.status !== "under_haandtering" && r.status !== "lukket" && <button onClick={() => patch.mutate({ id: r.id, status: "under_haandtering" })} className={btn}>Under håndtering</button>}
                            {r.status !== "lukket" && <button onClick={() => patch.mutate({ id: r.id, status: "lukket" })} className={btn}>Luk</button>}
                            {r.status !== "aaben" && <button onClick={() => patch.mutate({ id: r.id, status: "aaben" })} className={btn}>Genåbn</button>}
                          </div>
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
