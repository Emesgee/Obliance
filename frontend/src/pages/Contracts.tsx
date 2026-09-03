import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { FormEvent } from "react";
import { api, ApiError, type Contract, type ContractList } from "../api/client";
import { useAuth } from "../auth";

// First data screen: the list is whatever RLS lets this user see (ADR-0002);
// amounts are null when the role lacks `okonomi` (ADR-0003) and render as "—",
// never as 0. Create is only offered with `kontrakt_red`.

const dkk = new Intl.NumberFormat("da-DK", { style: "currency", currency: "DKK", minimumFractionDigits: 2 });

function Amount({ value }: { value: string | null }) {
  if (value === null) return <span className="text-muted">—</span>;
  return <span className="font-mono tabular-nums">{dkk.format(Number(value))}</span>;
}

function Pill({ kind, children }: { kind: "intern" | "fortrolig" | "phase"; children: string }) {
  const cls =
    kind === "fortrolig" ? "bg-warn-bg text-warn" : kind === "intern" ? "bg-none-bg text-none" : "bg-blue-bg text-accent";
  return <span className={`pill ${cls}`}>{children}</span>;
}

function CreateForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [reference, setReference] = useState("");
  const [name, setName] = useState("");
  const [confidentiality, setConfidentiality] = useState<"intern" | "fortrolig">("intern");
  const [error, setError] = useState<string | null>(null);

  const create = useMutation({
    mutationFn: (body: Partial<Contract>) =>
      api<Contract>("/api/contracts", { method: "POST", body: JSON.stringify(body) }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["contracts"] });
      onDone();
    },
    onError: (e) => setError(e instanceof ApiError ? e.message : "Kunne ikke oprette kontrakten."),
  });

  function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    create.mutate({ reference, name, confidentiality });
  }

  return (
    <form onSubmit={submit} className="mb-6 grid grid-cols-1 gap-3 rounded-cc border border-line bg-card p-4 sm:grid-cols-4">
      <div className="sm:col-span-1">
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="ref">Reference</label>
        <input id="ref" required placeholder="K-2026-001" value={reference} onChange={(e) => setReference(e.target.value.toUpperCase())}
          className="w-full rounded-cc-sm border border-line px-3 py-2 font-mono text-sm" />
      </div>
      <div className="sm:col-span-2">
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="name">Navn</label>
        <input id="name" required value={name} onChange={(e) => setName(e.target.value)}
          className="w-full rounded-cc-sm border border-line px-3 py-2 text-sm" />
      </div>
      <div>
        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="conf">Fortrolighed</label>
        <select id="conf" value={confidentiality} onChange={(e) => setConfidentiality(e.target.value as "intern" | "fortrolig")}
          className="w-full rounded-cc-sm border border-line px-3 py-2 text-sm">
          <option value="intern">Intern</option>
          <option value="fortrolig">Fortrolig</option>
        </select>
      </div>
      {error && <p role="alert" className="sm:col-span-4 rounded-cc-sm bg-crit-bg px-3 py-2 text-sm text-crit">{error}</p>}
      <div className="flex gap-2 sm:col-span-4">
        <button type="submit" disabled={create.isPending} className="rounded-cc-sm bg-accent px-4 py-2 text-sm font-semibold text-card disabled:opacity-60">
          {create.isPending ? "Opretter …" : "Opret kontrakt"}
        </button>
        <button type="button" onClick={onDone} className="rounded-cc-sm border border-line px-4 py-2 text-sm">Annullér</button>
      </div>
    </form>
  );
}

export default function Contracts() {
  const { can } = useAuth();
  const [creating, setCreating] = useState(false);
  const q = useQuery({ queryKey: ["contracts"], queryFn: () => api<ContractList>("/api/contracts") });

  return (
    <section>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">Kontrakter</h1>
        {can("kontrakt_red") && !creating && (
          <button onClick={() => setCreating(true)} className="rounded-cc-sm bg-accent px-4 py-2 text-sm font-semibold text-card">
            Ny kontrakt
          </button>
        )}
      </div>

      {creating && <CreateForm onDone={() => setCreating(false)} />}

      {q.isLoading && <p className="text-slate">Henter …</p>}
      {q.isError && <p role="alert" className="text-crit">Kunne ikke hente kontrakter.</p>}
      {q.data && q.data.items.length === 0 && (
        <p className="rounded-cc border border-line bg-card p-6 text-slate">
          Ingen kontrakter endnu{can("kontrakt_red") ? " — opret den første." : "."}
        </p>
      )}
      {q.data && q.data.items.length > 0 && (
        <div className="overflow-x-auto rounded-cc border border-line bg-card">
          <table className="w-full min-w-[640px] text-sm">
            <thead>
              <tr className="border-b border-line text-left text-xs uppercase tracking-wider text-muted">
                <th className="px-4 py-3">Reference</th>
                <th className="px-4 py-3">Navn</th>
                <th className="px-4 py-3">Fase</th>
                <th className="px-4 py-3">Fortrolighed</th>
                <th className="px-4 py-3 text-right">Årlig værdi</th>
              </tr>
            </thead>
            <tbody>
              {q.data.items.map((c) => (
                <tr key={c.id} className="border-b border-line last:border-0">
                  <td className="px-4 py-3 font-mono">{c.reference}</td>
                  <td className="px-4 py-3">{c.name}</td>
                  <td className="px-4 py-3"><Pill kind="phase">{c.phase}</Pill></td>
                  <td className="px-4 py-3"><Pill kind={c.confidentiality}>{c.confidentiality}</Pill></td>
                  <td className="px-4 py-3 text-right"><Amount value={c.annual_value} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
