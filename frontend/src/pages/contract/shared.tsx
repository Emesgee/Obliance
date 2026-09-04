import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, type Citation, type CitationRow, type Suggestion } from "../../api/client";

// Pieces shared by the register sections (Forpligtelser, Risici): the AI
// proposal's confidence, the citation chip (ADR-0005 §4) and the human verdict
// (ADR-0004 §2 — reject needs a reason).

export const dkDay = new Intl.DateTimeFormat("da-DK", { dateStyle: "medium" });

export function ConfPill({ v }: { v: Suggestion["confidence"] }) {
  const cls = v === "hoej" ? "bg-ok-bg text-ok" : v === "mellem" ? "bg-warn-bg text-warn" : "bg-crit-bg text-crit";
  return <span className={`pill ${cls}`}>Sikkerhed: {v === "hoej" ? "Høj" : v === "mellem" ? "Mellem" : "Lav"}</span>;
}

export function Chip({ c }: { c: Citation | CitationRow }) {
  const stale = "successor_status" in c && c.successor_status === "ikke_fundet";
  const warn = !c.verified || stale;
  return (
    <span title={c.quote ?? ""} className={`mr-1 inline-block rounded-cc-sm border px-2 py-0.5 text-xs ${warn ? "border-warn bg-warn-bg text-warn" : "border-line bg-bg"}`}>
      {c.label}{!c.verified && " · citat ikke fundet"}{stale && " · kilde forældet"}
    </span>
  );
}

export function Verdict({ s, onDone }: { s: Suggestion<unknown>; onDone: () => void }) {
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
    <div className="mt-2 flex flex-wrap items-start gap-2" onClick={(e) => e.stopPropagation()}>
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
