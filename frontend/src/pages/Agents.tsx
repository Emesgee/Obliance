import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { api, type AgentInfo, type AgentRun, ApiError } from "../api/client";

// AI-agenter (ADR-0010 §2, afklaring 2–3): the mockup's agent screen. Status is
// `agent_settings`, "sidst kørt · fund" is the latest finished `agent_runs` row,
// alerts are ADR-0010 §7's four conditions. Pausing asks for a reason and records
// who and when; "Kør nu" is the scheduler's own job with trigger = manual.

const dkTime = new Intl.DateTimeFormat("da-DK", { dateStyle: "short", timeStyle: "short" });
const dkk2 = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });

const STATUS: Record<AgentRun["status"], string> = { koerer: "Kører", ok: "OK", fejlet: "Fejlet", sprunget_over: "Sprunget over" };
const TRIGGER: Record<AgentRun["trigger"], string> = { schedule: "Kalender", event: "Hændelse", manual: "Manuel" };

function cadenceLabel(a: AgentInfo): string {
  const cron = a.schedule_override ?? a.cadence;
  const parts: string[] = [];
  if (cron) {
    const [m, h] = cron.split(" ");
    const daily = /^\d+$/.test(m) && /^\d+$/.test(h);
    parts.push(daily ? `dagligt kl. ${h.padStart(2, "0")}:${m.padStart(2, "0")}` : cron);
    if (a.schedule_override) parts[0] += " (tilpasset)";
  }
  if (a.event) parts.push(`ved ${a.event}`);
  return parts.join(" · ") || "—";
}

function RunPill({ status }: { status: AgentRun["status"] }) {
  const cls = status === "ok" ? "bg-ok-bg text-ok" : status === "fejlet" ? "bg-crit-bg text-crit" : status === "koerer" ? "bg-blue-bg text-accent" : "bg-none-bg text-none";
  return <span className={`pill ${cls}`}>{STATUS[status]}</span>;
}

