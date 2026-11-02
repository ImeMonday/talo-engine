import { FormEvent, useState } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { api, errMsg, User } from "../api";
import { useAuth } from "../auth";
import { Err, Wordmark } from "../ui";

export default function AuthPage({ mode }: { mode: "login" | "signup" }) {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [org, setOrg] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/app" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const body = mode === "login" ? { email, password } : { email, password, org_name: org };
      setUser(await api<User>(`/api/auth/${mode}`, { method: "POST", body: JSON.stringify(body) }));
      nav("/app");
    } catch (e2) {
      setErr(errMsg(e2));
      setBusy(false);
    }
  }

  return (
    <main className="narrow">
      <Link to="/" style={{ textDecoration: "none" }}><Wordmark /></Link>
      <form className="card" onSubmit={submit} style={{ marginTop: 16 }}>
        <h1 style={{ fontSize: 30 }}>{mode === "login" ? "Log in" : "Create your account"}</h1>
        {mode === "signup" && (
          <>
            <label htmlFor="org">Organisation</label>
            <input id="org" value={org} onChange={(e) => setOrg(e.target.value)} required minLength={2} autoComplete="organization" />
          </>
        )}
        <label htmlFor="email">Email</label>
        <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required autoComplete="email" />
        <label htmlFor="pw">Password{mode === "signup" && " (at least 10 characters)"}</label>
        <input id="pw" type="password" value={password} onChange={(e) => setPassword(e.target.value)} required
          minLength={mode === "signup" ? 10 : 1} autoComplete={mode === "login" ? "current-password" : "new-password"} />
        <Err msg={err} />
        <button className="btn" style={{ width: "100%", marginTop: 16 }} disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Log in" : "Create account"}
        </button>
        <p className="mut" style={{ marginBottom: 0 }}>
          {mode === "login" ? <>New here? <Link to="/signup">Create an account</Link></> : <>Already registered? <Link to="/login">Log in</Link></>}
        </p>
      </form>
    </main>
  );
}
