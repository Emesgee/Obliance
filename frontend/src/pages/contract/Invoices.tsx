import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type Contract, type Invoice, type InvoiceFindingPayload, type PriceTerm, type PriceTermPayload, type Suggestion } from "../../api/client";
import { useAuth } from "../../auth";
import { ControlPill } from "../Economy";
import { Chip, ConfPill, Verdict, dkDay } from "./shared";

// Fakturaer on the contract (ADR-0018 §6): what the control found, decided by
// okonomi. A finding is a proposal whose approval creates a prisafvigelse-claim
// computed in code (ADR-0013). Agreed prices (prisbilag) are shown as the basis.

const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });
const dkNum = new Intl.NumberFormat("da-DK", { maximumFractionDigits: 2 });

export default function InvoicesSection({ contract }: { contract: Contract }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const money = can("okonomi");
  const canSee = money || can("kontrakt_red");

  const invoices = useQuery({ queryKey: ["invoices", contract.id], queryFn: () => api<Invoice[]>(`/api/contracts/${contract.id}/invoices`), enabled: canSee });
  const prices = useQuery({ queryKey: ["price-terms", contract.id], queryFn: () => api<PriceTerm[]>(`/api/contracts/${contract.id}/price-terms`) });
  const suggestions = useQuery({ queryKey: ["suggestions", contract.id], queryFn: () => api<Suggestion[]>(`/api/contracts/${contract.id}/suggestions`) });
  const open_ = (suggestions.data ?? []).filter((s) => s.status === "foreslaaet");
  const findings = open_.filter((s): s is Suggestion<InvoiceFindingPayload> => s.subject_kind === "invoice_finding");
  const priceProps = open_.filter((s): s is Suggestion<PriceTermPayload> => s.subject_kind === "price_term");
  const refresh = () => { for (const k of ["invoices", "price-terms", "suggestions", "claims", "dashboard"]) void qc.invalidateQueries({ queryKey: [k] }); };
  const decide = useMutation({
    mutationFn: ({ id, action }: { id: string; action: "approve" | "reject" }) => api<Invoice>(`/api/invoices/${id}/${action}`, { method: "POST", body: JSON.stringify({ comment: comment || null }) }),
    onSuccess: () => { setComment(""); setMsg(null); refresh(); },
    onError: (e) => setMsg(e instanceof ApiError ? e.message : "Handlingen mislykkedes."),
  });

  if (!canSee) return null;
  const rows = invoices.data ?? [];
  const priceRows = prices.data ?? [];
  if (rows.length === 0 && findings.length === 0 && priceRows.length === 0 && priceProps.length === 0) return null;

  return (
    <section className="mb-6">
      <h2 className="mb-2 text-lg font-semibold">
        Økonomi
        {findings.length + priceProps.length > 0 && <span className="ml-2 pill bg-blue-bg text-accent">{findings.length + priceProps.length} kræver handling</span>}
      </h2>
      {msg && <p className="mb-2 text-sm text-crit">{msg}</p>}

      {(priceRows.length > 0 || priceProps.length > 0) && (
        <div className="mb-3 rounded-cc border border-line bg-card p-3 text-sm">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Aftalte priser</p>
          <ul>
            {priceProps.map((p) => (
              <li key={p.id} className="border-t border-line py-1 first:border-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="pill bg-blue-bg text-accent">AI</span>
                  <span className="flex-1">{p.payload.description}{p.payload.product_ref ? ` (${p.payload.product_ref})` : ""}{p.payload.unit ? ` · pr. ${p.payload.unit}` : ""}</span>
                  <span className="font-mono">{dkk.format(Number(p.payload.agreed_unit_price))}</span>
                  <ConfPill v={p.confidence} />
                  {p.citations.map((c, i) => <Chip key={i} c={c} />)}
                </div>
                {money && can("hitl") ? <Verdict s={p} onDone={refresh} /> : <p className="text-xs text-muted">Godkendelse kræver hitl og okonomi.</p>}
              </li>
            ))}
            {priceRows.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-2 border-t border-line py-1 first:border-0">
                <span className="flex-1">{t.description}{t.product_ref ? ` (${t.product_ref})` : ""}{t.unit ? ` · pr. ${t.unit}` : ""}</span>
                <span className="font-mono">{dkk.format(Number(t.agreed_unit_price))}</span>
                {t.citations.map((c) => <Chip key={c.id} c={c} />)}
              </li>
            ))}
          </ul>
        </div>
      )}

      {findings.length > 0 && (
        <div className="mb-3 rounded-cc border border-accent bg-card p-3 text-sm">
          <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Afvigelser fundet</p>
          {findings.map((f) => (
            <div key={f.id} className="border-t border-line py-2 first:border-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="pill bg-crit-bg text-crit">Afvigelse</span>
                <span className="font-mono">Faktura {f.payload.invoice_number} · linje {f.payload.line_no}</span>
                <span className="flex-1">{f.payload.description}: {dkNum.format(Number(f.payload.quantity))} × {dkk.format(Number(f.payload.invoiced_unit_price))} mod aftalt {dkk.format(Number(f.payload.agreed_unit_price))}</span>
                <span className="font-mono font-semibold">{dkk.format(Number(f.payload.amount))}</span>
              </div>
              <p className="mt-1 text-xs text-slate">{f.payload.basis_text} · Anbefaling: {f.payload.recommendation}. Godkendelse opretter et kreditnotakrav.</p>
              {money && can("hitl") ? <Verdict s={f} onDone={refresh} /> : <p className="text-xs text-muted">Afgørelse kræver hitl og okonomi.</p>}
            </div>
          ))}
        </div>
      )}

      {rows.length > 0 && (
        <div className="overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[720px] text-sm">
            <thead><tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted"><th className="px-3 py-2">Faktura</th><th className="px-3 py-2">Dato</th><th className="px-3 py-2">Leverandør</th><th className="px-3 py-2 text-right">Beløb</th><th className="px-3 py-2">Match</th><th className="px-3 py-2">Status</th></tr></thead>
            <tbody>
              {rows.map((inv) => (
                <>
                  <tr key={inv.id} className="cursor-pointer border-b border-line align-top last:border-0" onClick={() => setOpen(open === inv.id ? null : inv.id)}>
                    <td className="px-3 py-2 font-mono">{inv.invoice_number}</td>
                    <td className="px-3 py-2 text-muted">{dkDay.format(new Date(inv.invoice_date))}</td>
                    <td className="px-3 py-2">{inv.supplier_name}</td>
                    <td className="px-3 py-2 text-right font-mono tabular-nums">{dkk.format(Number(inv.total_amount))}</td>
                    <td className="px-3 py-2 text-xs text-muted">{inv.matched_by ?? "—"}</td>
                    <td className="px-3 py-2"><ControlPill inv={inv} /></td>
                  </tr>
                  {open === inv.id && (
                    <tr key={`${inv.id}-x`} className="border-b border-line bg-bg last:border-0"><td colSpan={6} className="px-4 py-3 text-sm">
                      <table className="mb-2 w-full text-xs">
                        <thead><tr className="text-left text-muted"><th className="py-1 pr-2">Linje</th><th className="py-1 pr-2">Beskrivelse</th><th className="py-1 pr-2 text-right">Antal</th><th className="py-1 pr-2 text-right">Enhedspris</th><th className="py-1 text-right">Total</th></tr></thead>
                        <tbody>{inv.lines.map((l) => <tr key={l.id}><td className="py-0.5 pr-2 font-mono">{l.line_no}</td><td className="py-0.5 pr-2">{l.description}{l.product_ref ? ` (${l.product_ref})` : ""}</td><td className="py-0.5 pr-2 text-right font-mono">{dkNum.format(Number(l.quantity))} {l.unit ?? ""}</td><td className="py-0.5 pr-2 text-right font-mono">{dkk.format(Number(l.unit_price))}</td><td className="py-0.5 text-right font-mono">{dkk.format(Number(l.line_total))}</td></tr>)}</tbody>
                      </table>
                      {inv.control_note && <p className="mb-1 text-xs text-slate">{inv.control_note}</p>}
                      {inv.decision_comment && <p className="mb-1 text-xs text-muted">{inv.decision_comment}</p>}
                      {money && (inv.status === "kontrolleret" || inv.status === "matchet") && (
                        <div className="flex flex-wrap items-center gap-2" onClick={(e) => e.stopPropagation()}>
                          <input value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Kommentar (påkrævet ved afvisning)" className="min-w-[14rem] flex-1 rounded-cc-sm border border-line px-2 py-1 text-xs" />
                          <button onClick={() => decide.mutate({ id: inv.id, action: "approve" })} className="rounded-cc-sm bg-accent px-3 py-1 text-xs font-semibold text-card">Godkend faktura</button>
                          <button onClick={() => decide.mutate({ id: inv.id, action: "reject" })} disabled={comment.trim().length < 3} className="rounded-cc-sm border border-line px-3 py-1 text-xs disabled:opacity-50">Afvis</button>
                        </div>
                      )}
                    </td></tr>
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
