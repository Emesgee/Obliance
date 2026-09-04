import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import {
  api,
  ApiError,
  type Contract,
  type Kpi,
  type KpiPayload,
  type MeasurementPayload,
  type PenaltyTerm,
  type PenaltyTermPayload,
  type Suggestion,
} from "../../api/client";
import { useAuth } from "../../auth";
import { Chip, ConfPill, Verdict, dkDay } from "./shared";

// KPI og SLA (ADR-0019): the target is a clause, the measurement a fact about a
// period, the status a derivation — grey means "data mangler", never green by
// default. Bodsklausuler (ADR-0013 §1) are approved parameters; the model never
// computes an amount. Register ∪ proposals, as elsewhere.

const PERIOD: Record<string, string> = { maaned: "Måned", kvartal: "Kvartal", halvaar: "Halvår", aar: "År" };
const UNIT: Record<string, string> = { pct: "%", antal: "stk.", timer: "timer", dkk: "kr.", score: "" };
const TERM: Record<string, string> = {
  service_credit_pct_of_fee: "Service credit, % af vederlag",
  service_credit_tiered: "Service credit, trappe",
  delivery_penalty_per_week: "Bod pr. påbegyndt uge",
  fixed_penalty_per_breach: "Fast bod pr. brud",
};
const BASIS: Record<string, string> = {
  maanedligt_driftsvederlag: "månedligt driftsvederlag",
  aarligt_vederlag: "årligt vederlag",
  vaerdi_ikke_leverede_ordrelinjer: "værdien af ikke-leverede ordrelinjer",
  maanedens_omsaetning: "månedens omsætning",
  fast_beloeb: "fast beløb",
};
const dkNum = new Intl.NumberFormat("da-DK", { maximumFractionDigits: 2 });

function num(v: string | null | undefined, unit?: string): string {
  if (v === null || v === undefined) return "—";
  return `${dkNum.format(Number(v))}${unit ? ` ${unit}` : ""}`.trim();
}

export function StatusLight({ color, reason }: { color: Kpi["status"]["color"]; reason: string }) {
  const cls = color === "groen" ? "bg-ok-bg text-ok" : color === "gul" ? "bg-warn-bg text-warn" : color === "roed" ? "bg-crit-bg text-crit" : "bg-none-bg text-none";
  const label = color === "groen" ? "Grøn" : color === "gul" ? "Gul" : color === "roed" ? "Rød" : "Grå";
  return <span className={`pill ${cls}`} title={reason}>{label}</span>;
}

function termText(t: PenaltyTerm): string {
  const parts: string[] = [];
  if (t.rate) parts.push(`${dkNum.format(Number(t.rate) * 100)} % af ${BASIS[t.basis] ?? t.basis}`);
  if (t.tiers?.length) parts.push(t.tiers.map((x) => `< ${x.below}: ${dkNum.format(Number(x.rate) * 100)} %`).join(", "));
  if (t.basis === "fast_beloeb" && t.basis_amount) parts.push(`${dkNum.format(Number(t.basis_amount))} kr.`);
  else if (t.basis_amount) parts.push(`(${dkNum.format(Number(t.basis_amount))} kr.)`);
  if (t.cap_rate) parts.push(`loft ${dkNum.format(Number(t.cap_rate) * 100) } % af månedens omsætning`);
  if (t.cap_amount) parts.push(`loft ${dkNum.format(Number(t.cap_amount))} kr.`);
  return parts.join(" · ") || TERM[t.term_type] || t.term_type;
}

