import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Dashboard as DashboardData } from "../api/client";
import { useAuth } from "../auth";

// Overblik — a roll-up of what the caller may see (ADR-0001 §Overblik). Amounts
// are absent, not zero, without `okonomi` (ADR-0003). "Kræver handling" is the
// one HITL queue (ADR-0004), "Frister" the derived deadline queue (ADR-0017).

const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", maximumFractionDigits: 0 });
const dkk2 = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });
const dkDay = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium" });
const dkTime = new Intl.DateTimeFormat("da-DK", { dateStyle: "short", timeStyle: "short" });

const SUBJECT: Record<string, string> = {
  contract_intake: "Stamdata", obligation: "Forpligtelse", risk: "Risiko", raci_entry: "RACI",
  invoice_finding: "Faktura", sla_breach: "SLA-brud", task: "Opgave", kpi: "KPI", kpi_measurement: "Måling",
  penalty_term: "Bodsklausul",
};
const KIND: Record<string, string> = { opsigelse: "Opsigelse", udloeb: "Udløb", forpligtelse: "Forpligtelse", risiko: "Risiko" };
const TASK: Record<string, string> = {
  contract_intake: "Contract Intake", obligation_extract: "Obligation Extraction", risk_assess: "Risk",
  kpi_parse: "KPI/SLA", copilot: "Copilot",
};

function Tile({ label, value, tone, to }: { label: string; value: string | number; tone?: "crit" | "warn" | "ok" | "accent"; to?: string }) {
  const color = tone === "crit" ? "text-crit" : tone === "warn" ? "text-warn" : tone === "ok" ? "text-ok" : tone === "accent" ? "text-accent" : "text-ink";
  const body = (
    <div className="rounded-cc border border-line bg-card p-4">
      <div className="text-xs font-semibold uppercase tracking-wider text-muted">{label}</div>
      <div className={`mt-1 text-2xl font-bold tabular-nums ${color}`}>{value}</div>
    </div>
  );
  return to ? <Link to={to} className="block hover:opacity-90">{body}</Link> : body;
}

function SevPill({ v }: { v: string }) {
  const cls = v === "hoej" ? "bg-crit-bg text-crit" : v === "mellem" ? "bg-warn-bg text-warn" : "bg-none-bg text-none";
  return <span className={`pill ${cls}`}>{v === "hoej" ? "Høj" : v === "mellem" ? "Mellem" : "Lav"}</span>;
}

function ConfPill({ v }: { v: string }) {
  const cls = v === "hoej" ? "bg-ok-bg text-ok" : v === "mellem" ? "bg-warn-bg text-warn" : "bg-crit-bg text-crit";
  return <span className={`pill ${cls}`}>{v === "hoej" ? "Høj" : v === "mellem" ? "Mellem" : "Lav"}</span>;
}

function daysLabel(d: number): string {
  if (d < 0) return `${-d} dage over tid`;
  if (d === 0) return "i dag";
  if (d === 1) return "i morgen";
  return `om ${d} dage`;
}

