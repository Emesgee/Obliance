import { useState } from "react";
import type { FormEvent } from "react";
import { Navigate, useLocation, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { useAuth } from "../auth";

export default function Login() {
  const { me, login } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to="/" replace />;

  const from = (location.state as { from?: string } | null)?.from ?? "/";

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Kunne ikke logge ind. Prøv igen.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-bg px-6">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-sm rounded-cc border border-line bg-card p-8 shadow-cc"
        aria-labelledby="login-heading"
      >
        <div className="mb-6">
          <div className="text-xl font-bold tracking-tight text-navy">Obliance</div>
          <h1 id="login-heading" className="mt-1 text-sm text-slate">
            Log ind med din organisations konto
          </h1>
        </div>

        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="email">
          E-mail
        </label>
        <input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className="mb-4 w-full rounded-cc-sm border border-line bg-card px-3 py-2 text-ink"
        />

        <label className="mb-1 block text-xs font-semibold uppercase tracking-wider text-muted" htmlFor="password">
          Adgangskode
        </label>
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          className="mb-5 w-full rounded-cc-sm border border-line bg-card px-3 py-2 text-ink"
        />

        {error && (
          <p role="alert" className="mb-4 rounded-cc-sm bg-crit-bg px-3 py-2 text-sm text-crit">
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={busy}
          className="w-full rounded-cc-sm bg-accent px-4 py-2 font-semibold text-card disabled:opacity-60"
        >
          {busy ? "Logger ind …" : "Log ind"}
        </button>

        <p className="mt-5 text-xs text-muted">
          Ingen konto? Adgang gives af din organisations administrator — der er ingen selvoprettelse.
        </p>
      </form>
    </main>
  );
}
