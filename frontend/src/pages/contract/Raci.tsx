import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type Contract, type Member, type Raci, type RaciActivity, type RaciPayload, type Suggestion, type Task, type TaskPayload } from "../../api/client";
import { useAuth } from "../../auth";
import { Chip, ConfPill, Verdict, dkDay } from "./shared";

// Ansvar og governance (ADR-0021): the matrix (activities × functions, one row per
// filled cell), staffing (function → person, mirrored to owner/manager), gap
// findings as task proposals from the rule agent, and the tasks themselves.

const LETTERS = ["", "R", "A", "C", "I"];
const CRIT: Record<string, string> = { lav: "Lav", mellem: "Mellem", hoej: "Høj", kritisk: "Kritisk" };
const PRIO: Record<string, [string, string]> = { lav: ["Lav", "bg-none-bg text-none"], mellem: ["Mellem", "bg-warn-bg text-warn"], hoej: ["Høj", "bg-crit-bg text-crit"] };

function letterCls(l: string | undefined): string {
  return l === "A" ? "bg-accent text-card font-bold" : l === "R" ? "bg-blue-bg text-accent font-semibold" : l === "C" ? "bg-warn-bg text-warn" : l === "I" ? "bg-none-bg text-none" : "text-muted";
}

function Cell({ activity, fn, canEdit, onChange }: { activity: RaciActivity; fn: string; canEdit: boolean; onChange: (letter: string | null) => void }) {
  const l = activity.cells[fn];
  if (!canEdit) return <span className={`inline-block w-7 rounded-cc-sm py-0.5 text-center text-xs ${letterCls(l)}`}>{l ?? "·"}</span>;
  return (
    <select value={l ?? ""} onChange={(e) => onChange(e.target.value || null)} className={`w-10 rounded-cc-sm border border-line px-1 py-0.5 text-center text-xs ${letterCls(l)}`} title="R udfører · A ansvarlig · C høres · I informeres">
      {LETTERS.map((x) => <option key={x} value={x}>{x || "·"}</option>)}
    </select>
  );
}