export default function Dashboard() {
  const { me } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardData>("/api/dashboard"), refetchInterval: 60_000 });

  if (q.isLoading) return <p className="text-slate">Henter overblik …</p>;
  if (q.isError || !q.data) return <p role="alert" className="text-crit">Kunne ikke hente overblikket.</p>;
  const d = q.data;
  const c = d.counts;

  return (
    <section>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Overblik</h1>
        <span className="text-sm text-muted">{me?.org_name} · {dkDay.format(new Date())}</span>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Tile label="Kontrakter under overvågning" value={c.contracts_active} to="/contracts" />
        <Tile label="Kræver handling" value={c.suggestions_open} tone={c.suggestions_open ? "accent" : undefined} />
        <Tile label="Forsinkede forpligtelser" value={c.obligations_overdue} tone={c.obligations_overdue ? "crit" : "ok"} />
        <Tile label="Åbne risici · høj" value={`${c.risks_open} · ${c.risks_high}`} tone={c.risks_high ? "warn" : undefined} />
        <Tile label="KPI'er uden data" value={`${c.kpis_gray} af ${c.kpis_total}`} tone={c.kpis_gray ? "warn" : undefined} />
        <Tile label="Krav til godkendelse" value={c.claims_pending} tone={c.claims_pending ? "accent" : undefined} />
        <Tile label="Årlig værdi" value={d.portfolio_annual_value === null ? "—" : dkk.format(Number(d.portfolio_annual_value))} />
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <section className="rounded-cc border border-line bg-card">
          <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">Kræver handling</h2>
          {d.actions.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate">Ingen åbne AI-forslag. Køen er tom.</p>
          ) : (
            <ul>
              {d.actions.slice(0, 12).map((a) => (
                <li key={a.suggestion_id} className="border-b border-line px-4 py-2 last:border-0">
                  <Link to={`/contracts/${a.contract_id}`} className="flex items-center gap-2 text-sm hover:text-accent">
                    <span className={`pill ${a.kind === "claim" ? "bg-warn-bg text-warn" : "bg-blue-bg text-accent"}`}>{a.kind === "claim" ? "Krav" : SUBJECT[a.subject_kind] ?? a.subject_kind}</span>
                    <span className="min-w-0 flex-1 truncate">{a.title}</span>
                    <span className="font-mono text-xs text-muted">{a.contract_ref}</span>
                    <ConfPill v={a.confidence} />
                    {!a.can_decide && <span className="text-xs text-muted" title="Du har ikke tilladelse til at afgøre dette forslag">·</span>}
                  </Link>
                </li>
              ))}
              {d.actions.length > 12 && <li className="px-4 py-2 text-xs text-muted">+ {d.actions.length - 12} flere</li>}
            </ul>
          )}
        </section>

        <section className="rounded-cc border border-line bg-card">
          <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">Frister · næste {d.window_days} dage</h2>
          {d.deadlines.length === 0 ? (
            <p className="px-4 py-6 text-sm text-slate">Ingen frister i vinduet.</p>
          ) : (
            <ul>
              {d.deadlines.slice(0, 12).map((x, i) => (
                <li key={`${x.kind}-${x.subject_id ?? x.contract_id}-${i}`} className="border-b border-line px-4 py-2 last:border-0">
                  <Link to={`/contracts/${x.contract_id}`} className="flex items-center gap-2 text-sm hover:text-accent">
                    <span className={`w-24 shrink-0 font-mono text-xs ${x.days_left < 0 ? "text-crit" : "text-muted"}`}>{dkDay.format(new Date(x.due_date))}</span>
                    <span className="pill bg-none-bg text-none">{KIND[x.kind] ?? x.kind}</span>
                    <span className="min-w-0 flex-1 truncate">{x.label}</span>
                    <span className="font-mono text-xs text-muted">{x.contract_ref}</span>
                    <span className={`text-xs ${x.days_left < 0 ? "text-crit" : "text-muted"}`}>{daysLabel(x.days_left)}</span>
                    <SevPill v={x.severity} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <section className="rounded-cc border border-line bg-card lg:col-span-2">
          <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">AI-agenter</h2>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-4 py-2">Agent</th><th className="px-4 py-2">Status</th><th className="px-4 py-2">Sidst kørt</th><th className="px-4 py-2">Fund</th><th className="px-4 py-2">Fejl (7 d.)</th>
              </tr>
            </thead>
            <tbody>
              {d.agents.map((a) => (
                <tr key={a.agent_key} className="border-b border-line last:border-0">
                  <td className="px-4 py-2 font-medium">{a.label}</td>
                  <td className="px-4 py-2">
                    <span className={`pill ${!a.enabled ? "bg-none-bg text-none" : a.last_status === "fejlet" ? "bg-crit-bg text-crit" : "bg-ok-bg text-ok"}`}>
                      {!a.enabled ? "Pauset" : a.last_status === "fejlet" ? "Fejlet" : "Aktiv"}
                    </span>
                  </td>
                  <td className="px-4 py-2 text-muted">{a.last_run_at ? dkTime.format(new Date(a.last_run_at)) : "—"}</td>
                  <td className="px-4 py-2 font-mono">{a.last_findings ?? "—"}</td>
                  <td className={`px-4 py-2 font-mono ${a.runs_failed_7d ? "text-crit" : ""}`}>{a.runs_failed_7d}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="rounded-cc border border-line bg-card">
          <h2 className="border-b border-line px-4 py-3 text-sm font-semibold uppercase tracking-wider text-muted">AI-forbrug · denne måned</h2>
          {d.ai_spend === null ? (
            <p className="px-4 py-6 text-sm text-slate">Kræver tilladelsen okonomi.</p>
          ) : (
            <div className="px-4 py-3 text-sm">
              <div className="text-2xl font-bold tabular-nums">{dkk2.format(Number(d.ai_spend.month_dkk))}</div>
              <div className="mb-2 text-xs text-muted">USD {Number(d.ai_spend.month_usd).toFixed(2)}</div>
              <ul>
                {Object.entries(d.ai_spend.by_task).sort(([, a], [, b]) => Number(b) - Number(a)).map(([task, amount]) => (
                  <li key={task} className="flex justify-between border-t border-line py-1">
                    <span>{TASK[task] ?? task}</span><span className="font-mono tabular-nums">{dkk2.format(Number(amount))}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </div>
    </section>
  );
}