function Runs({ agentKey }: { agentKey: string }) {
  const q = useQuery({ queryKey: ["agent-runs-org", agentKey], queryFn: () => api<AgentRun[]>(`/api/agents/${agentKey}/runs?limit=20`) });
  if (q.isLoading) return <p className="px-4 py-2 text-xs text-muted">Henter kørsler …</p>;
  const rows = q.data ?? [];
  if (rows.length === 0) return <p className="px-4 py-2 text-xs text-muted">Ingen kørsler endnu.</p>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-line text-left uppercase tracking-wider text-muted">
          <th className="px-4 py-1">Startet</th><th className="px-2 py-1">Udløst af</th><th className="px-2 py-1">Status</th><th className="px-2 py-1">Kontrakter</th><th className="px-2 py-1">Fund</th><th className="px-2 py-1">Pris</th><th className="px-2 py-1">Bemærkning</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id} className="border-b border-line last:border-0">
            <td className="px-4 py-1 font-mono">{dkTime.format(new Date(r.started_at))}</td>
            <td className="px-2 py-1">{TRIGGER[r.trigger]}{r.contract_id ? " · én kontrakt" : ""}</td>
            <td className="px-2 py-1"><RunPill status={r.status} /></td>
            <td className="px-2 py-1 font-mono">{r.status === "sprunget_over" ? "—" : r.contracts_scanned}</td>
            <td className="px-2 py-1 font-mono">{r.status === "ok" ? r.suggestions_created + r.suggestions_updated : "—"}</td>
            <td className="px-2 py-1 font-mono">{r.cost_dkk ? dkk2.format(Number(r.cost_dkk)) : "—"}</td>
            <td className="max-w-md truncate px-2 py-1 text-muted" title={r.error ?? ""}>{r.error ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Agents() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["agents"], queryFn: () => api<AgentInfo[]>("/api/agents"), refetchInterval: 30_000 });
  const [open, setOpen] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["agents"] }); void qc.invalidateQueries({ queryKey: ["agent-runs-org"] }); void qc.invalidateQueries({ queryKey: ["dashboard"] }); };
  const fail = (e: unknown) => setMsg(e instanceof ApiError ? e.message : "Noget gik galt.");

  const setEnabled = useMutation({
    mutationFn: ({ key, enabled, reason }: { key: string; enabled: boolean; reason?: string }) =>
      api<AgentInfo>(`/api/agents/${key}/settings`, { method: "PUT", body: JSON.stringify({ enabled, reason: reason ?? null }) }),
    onSuccess: () => { setMsg(null); refresh(); }, onError: fail,
  });
  const runNow = useMutation({
    mutationFn: (key: string) => api<{ status: string }>(`/api/agents/${key}/run`, { method: "POST" }),
    onSuccess: (_d, key) => { setMsg(`Kørsel sat i gang for ${key}. Resultatet lander i kørselssporet.`); setTimeout(refresh, 1500); }, onError: fail,
  });

  if (q.isLoading) return <p className="text-slate">Henter agenter …</p>;
  if (q.isError || !q.data) return <p role="alert" className="text-crit">Kunne ikke hente agenterne.</p>;

  const pause = (a: AgentInfo) => {
    const reason = window.prompt(`Hvorfor pauses ${a.label}? Begrundelsen gemmes i audit-sporet.`);
    if (reason && reason.trim()) setEnabled.mutate({ key: a.agent_key, enabled: false, reason: reason.trim() });
  };

  return (
    <section>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-2xl font-bold tracking-tight">AI-agenter</h1>
        <span className="text-sm text-muted">Kalender i dansk tid · én kørsel pr. agent pr. organisation</span>
      </div>
      {msg && <p role="status" className="mb-3 rounded-cc-sm border border-line bg-card px-3 py-2 text-sm">{msg}</p>}
      <div className="rounded-cc border border-line bg-card">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
              <th className="px-4 py-2">Agent</th><th className="px-2 py-2">Kører</th><th className="px-2 py-2">Status</th><th className="px-2 py-2">Sidst kørt</th><th className="px-2 py-2">Fund</th><th className="px-2 py-2">Alarmer</th><th className="px-2 py-2"></th>
            </tr>
          </thead>
          <tbody>
            {q.data.map((a) => (
              <Fragment key={a.agent_key}>
                <tr className="border-b border-line">
                  <td className="px-4 py-2">
                    <button onClick={() => setOpen(open === a.agent_key ? null : a.agent_key)} className="text-left font-medium hover:text-accent">{a.label}</button>
                    <div className="text-xs text-muted">{a.purpose}{a.task === null ? " Regelbaseret, ingen model." : ""}</div>
                  </td>
                  <td className="px-2 py-2 text-xs text-muted">{cadenceLabel(a)}</td>
                  <td className="px-2 py-2">
                    <span className={`pill ${a.enabled ? "bg-ok-bg text-ok" : "bg-none-bg text-none"}`}>{a.enabled ? "Aktiv" : "Pauset"}</span>
                    {!a.enabled && <div className="mt-1 text-xs text-muted" title={a.paused_reason ?? ""}>{a.paused_by_name ?? "?"} · {a.paused_at ? dkTime.format(new Date(a.paused_at)) : ""}</div>}
                  </td>
                  <td className="px-2 py-2 text-xs">
                    {a.last_run ? (<><RunPill status={a.last_run.status} /> <span className="text-muted">{dkTime.format(new Date(a.last_run.started_at))}</span></>) : <span className="text-muted">—</span>}
                  </td>
                  <td className="px-2 py-2 font-mono">{a.last_run && a.last_run.status === "ok" ? a.last_run.suggestions_created + a.last_run.suggestions_updated : "—"}</td>
                  <td className="px-2 py-2 text-xs">
                    {a.alerts.length === 0 ? <span className="text-muted">—</span> : a.alerts.map((x) => <div key={x} className="text-crit">⚠ {x}</div>)}
                  </td>
                  <td className="px-2 py-2 text-right whitespace-nowrap">
                    <button onClick={() => runNow.mutate(a.agent_key)} disabled={!a.enabled || runNow.isPending} className="rounded-cc-sm border border-line px-2 py-1 text-xs disabled:opacity-50">Kør nu</button>{" "}
                    {a.enabled
                      ? <button onClick={() => pause(a)} className="rounded-cc-sm border border-line px-2 py-1 text-xs">Pause</button>
                      : <button onClick={() => setEnabled.mutate({ key: a.agent_key, enabled: true })} className="rounded-cc-sm border border-accent px-2 py-1 text-xs text-accent">Genoptag</button>}
                  </td>
                </tr>
                {open === a.agent_key && (
                  <tr className="border-b border-line bg-bg">
                    <td colSpan={7} className="p-0"><Runs agentKey={a.agent_key} /></td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
