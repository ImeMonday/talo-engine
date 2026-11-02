import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, AuditSummary, errMsg } from "../api";
import { useAuth } from "../auth";
import { Chip, Err, Skeleton } from "../ui";

export default function Dashboard() {
  const { user } = useAuth();
  const [audits, setAudits] = useState<AuditSummary[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<AuditSummary[]>("/api/audits").then(setAudits).catch((e) => setErr(errMsg(e)));
  }, []);

  return (
    <main className="wrap">
      <div className="row first between">
        <div>
          <h1>{user?.org.name}</h1>
          <p className="mut" style={{ margin: "4px 0 0" }}>Audits of your fraud, KYC and AML models</p>
        </div>
        {!user?.is_demo && <Link className="btn" to="/app/new">New audit</Link>}
      </div>
      <Err msg={err} />
      {audits === null && !err && <><Skeleton /><Skeleton /><Skeleton /></>}
      {audits?.length === 0 && (
        <div className="card center">
          <h2>No audits yet</h2>
          <p className="mut">Upload a decision log to run your first audit.</p>
          <Link className="btn" to="/app/new">New audit</Link>
        </div>
      )}
      {audits && audits.length > 0 && (
        <div className="card">
          {audits.map((a, i) => (
            <div key={a.id} className={`row ${i === 0 ? "first" : ""}`}>
              <Link className="grow serif" style={{ fontSize: 20 }} to={`/app/audits/${a.id}`}>{a.name}</Link>
              <span className="mut">{a.n_rows.toLocaleString()} rows</span>
              <span className="mut">{new Date(a.created_at).toLocaleDateString()}</span>
              <Chip kind={a.n_findings ? "bad" : "ok"}>{a.n_findings} findings</Chip>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