export default function RaciSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);
  const edit = can("raci_godkend");

  const raci = useQuery({ queryKey: ["raci", contract.id], queryFn: () => api<Raci>(`/api/contracts/${contract.id}/raci`) });
  const tasks = useQuery({ queryKey: ["tasks", contract.id], queryFn: () => api<Task[]>(`/api/contracts/${contract.id}/tasks`) });
  const members = useQuery({ queryKey: ["members"], queryFn: () => api<Member[]>("/api/members") });
  const suggestions = useQuery({ queryKey: ["suggestions", contract.id], queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`) });
  const open_ = (suggestions.data ?? []).filter((s) => s.status === "foreslaaet");
  const raciProps = open_.filter((s): s is Suggestion<RaciPayload> => s.subject_kind === "raci_entry");
  const gapProps = open_.filter((s): s is Suggestion<TaskPayload> => s.subject_kind === "task");
  const refresh = () => { for (const k of ["raci", "tasks", "suggestions", "contract", "agent-runs"]) void qc.invalidateQueries({ queryKey: [k, contract.id] }); void qc.invalidateQueries({ queryKey: ["dashboard"] }); };
  const fail = (e: unknown) => setMsg(e instanceof ApiError ? e.message : "Handlingen mislykkedes.");

  const setCell = useMutation({
    mutationFn: ({ id, fn, letter }: { id: string; fn: string; letter: string | null }) => api<RaciActivity>(`/api/raci/activities/${id}/cells/${fn}`, { method: "PUT", body: JSON.stringify({ letter }) }),
    onSuccess: () => { setMsg(null); refresh(); }, onError: fail,
  });
  const assign = useMutation({
    mutationFn: ({ fn, profile_id, supplier_contact }: { fn: string; profile_id?: string | null; supplier_contact?: string | null }) => api(`/api/contracts/${contract.id}/roles/${fn}`, { method: "PUT", body: JSON.stringify({ profile_id: profile_id ?? null, supplier_contact: supplier_contact ?? null }) }),
    onSuccess: () => { setMsg(null); refresh(); }, onError: fail,
  });
  const create = useMutation({
    mutationFn: () => api<RaciActivity>(`/api/contracts/${contract.id}/raci/activities`, { method: "POST", body: JSON.stringify({ name: newName, criticality: "mellem", cells: { CM: "A", BUS: "R" } }) }),
    onSuccess: () => { setNewName(""); setCreating(false); refresh(); }, onError: fail,
  });
  const runAgent = useMutation({
    mutationFn: (key: string) => api(key === "raci_design" ? `/api/contracts/${contract.id}/agents/raci_design/run` : `/api/agents/${key}/run`, { method: "POST" }),
    onSuccess: refresh, onError: fail,
  });
  const patchTask = useMutation({
    mutationFn: ({ id, status }: { id: string; status: Task["status"] }) => api<Task>(`/api/tasks/${id}`, { method: "PATCH", body: JSON.stringify({ status }) }),
    onSuccess: refresh, onError: fail,
  });

  const fns = raci.data?.functions ?? [];
  const acts = raci.data?.activities ?? [];
  const roles = raci.data?.roles ?? [];
  const activeMembers = (members.data ?? []).filter((m) => !m.deactivated);
  const openTasks = (tasks.data ?? []).filter((t) => t.status !== "lukket");
  const pending = raciProps.length + gapProps.length;

  return (
    <section className="mb-6">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-semibold">
          Ansvar og governance
          {pending > 0 && <span className="ml-2 pill bg-blue-bg text-accent">{pending} kræver handling</span>}
        </h2>
        <div className="flex gap-2">
          {edit && !creating && <button onClick={() => setCreating(true)} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Ny aktivitet</button>}
          {can("agenter") && <button onClick={() => runAgent.mutate("raci_design")} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Kør RACI Design Agent</button>}
          {can("agenter") && <button onClick={() => runAgent.mutate("responsibility_gap")} className="rounded-cc-sm border border-line px-3 py-1 text-sm">Find ansvarshuller</button>}
        </div>
      </div>
      {msg && <p role="alert" className="mb-2 rounded-cc-sm bg-crit-bg px-3 py-1.5 text-sm text-crit">{msg}</p>}
      {creating && (
        <form onSubmit={(e) => { e.preventDefault(); create.mutate(); }} className="mb-3 flex gap-2 rounded-cc border border-line bg-card p-3">
          <input required value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Aktivitet, fx 'Beslutte forlængelse af rammeaftalen'" className="flex-1 rounded-cc-sm border border-line px-2 py-1.5 text-sm" />
          <button type="submit" className="rounded-cc-sm bg-accent px-3 py-1.5 text-sm font-semibold text-card">Opret (CM=A, BUS=R)</button>
          <button type="button" onClick={() => setCreating(false)} className="rounded-cc-sm border border-line px-3 py-1.5 text-sm">Annullér</button>
        </form>
      )}

      <div className="mb-3 overflow-x-auto rounded-cc border border-line bg-card">
        <table className="w-full min-w-[840px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
              <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">Aktivitet</th><th className="px-3 py-2">Krit.</th>
              {fns.map((f) => <th key={f.key} className="px-1 py-2 text-center" title={f.label}>{f.key}</th>)}
              <th className="px-3 py-2">Kilde</th>
            </tr>
            <tr className="border-b border-line bg-bg text-xs">
              <td className="px-3 py-1 text-muted" colSpan={3}>Bemanding (person pr. funktion)</td>
              {fns.map((f) => {
                const r = roles.find((x) => x.function === f.key);
                return (
                  <td key={f.key} className="px-1 py-1 text-center">
                    {edit && f.key !== "LEV" ? (
                      <select value={r?.profile_id ?? ""} onChange={(e) => assign.mutate({ fn: f.key, profile_id: e.target.value || null })} className={`w-full max-w-[7rem] rounded-cc-sm border px-1 py-0.5 text-xs ${r?.deactivated ? "border-crit text-crit" : r?.profile_id ? "border-line" : "border-warn text-warn"}`} title={r?.person_name ?? "ubesat"}>
                        <option value="">— ubesat</option>
                        {activeMembers.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                      </select>
                    ) : edit ? (
                      <input defaultValue={r?.supplier_contact ?? ""} onBlur={(e) => e.target.value !== (r?.supplier_contact ?? "") && assign.mutate({ fn: "LEV", supplier_contact: e.target.value || null })} placeholder="kontakt" className="w-full max-w-[7rem] rounded-cc-sm border border-line px-1 py-0.5 text-xs" />
                    ) : (
                      <span className={r?.deactivated ? "text-crit" : r?.person_name || r?.supplier_contact ? "" : "text-warn"} title={r?.deactivated ? "fratrådt" : ""}>{r?.person_name ?? r?.supplier_contact ?? "ubesat"}</span>
                    )}
                  </td>
                );
              })}
              <td />
            </tr>
          </thead>
          <tbody>
            {raciProps.map((p) => (
              <>
                <tr key={p.id} className="cursor-pointer border-b border-line bg-blue-bg/30 align-top" onClick={() => setOpen(open === p.id ? null : p.id)}>
                  <td className="px-3 py-2"><span className="pill bg-blue-bg text-accent">AI</span></td>
                  <td className="px-3 py-2 font-medium">{p.payload.name}{p.payload.template_key && <span className="ml-1 text-xs text-muted">· skabelon</span>}</td>
                  <td className="px-3 py-2">{CRIT[p.payload.criticality]}</td>
                  {fns.map((f) => <td key={f.key} className="px-1 py-2 text-center"><span className={`inline-block w-7 rounded-cc-sm py-0.5 text-xs ${letterCls(p.payload.cells[f.key])}`}>{p.payload.cells[f.key] ?? "·"}</span></td>)}
                  <td className="px-3 py-2"><ConfPill v={p.confidence} /> {p.citations.map((c, i) => <Chip key={i} c={c} />)}</td>
                </tr>
                {open === p.id && (
                  <tr key={`${p.id}-x`} className="border-b border-line bg-bg"><td colSpan={4 + fns.length} className="px-4 py-3 text-sm">
                    {p.payload.validation_errors.length > 0 && <p className="mb-1 text-xs text-crit">Ugyldig fordeling: {p.payload.validation_errors.join("; ")} — ret cellerne i godkendelsen eller afvis.</p>}
                    {p.citations[0]?.quote && <blockquote className="mb-1 border-l-2 border-line pl-3 text-slate">“{p.citations[0].quote}”</blockquote>}
                    <p className="text-xs text-muted">{p.rationale}</p>
                    {edit && can("hitl") ? <Verdict s={p} onDone={refresh} /> : <p className="mt-1 text-xs text-muted">Godkendelse kræver hitl og raci_godkend.</p>}
                  </td></tr>
                )}
              </>
            ))}
            {acts.map((a) => (
              <tr key={a.id} className={`border-b border-line align-top last:border-0 ${a.validation_errors.length ? "bg-crit-bg/30" : ""}`}>
                <td className="px-3 py-2 font-mono">{a.ref}{a.origin === "ai" && <span className="ml-1 text-xs text-muted">·AI</span>}</td>
                <td className="px-3 py-2 font-medium">{a.name}{a.validation_errors.length > 0 && <span className="block text-xs text-crit">{a.validation_errors.join("; ")}</span>}</td>
                <td className="px-3 py-2">{CRIT[a.criticality]}</td>
                {fns.map((f) => <td key={f.key} className="px-1 py-2 text-center"><Cell activity={a} fn={f.key} canEdit={edit} onChange={(letter) => setCell.mutate({ id: a.id, fn: f.key, letter })} /></td>)}
                <td className="px-3 py-2">{a.citations.map((c) => <Chip key={c.id} c={c} />)}</td>
              </tr>
            ))}
            {acts.length === 0 && raciProps.length === 0 && <tr><td colSpan={4 + fns.length} className="px-3 py-4 text-sm text-slate">Ingen aktiviteter endnu. RACI Design Agent foreslår dem fra skabeloner og klausuler, når aftalegrundlaget er uploadet.</td></tr>}
          </tbody>
        </table>
      </div>

      {(gapProps.length > 0 || openTasks.length > 0) && (
        <div className="rounded-cc border border-line bg-card p-3 text-sm">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Ansvarshuller og opgaver</p>
          {gapProps.map((g) => (
            <div key={g.id} className="border-t border-line py-2 first:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="pill bg-crit-bg text-crit">{g.payload.rule ?? "Fund"}</span>
                <span className="flex-1 font-medium">{g.payload.title}</span>
                <span className={`pill ${PRIO[g.payload.priority]?.[1] ?? ""}`}>{PRIO[g.payload.priority]?.[0] ?? g.payload.priority}</span>
              </div>
              <p className="mt-1 text-xs text-slate">{g.payload.description}</p>
              {edit && can("hitl") ? <Verdict s={g} onDone={refresh} /> : <p className="text-xs text-muted">Opgaven oprettes af en person med hitl og kontrakt_red.</p>}
            </div>
          ))}
          {openTasks.map((t) => (
            <div key={t.id} className="flex flex-wrap items-center gap-2 border-t border-line py-2 first:border-0">
              <span className="font-mono text-xs">{t.ref}</span>
              <span className="flex-1">{t.title}</span>
              {t.deadline && <span className="text-xs text-muted">{dkDay.format(new Date(t.deadline))}</span>}
              <span className={`pill ${PRIO[t.priority][1]}`}>{PRIO[t.priority][0]}</span>
              <span className="pill bg-none-bg text-none">{t.status === "igang" ? "I gang" : "Åben"}</span>
              {can("kontrakt_red") && <button onClick={() => patchTask.mutate({ id: t.id, status: "lukket" })} className="rounded-cc-sm border border-line px-2 py-0.5 text-xs">Luk</button>}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