function MeasureForm({ kpi, onDone }: { kpi: Kpi; onDone: () => void }) {
  const qc = useQueryClient();
  const [month, setMonth] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const record = useMutation({
    mutationFn: () => api<{ breach: unknown | null; claim: { ref: string; amount: string | null } | null }>(`/api/kpis/${kpi.id}/measurements`, {
      method: "POST",
      body: JSON.stringify({ period_start: `${month}-01`, value: value.replace(",", "."), note: note || null }),
    }),
    onSuccess: (r) => {
      setMsg(r.breach ? (r.claim ? `SLA-brud registreret — krav ${r.claim.ref} beregnet${r.claim.amount ? ` (${dkNum.format(Number(r.claim.amount))} kr.)` : ""}.` : "SLA-brud registreret uden krav (ingen godkendte parametre).") : "Måling registreret — målet er opfyldt.");
      void qc.invalidateQueries({ queryKey: ["kpis", kpi.contract_id] });
      void qc.invalidateQueries({ queryKey: ["claims", kpi.contract_id] });
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Kunne ikke registrere målingen."),
  });
  function submit(e: FormEvent) { e.preventDefault(); setMsg(null); record.mutate(); }
  return (
    <form onSubmit={submit} className="mt-2 flex flex-wrap items-end gap-2" onClick={(e) => e.stopPropagation()}>
      <label className="text-xs text-muted">Periode (første måned)<br /><input type="month" required value={month} onChange={(e) => setMonth(e.target.value)} className="rounded-cc-sm border border-line px-2 py-1 text-sm" /></label>
      <label className="text-xs text-muted">Værdi ({UNIT[kpi.unit] || kpi.unit})<br /><input required value={value} onChange={(e) => setValue(e.target.value)} placeholder="99,62" className="w-28 rounded-cc-sm border border-line px-2 py-1 text-sm" /></label>
      <label className="min-w-[14rem] flex-1 text-xs text-muted">Note (påkrævet ved erstatning)<br /><input value={note} onChange={(e) => setNote(e.target.value)} className="w-full rounded-cc-sm border border-line px-2 py-1 text-sm" /></label>
      <button type="submit" disabled={record.isPending} className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card disabled:opacity-60">Registrér måling</button>
      <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Luk</button>
      {msg && <p className="w-full text-sm text-slate">{msg}</p>}
    </form>
  );
}

export default function KpisSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [measuring, setMeasuring] = useState<string | null>(null);

  const kpis = useQuery({ queryKey: ["kpis", contract.id], queryFn: () => api<Kpi[]>(`/api/contracts/${contract.id}/kpis`) });
  const terms = useQuery({ queryKey: ["penalty-terms", contract.id], queryFn: () => api<PenaltyTerm[]>(`/api/contracts/${contract.id}/penalty-terms`) });
  const suggestions = useQuery({ queryKey: ["suggestions", contract.id], queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`) });
  const open_ = (suggestions.data ?? []).filter((s) => s.status === "foreslaaet");
  const kpiProps = open_.filter((s): s is Suggestion<KpiPayload> => s.subject_kind === "kpi");
  const measProps = open_.filter((s): s is Suggestion<MeasurementPayload> => s.subject_kind === "kpi_measurement");
  const termProps = open_.filter((s): s is Suggestion<PenaltyTermPayload> => s.subject_kind === "penalty_term");
  const refresh = () => {
    for (const k of ["kpis", "penalty-terms", "suggestions", "claims"]) void qc.invalidateQueries({ queryKey: [k, contract.id] });
  };
  const run = useMutation({
    mutationFn: () => api<{ status: string }>(`/api/contracts/${contract.id}/agents/kpi_parse/run`, { method: "POST" }),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ["agent-runs", contract.id] }); refresh(); },
  });
  const decideKpi = can("hitl") && can("kontrakt_red");
  const decideMoney = can("hitl") && can("okonomi");
  const canMeasure = can("kontrakt_red") || can("okonomi");
  const rows = kpis.data ?? [];
  const termRows = terms.data ?? [];
  const termById = new Map(termRows.map((t) => [t.id, t]));
  const pending = kpiProps.length + measProps.length + termProps.length;

  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          KPI og SLA
          {pending > 0 && <span className="ml-2 pill bg-blue-bg text-accent">{pending} kræver handling</span>}
        </h2>
        {can("agenter") && <button onClick={() => run.mutate()} disabled={run.isPending} className="rounded-cc-sm border border-line px-3 py-1 text-sm disabled:opacity-60">Læs seneste rapport (KPI/SLA Agent)</button>}
      </div>

      {rows.length === 0 && pending === 0 && (
        <p className="rounded-cc border border-line bg-card p-4 text-sm text-slate">Ingen KPI'er endnu. Målene foreslås fra aftalegrundlaget sammen med forpligtelserne.</p>
      )}
      {(rows.length > 0 || kpiProps.length > 0 || measProps.length > 0) && (
        <div className="mb-3 overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[760px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">KPI</th><th className="px-3 py-2">Mål</th><th className="px-3 py-2">Periode</th>
                <th className="px-3 py-2">Status</th><th className="px-3 py-2">Seneste</th><th className="px-3 py-2">Bod</th><th className="px-3 py-2">Kilde</th>
              </tr>
            </thead>
            <tbody>
              {kpiProps.map((p) => (
                <>
                  <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI</span></td>
                    <td className="px-3 py-2 font-medium">{p.payload.name}</td>
                    <td className="px-3 py-2 font-mono">{p.payload.target_text}</td>
                    <td className="px-3 py-2">{PERIOD[p.payload.period] ?? p.payload.period}</td>
                    <td className="px-3 py-2"><ConfPill v={p.confidence} /></td>
                    <td className="px-3 py-2 text-muted">—</td><td className="px-3 py-2 text-muted">—</td>
                    <td className="px-3 py-2">{p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                  </tr>
                  {open === p.id && (
                    <tr key={`${p.id}-x`} className="border-b border-line bg-bg"><td colSpan={8} className="px-4 py-3 text-sm">
                      {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                      <p className="text-xs text-muted">{p.rationale}</p>
                      {decideKpi ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og kontrakt_red.</p>}
                    </td></tr>
                  )}
                </>
              ))}
              {measProps.map((p) => (
                <>
                  <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI · måling</span></td>
                    <td className="px-3 py-2 font-medium">{p.payload.kpi_name}</td>
                    <td className="px-3 py-2 font-mono">{p.payload.target_text}</td>
                    <td className="px-3 py-2">{dkDay.format(new Date(p.payload.period_start))}</td>
                    <td className="px-3 py-2"><ConfPill v={p.confidence} /></td>
                    <td className="px-3 py-2 font-mono">{num(p.payload.value, UNIT[p.payload.unit])}</td>
                    <td className="px-3 py-2 text-muted">—</td>
                    <td className="px-3 py-2">{p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                  </tr>
                  {open === p.id && (
                    <tr key={`${p.id}-x`} className="border-b border-line bg-bg"><td colSpan={8} className="px-4 py-3 text-sm">
                      {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                      <p className="text-xs text-muted">Godkendes målingen, sammenlignes den med målet i kode. Et brud udløser et beregnet krav, hvis en bodsklausul er godkendt.</p>
                      {decideKpi ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og kontrakt_red.</p>}
                    </td></tr>
                  )}
                </>
              ))}
              {rows.map((k) => {
                const term = k.penalty_term_id ? termById.get(k.penalty_term_id) : undefined;
                return (
                  <>
                    <tr key={k.id} className="cursor-pointer border-b border-line align-top last:border-0" onClick={() => setOpen(open === k.id ? null : k.id)}>
                      <td className="px-3 py-2 font-mono">{k.ref}{k.origin === "ai" && <span className="ml-1 text-xs text-muted">·AI</span>}</td>
                      <td className="px-3 py-2 font-medium">{k.name}</td>
                      <td className="px-3 py-2 font-mono">{k.target_text}</td>
                      <td className="px-3 py-2">{PERIOD[k.period]}</td>
                      <td className="px-3 py-2"><StatusLight color={k.status.color} reason={k.status.reason} /> <span className="text-xs text-muted">{k.status.reason}</span></td>
                      <td className="px-3 py-2 font-mono">{k.status.value !== null ? `${num(k.status.value, UNIT[k.unit])}` : "—"}{k.status.measured_period_start && <span className="ml-1 text-xs text-muted">{dkDay.format(new Date(k.status.measured_period_start))}</span>}</td>
                      <td className="px-3 py-2">{term ? <span className="font-mono text-xs">{term.ref}</span> : <span className="text-muted">—</span>}</td>
                      <td className="px-3 py-2">{k.citations.map((c) => <Chip key={c.id} c={c} />)}</td>
                    </tr>
                    {open === k.id && (
                      <tr key={`${k.id}-x`} className="border-b border-line bg-bg last:border-0"><td colSpan={8} className="px-4 py-3 text-sm">
                        {term && <p className="mb-2 text-slate"><span className="font-semibold">{term.ref} {term.name}:</span> {termText(term)}</p>}
                        <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Historik</p>
                        {k.measurements.length === 0 ? <p className="text-xs text-muted">Ingen målinger — grå betyder data mangler, ikke nul.</p> : (
                          <ul className="mb-2">
                            {k.measurements.map((m) => (
                              <li key={m.id} className={`flex gap-3 py-0.5 ${m.superseded_by_id ? "text-muted line-through" : ""}`}>
                                <span className="w-24 font-mono text-xs">{dkDay.format(new Date(m.period_start))}</span>
                                <span className="font-mono">{num(m.value, UNIT[k.unit])}</span>
                                <span className="text-xs text-muted">{m.source_kind}{m.note ? ` · ${m.note}` : ""}</span>
                              </li>
                            ))}
                          </ul>
                        )}
                        {canMeasure && measuring !== k.id && <button onClick={(e) => { e.stopPropagation(); setMeasuring(k.id); }} className="rounded-cc-sm border border-line px-3 py-1 text-xs">Registrér måling</button>}
                        {measuring === k.id && <MeasureForm kpi={k} onDone={() => setMeasuring(null)} />}
                      </td></tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {(termRows.length > 0 || termProps.length > 0) && (
        <div className="overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">Bodsklausul</th><th className="px-3 py-2">Udløses ved</th><th className="px-3 py-2">Parametre</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Kilde</th>
              </tr>
            </thead>
            <tbody>
              {termProps.map((p) => (
                <>
                  <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                    <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI</span></td>
                    <td className="px-3 py-2 font-medium">{p.payload.name}</td>
                    <td className="px-3 py-2">{p.payload.trigger_description}</td>
                    <td className="px-3 py-2">{TERM[p.payload.term_type] ?? p.payload.term_type}{p.payload.rate ? ` · ${dkNum.format(Number(p.payload.rate) * 100)} % af ${BASIS[p.payload.basis] ?? p.payload.basis}` : ""}</td>
                    <td className="px-3 py-2"><ConfPill v={p.confidence} /></td>
                    <td className="px-3 py-2">{p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                  </tr>
                  {open === p.id && (
                    <tr key={`${p.id}-x`} className="border-b border-line bg-bg"><td colSpan={6} className="px-4 py-3 text-sm">
                      {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                      <p className="text-xs text-muted">Pengeparametre godkendes af okonomi (ADR-0013). Først derefter bruges de i en beregning.</p>
                      {decideMoney ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og okonomi.</p>}
                    </td></tr>
                  )}
                </>
              ))}
              {termRows.map((t) => (
                <tr key={t.id} className="border-b border-line align-top last:border-0">
                  <td className="px-3 py-2 font-mono">{t.ref}{t.origin === "ai" && <span className="ml-1 text-xs text-muted">·AI</span>}</td>
                  <td className="px-3 py-2 font-medium">{t.name}{t.applies_to && <span className="block text-xs text-muted">→ {t.applies_to}</span>}</td>
                  <td className="px-3 py-2">{t.trigger_description ?? "—"}</td>
                  <td className="px-3 py-2">{termText(t)}</td>
                  <td className="px-3 py-2"><span className={`pill ${t.status === "aktiv" ? "bg-ok-bg text-ok" : "bg-warn-bg text-warn"}`}>{t.status === "aktiv" ? "Godkendt" : "Kræver godkendelse"}</span></td>
                  <td className="px-3 py-2">{t.citations.map((c) => <Chip key={c.id} c={c} />)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
