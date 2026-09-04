import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type Claim, type Contract } from "../../api/client";
import { useAuth } from "../../auth";
import { Chip, dkDay } from "./shared";

// Krav (ADR-0013 §3/§4): computed by code from approved parameters, with its
// basis shown as one line; approved by okonomi (two signatures over 250.000 kr.,
// the second a Contract Owner); "fremsat" is a separate click — the system never
// sends anything. Amounts are absent without okonomi (ADR-0003).

const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });
const TYPE: Record<string, string> = { service_credit: "Service credit", bod: "Bod", prisafvigelse: "Prisafvigelse" };
const STATUS: Record<Claim["status"], [string, string]> = {
  beregnet: ["Beregnet", "bg-blue-bg text-accent"],
  afventer_2_signatur: ["Afventer 2. signatur", "bg-warn-bg text-warn"],
  godkendt: ["Godkendt", "bg-ok-bg text-ok"],
  fremsat: ["Fremsat", "bg-ok-bg text-ok"],
  modregnet: ["Modregnet", "bg-none-bg text-none"],
  betalt: ["Betalt", "bg-none-bg text-none"],
  afvist_af_leverandoer: ["Afvist af leverandør", "bg-crit-bg text-crit"],
  frafaldet: ["Frafaldet", "bg-none-bg text-none"],
};

export default function ClaimsSection({ contract }: { contract: Contract }) {
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const claims = useQuery({ queryKey: ["claims", contract.id], queryFn: () => api<Claim[]>(`/api/contracts/${contract.id}/claims`) });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["claims", contract.id] });
  const act = useMutation({
    mutationFn: ({ id, action, status }: { id: string; action: "approve" | "submit" | "settle" | "recompute"; status?: Claim["status"] }) =>
      api<Claim | { matches_stored: boolean; amount: string }>(`/api/claims/${id}/${action}`, {
        method: "POST",
        body: JSON.stringify(action === "settle" ? { status, comment: comment || null } : { comment: comment || null }),
      }),
    onSuccess: (r) => {
      if ("matches_stored" in r) setMsg(r.matches_stored ? `Genberegning giver samme beløb: ${dkk.format(Number(r.amount))}.` : `Genberegning afviger: ${dkk.format(Number(r.amount))}.`);
      else setMsg(null);
      setComment("");
      refresh();
    },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Handlingen mislykkedes."),
  });
  const money = can("okonomi");
  const rows = claims.data ?? [];
  const btn = "rounded-cc-sm border border-line px-3 py-1 text-xs";
  const primary = "rounded-cc-sm bg-accent px-3 py-1 text-xs font-semibold text-card";

  if (rows.length === 0) return null;
  return (
    <section className="mb-6">
      <h2 className="mb-2 text-lg font-semibold">Krav</h2>
      {msg && <p className="mb-2 text-sm text-slate">{msg}</p>}
      <div className="overflow-x-auto rounded-cc border border-line bg-card">
        <table className="w-full min-w-[720px] text-sm">
          <thead>
            <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
              <th className="px-3 py-2">Ref.</th><th className="px-3 py-2">Type</th><th className="px-3 py-2">Periode</th><th className="px-3 py-2 text-right">Beløb</th><th className="px-3 py-2">Status</th><th className="px-3 py-2">Kilde</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((c) => {
              const [label, cls] = STATUS[c.status];
              const isOpen = open === c.id;
              const canSecond = c.status === "afventer_2_signatur" && me?.role === "contract_owner" && c.approved_by !== me?.user_id;
              return (
                <>
                  <tr key={c.id} className="cursor-pointer border-b border-line align-top last:border-0" onClick={() => setOpen(isOpen ? null : c.id)}>
                    <td className="px-3 py-2 font-mono">{c.ref}</td>
                    <td className="px-3 py-2">{TYPE[c.claim_type]}</td>
                    <td className="px-3 py-2 text-muted">{c.period_start ? `${dkDay.format(new Date(c.period_start))} – ${dkDay.format(new Date(c.period_end ?? c.period_start))}` : "—"}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">{c.amount === null ? <span className="text-muted">—</span> : dkk.format(Number(c.amount))}{c.cap_applied && <span className="ml-1 text-xs text-warn" title="Loft anvendt">loft</span>}</td>
                    <td className="px-3 py-2"><span className={`pill ${cls}`}>{label}</span>{c.requires_second_signature && c.status === "beregnet" && <span className="ml-1 text-xs text-muted">2 signaturer</span>}</td>
                    <td className="px-3 py-2">{c.citations.map((x) => <Chip key={x.id} c={x} />)}</td>
                  </tr>
                  {isOpen && (
                    <tr key={`${c.id}-x`} className="border-b border-line bg-bg last:border-0"><td colSpan={6} className="px-4 py-3 text-sm">
                      {c.basis_text ? <p className="mb-1"><span className="font-semibold">Beregningsgrundlag:</span> {c.basis_text}</p> : <p className="mb-1 text-muted">Beregningsgrundlaget kræver tilladelsen okonomi.</p>}
                      <p className="mb-2 text-xs text-muted">Formelversion {c.formula_version}{c.decision_comment ? ` · ${c.decision_comment}` : ""}</p>
                      {money && (
                        <div className="flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
                          <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Kommentar / begrundelse" className="min-w-[14rem] flex-1 rounded-cc-sm border border-line px-2 py-1 text-xs" />
                          {c.status === "beregnet" && <button onClick={() => act.mutate({ id: c.id, action: "approve" })} className={primary}>Godkend{c.requires_second_signature ? " (1. signatur)" : ""}</button>}
                          {canSecond && <button onClick={() => act.mutate({ id: c.id, action: "approve" })} className={primary}>Godkend (2. signatur)</button>}
                          {c.status === "godkendt" && <button onClick={() => act.mutate({ id: c.id, action: "submit" })} className={primary}>Markér fremsat</button>}
                          {c.status === "fremsat" && (
                            <>
                              <button onClick={() => act.mutate({ id: c.id, action: "settle", status: "modregnet" })} className={btn}>Modregnet</button>
                              <button onClick={() => act.mutate({ id: c.id, action: "settle", status: "betalt" })} className={btn}>Betalt</button>
                              <button onClick={() => act.mutate({ id: c.id, action: "settle", status: "afvist_af_leverandoer" })} className={btn}>Afvist af leverandør</button>
                            </>
                          )}
                          {!["modregnet", "betalt", "frafaldet"].includes(c.status) && <button onClick={() => act.mutate({ id: c.id, action: "settle", status: "frafaldet" })} disabled={comment.trim().length < 3} title="Kræver begrundelse" className={`${btn} disabled:opacity-50`}>Frafald</button>}
                          <button onClick={() => act.mutate({ id: c.id, action: "recompute" })} className={btn}>Genberegn</button>
                        </div>
                      )}
                    </td></tr>
                  )}
                </>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
