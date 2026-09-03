import { useQuery } from "@tanstack/react-query";
import { Route, Routes } from "react-router-dom";

// Skeleton shell. No colour literal anywhere here — every colour is a token
// class (gate G-02). The health call goes to our own API, never to a provider
// (gate G-01).

type Health = { status: string; env: string };

async function fetchHealth(): Promise<Health> {
  const res = await fetch("/api/health");
  if (!res.ok) throw new Error(`health ${res.status}`);
  return (await res.json()) as Health;
}

function Shell() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="flex items-center justify-between border-b border-line bg-card px-6 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-bold tracking-tight text-navy">Obliance</span>
          <span className="font-mono text-xs uppercase tracking-wider text-muted">skelet</span>
        </div>
        <span
          className={
            "pill " +
            (health.isLoading
              ? "bg-none-bg text-none"
              : health.isError
                ? "bg-crit-bg text-crit"
                : "bg-ok-bg text-ok")
          }
        >
          {health.isLoading ? "API: data mangler" : health.isError ? "API: nede" : `API: ok · ${health.data?.env}`}
        </span>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-10">
        <h1 className="mb-3 text-2xl font-bold tracking-tight">Fundamentet er bygget og bevogtet</h1>
        <p className="max-w-prose text-slate">
          Tokens fra ADR-0015, RLS på to niveauer fra ADR-0002, gates fra ADR-0023. Første feature
          rører intet af det uden at CI siger til.
        </p>
        <div className="mt-8 grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="rounded-cc border border-line bg-card p-4 shadow-cc">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Status</div>
            <div className="flex gap-2">
              <span className="status-dot bg-ok-mark" />
              <span className="status-dot bg-warn-mark" />
              <span className="status-dot bg-crit-mark" />
              <span className="status-dot bg-none-mark" />
            </div>
          </div>
          <div className="rounded-cc border-l-4 border-ai bg-ai-bg p-4">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-ai">AI-forslag</div>
            <div className="text-sm text-ink">Violet betyder: ingen har godkendt endnu.</div>
          </div>
          <div className="rounded-cc border border-line bg-card p-4 shadow-cc">
            <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-muted">Beløb</div>
            <div className="font-mono tabular-nums text-ink">30.625,00 kr.</div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <Routes>
      <Route path="*" element={<Shell />} />
    </Routes>
  );
}
