import { BrowserRouter, Link, Navigate, NavLink, Outlet, Route, Routes, useNavigate } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth";
import { api } from "./api";
import { Chip, Wordmark } from "./ui";
import Landing from "./pages/Landing";
import AuthPage from "./pages/Auth";
import Dashboard from "./pages/Dashboard";
import NewAudit from "./pages/NewAudit";
import AuditPage from "./pages/AuditPage";
import Ledger from "./pages/Ledger";
import Evals from "./pages/Evals";
import Settings from "./pages/Settings";

const active = ({ isActive }: { isActive: boolean }) => (isActive ? "active" : "");

function Shell() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  async function logout() {
    await api("/api/auth/logout", { method: "POST" }).catch(() => undefined);
    setUser(null);
    nav("/");
  }
  return (
    <>
      <header className="bar">
        <nav aria-label="Main">
          <Link to="/app" style={{ textDecoration: "none", marginRight: 10 }}><Wordmark /></Link>
          <NavLink to="/app" end className={active}>Audits</NavLink>
          <NavLink to="/app/egress" className={active}>Egress Ledger</NavLink>
          <NavLink to="/app/evals" className={active}>Evals</NavLink>
          <NavLink to="/app/settings" className={active}>Settings</NavLink>
        </nav>
        <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          {(user?.is_demo || user?.sandbox) && <Chip kind="mid">Sandbox, sample data</Chip>}
          <span className="mut">{user?.email}</span>
          <button className="btn ghost sm" onClick={logout}>Sign out</button>
        </div>
      </header>
      <Outlet />
    </>
  );
}

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <p className="center mut">Loading…</p>;
  return user ? <Shell /> : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/login" element={<AuthPage mode="login" />} />
          <Route path="/signup" element={<AuthPage mode="signup" />} />
          <Route path="/app" element={<Protected />}>
            <Route index element={<Dashboard />} />
            <Route path="new" element={<NewAudit />} />
            <Route path="audits/:id" element={<AuditPage />} />
            <Route path="egress" element={<Ledger />} />
            <Route path="evals" element={<Evals />} />
            <Route path="settings" element={<Settings />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
