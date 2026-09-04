import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import ContractDetail from "./pages/ContractDetail";
import Contracts from "./pages/Contracts";
import Dashboard from "./pages/Dashboard";
import Economy from "./pages/Economy";
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
        <div className="flex items-baseline gap-5">
          <span className="text-lg font-bold tracking-tight text-navy">Obliance</span>
          <nav className="flex gap-4 text-sm">
            {[["/", "Overblik"], ["/contracts", "Kontrakter"], ["/economy", "Økonomi"]].map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => (isActive ? "font-semibold text-accent" : "text-slate hover:text-ink")}>
                {label}
              </NavLink>
            ))}
          </nav>
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
            <Route index element={<Dashboard />} />
            <Route path="/contracts" element={<Contracts />} />
            <Route path="/contracts/:id" element={<ContractDetail />} />
            <Route path="/economy" element={<Economy />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Route>
      </Routes>
    </AuthProvider>
  );
}
