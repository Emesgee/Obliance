import { Navigate, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import Contracts from "./pages/Contracts";
import Login from "./pages/Login";

// Shell (ADR-0024 increment 1). No colour literal anywhere — every colour is a
// token class (gate G-02). All calls go to our own /api (gate G-01).

function RequireAuth() {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <p className="p-6 text-slate">Henter session …</p>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}

function Shell() {
  const { me, logout } = useAuth();
  return (
    <div className="min-h-screen bg-bg text-ink">
      <header className="flex items-center justify-between border-b border-line bg-card px-6 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-lg font-bold tracking-tight text-navy">Obliance</span>
          <span className="font-mono text-xs uppercase tracking-wider text-muted">{me?.org_name}</span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span>
            {me?.name} <span className="text-muted">· {me?.role}</span>
          </span>
          <button onClick={logout} className="rounded-cc-sm border border-line px-3 py-1 text-sm">
            Log af
          </button>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">
        <Outlet />
      </main>
    </div>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<RequireAuth />}>
          <Route element={<Shell />}>
            <Route index element={<Contracts />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
