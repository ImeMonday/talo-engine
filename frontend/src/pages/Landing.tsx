import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errMsg, User } from "../api";
import { useAuth } from "../auth";
import { Err, Wordmark } from "../ui";

export default function Landing() {
  const { user, setUser } = useAuth();
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function demo() {
    setBusy(true);
    setErr(null);
    try {
      setUser(await api<User>("/api/auth/demo", { method: "POST" }));
      nav("/app");
    } catch (e) {
      setErr(errMsg(e));
      setBusy(false);
    }
  }

  return (
    <>
      <header className="bar">
        <Wordmark />
        <nav aria-label="Account">
          {user ? <Link to="/app">Open app</Link> : (<><Link to="/login">Log in</Link><Link to="/signup">Sign up</Link></>)}
        </nav>
      </header>
      <main className="wrap">
        <section className="hero">
          <p className="lbl">Model audits for African banks and fintechs</p>
          <h1>Audit your fraud models without sending customer data anywhere.</h1>
          <p className="mut">
            Talo detects drift in fraud, KYC and AML models with deterministic statistics, then uses Meta's Muse models
            to write an audit report in which every claim is cited to evidence and checked by a second agent.
          </p>
          <div className="actions">
            <button className="btn" onClick={demo} disabled={busy}>{busy ? "Opening demo…" : "Try the demo"}</button>
            <Link className="btn ghost" to="/signup">Create an account</Link>
          </div>
          <Err msg={err} />
        </section>

        <section aria-labelledby="how">
          <h2 id="how">How it works</h2>
          <div className="steps">
            <div className="card"><h3>1. Upload a decision log</h3><p className="mut">A model proposes how your columns map to a standard schema. Code validates it and you confirm.</p></div>
            <div className="card"><h3>2. Find drift with code</h3><p className="mut">PSI, KS tests and per-channel alert-rate tests produce numbered evidence. No model is involved.</p></div>
            <div className="card"><h3>3. Write and verify</h3><p className="mut">An agent investigates the evidence and drafts claims. A number checker and a separate verifier test each one.</p></div>
            <div className="card"><h3>4. Inspect what left</h3><p className="mut">Every outbound model call is recorded in the Egress Ledger, payload included.</p></div>
          </div>
        </section>

        <section aria-labelledby="trust" style={{ marginTop: 32 }}>
          <h2 id="trust">Built so that every claim can be checked</h2>
          <div className="steps">
            <div className="card"><h3>Numbers come from code</h3><p className="mut">A claim may only quote numbers that appear in the evidence it cites. A checker rejects anything else before a model judges it.</p></div>
            <div className="card"><h3>A second agent attacks the first</h3><p className="mut">The verifier looks for causes, impacts and losses the evidence does not establish, and a red-team button proves it.</p></div>
            <div className="card"><h3>Guesses stay labelled as guesses</h3><p className="mut">Possible causes appear as unverified hypotheses with a concrete next check, never mixed into verified findings.</p></div>
          </div>
        </section>

        <section className="card dark" style={{ marginTop: 32 }} aria-labelledby="privacy">
          <h2 id="privacy">Raw customer rows never reach a model.</h2>
          <p>
            Models receive column names, types, aggregates and evidence objects only. A guard blocks anything that
            looks like row data, and every request is recorded in the Egress Ledger so you can inspect exactly what was sent.
          </p>
        </section>

        <section className="card" style={{ marginTop: 20 }} aria-labelledby="limits">
          <h2 id="limits">Honest limits</h2>
          <p className="mut">
            Talo is a hackathon project tested on synthetic data. It is not a regulatory certification. The verifier
            reduces unsupported claims but cannot guarantee a model never makes one, which is why every claim shows its
            evidence and its verdict.
          </p>
        </section>
      </main>
      <footer>Built for the Meta Global AI Developer Hackathon.</footer>
    </>
  );
}
