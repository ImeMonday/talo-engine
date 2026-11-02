import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, ApiError, Audit, ClaimResult, Evidence, errMsg, Report } from "../api";
import { useAuth } from "../auth";
import { Chip, Err, pct, sevKind, Skeleton, Stat, Timeline, Verdict, Wordmark } from "../ui";

function ClaimRow({ c, onEvidence }: { c: ClaimResult; onEvidence: (id: string) => void }) {
  return (
    <div className="row" style={{ display: "block" }}>
      {c.original_text && <p className="struck serif" style={{ margin: "0 0 6px" }}>{c.original_text}</p>}
      <p className="serif" style={{ fontSize: 20, margin: "0 0 8px" }}>{c.text}</p>
      <div className="row first" style={{ padding: 0 }}>
        <Verdict v={c.verdict} />
        {c.rewritten && <Chip kind="mid">Rewritten once</Chip>}
        {c.injected && <Chip kind="mid">Red team</Chip>}
        {c.evidence_ids.map((id) => (
          <button key={id} className="btn ghost sm mono" onClick={() => onEvidence(id)}>{id}</button>
        ))}
      </div>
      {c.reason && <p className="mut" style={{ margin: "6px 0 0" }}>{c.reason}</p>}
    </div>
  );
}

const stepLabel = (s: { step: string; action: string; id: string | null }) =>
  s.step === "tool" ? `Investigator called ${s.action}${s.id ? ` on ${s.id}` : ""}` : "Verifier checked every claim";
const cents = (usd: number) => `${(usd * 100).toFixed(1)} cents`;

export default function AuditPage() {
  const { id } = useParams<{ id: string }>();
  const { user } = useAuth();
  const [audit, setAudit] = useState<Audit | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [sent, setSent] = useState<number | null>(null);
  const [calls, setCalls] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "report" | "redteam">("");
  const [ev, setEv] = useState<Evidence | null>(null);

  const refreshLedger = useCallback(
    () => api<{ raw_rows_sent: number; calls: number }>("/api/egress")
      .then((d) => { setSent(d.raw_rows_sent); setCalls(d.calls); }).catch(() => undefined),
    [],
  );

  const load = useCallback(async () => {
    try {
      setAudit(await api<Audit>(`/api/audits/${id}`));
    } catch (e) {
      setErr(errMsg(e));
      return;
    }
    try {
      setReport(await api<Report>(`/api/audits/${id}/report`));
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) setErr(errMsg(e));
    }
    void refreshLedger();
  }, [id, refreshLedger]);
  useEffect(() => { void load(); }, [load]);

  async function run(kind: "report" | "redteam") {
    setBusy(kind);
    setErr(null);
    const poll = window.setInterval(() => void refreshLedger(), 2000);
    try {
      await api(`/api/audits/${id}/report${kind === "redteam" ? "/redteam" : ""}`, { method: "POST" });
      await load();
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      window.clearInterval(poll);
      setBusy("");
    }
  }

  if (!audit) return <main className="wrap">{err ? <Err msg={err} /> : <><Skeleton /><Skeleton /><Skeleton /></>}</main>;
  const high = audit.findings.filter((f) => f.severity === "high").length;
  const showEvidence = (evId: string) => setEv((audit.evidence ?? audit.findings).find((f) => f.id === evId) ?? null);
  const stats = report?.report.stats;
  const usage = report?.report.usage;
  const hyps = report?.report.hypotheses ?? [];
  const segments = audit.segments ?? [];

  return (
    <main className="wrap">
      <div className="printonly" style={{ marginBottom: 16 }}>
        <Wordmark />
        <p>Model audit report{report ? `, generated ${new Date(report.created_at).toLocaleString()}` : ""}</p>
        {report?.report.fingerprint && <p className="mono" style={{ fontSize: 12 }}>Integrity fingerprint (SHA-256): {report.report.fingerprint}</p>}
      </div>
      <p className="noprint"><Link to="/app">← All audits</Link></p>
      <div className="row first between">
        <div>
          <h1>{audit.name}</h1>
          <p className="mut" style={{ margin: "4px 0 0" }}>{audit.n_rows.toLocaleString()} rows · baseline ends {audit.cutoff}</p>
        </div>
        <button className="btn ghost noprint" onClick={() => window.print()}>Download report (PDF)</button>
      </div>
      <Err msg={err} />

      <div className="grid" style={{ marginTop: 16 }}>
        <Stat label="Drift findings" value={audit.findings.length} sub={`${high} high severity`} />
        <Stat label="Claims verified" value={stats ? `${stats.supported + stats.partial} of ${stats.claims}` : "n/a"}
          sub={stats ? `${stats.insufficient} insufficient evidence` : "Generate a report"} />
        <Stat dark label="Raw rows sent to a model" value={sent ?? 0} sub={`${calls} model calls recorded. Schema and aggregates only.`} />
      </div>

      <div className="card">
        <h2>Drift timeline</h2>
        <Timeline series={audit.timeline} cutoff={audit.cutoff} field="amount_psi" title="Transaction amount drift (PSI vs baseline)" fmt={(n) => n.toFixed(2)} />
        <Timeline series={audit.timeline} cutoff={audit.cutoff} field="alert_rate" title="Share of transactions flagged" fmt={(n) => `${(n * 100).toFixed(1)}%`} />
      </div>

      <div className="card">
        <h2>Findings</h2>
        {audit.findings.length === 0 && <p className="mut">No finding crossed a detection threshold.</p>}
        {audit.findings.map((f, i) => (
          <div key={f.id} className={`row ${i === 0 ? "first" : ""}`}>
            <button className="btn ghost sm mono" onClick={() => setEv(f)}>{f.id}</button>
            <span className="grow" style={{ fontWeight: 500 }}>{f.kind.replace(/_/g, " ")} · {f.segment}</span>
            <span className="mono mut">{f.metric} {f.value}</span>
            <Chip kind={sevKind(f.severity)}>{f.severity}</Chip>
          </div>
        ))}
      </div>

      {segments.length > 0 && (
        <div className="card scroll">
          <h2>Where the drift sits</h2>
          <p className="mut">Each channel, current window against baseline. Shaded rows crossed a detection threshold.</p>
          <table>
            <thead><tr><th>Channel</th><th>Rows (base → now)</th><th>Flagged rate</th><th>Median amount</th><th>Amount PSI</th><th>Score KS</th></tr></thead>
            <tbody>
              {segments.map((s) => {
                const hit = audit.findings.some((f) => f.segment === s.segment);
                return (
                  <tr key={s.segment} style={hit ? { background: "#F8DCD2" } : undefined}>
                    <td className="mono">{s.segment.replace("channel=", "")}</td>
                    <td className="mono">{s.n_baseline.toLocaleString()} → {s.n_current.toLocaleString()}</td>
                    <td className="mono">{(s.alert_rate_baseline * 100).toFixed(1)}% → {(s.alert_rate_current * 100).toFixed(1)}%</td>
                    <td className="mono">{s.median_amount_ratio === null ? "n/a" : `×${s.median_amount_ratio.toFixed(2)}`}</td>
                    <td className="mono">{s.amount_psi.toFixed(3)}</td>
                    <td className="mono">{s.score_ks.toFixed(3)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {ev && (
        <div className="card" aria-live="polite">
          <div className="row first between"><h2 className="mono" style={{ fontSize: 22 }}>{ev.id}</h2>
            <button className="btn ghost sm noprint" onClick={() => setEv(null)}>Close</button></div>
          {([["Metric", ev.metric], ["Value", ev.value], ["Threshold", ev.threshold], ["Segment", ev.segment],
            ["Baseline window", ev.baseline], ["Current window", ev.window], ["Method", `${ev.method} (code, no model)`]] as const).map(([k, v]) => (
            <div key={k} className="row"><span className="mut grow">{k}</span><span className="mono">{String(v)}</span></div>
          ))}
          {ev.detail && <div className="row"><span className="mut grow">Detail</span><span className="mono">{ev.detail}</span></div>}
        </div>
      )}

      <div className="card">
        <div className="row first between">
          <h2 style={{ margin: 0 }}>Verified report</h2>
          {!user?.is_demo && (
            <button className="btn noprint" disabled={busy !== ""} onClick={() => run("report")}>
              {busy === "report" ? `Working… ${calls} model calls so far` : report ? "Regenerate" : "Generate verified report"}
            </button>
          )}
        </div>
        {!report && <p className="mut">
          {user?.is_demo ? "No report has been generated for this demo yet." : "Generate a report to have the agents investigate the evidence and the verifier check every claim."}
        </p>}
        {report && (
          <>
            <p className="mut">
              Written by {report.provider} / {report.model}.
              {usage && ` ${usage.calls} model calls, ${(usage.prompt_tokens + usage.completion_tokens).toLocaleString()} tokens, about ${cents(usage.cost_usd)}, ${usage.seconds} seconds.`}
            </p>
            {stats && <div className="row first"><Chip kind="ok">{stats.supported} supported</Chip><Chip kind="mid">{stats.partial} partial</Chip>
              <Chip kind="bad">{stats.insufficient} insufficient evidence</Chip><span className="mut">{stats.rewritten} rewritten once{stats.first_pass_supported !== undefined && `, ${stats.first_pass_supported} of ${stats.claims} supported on the first draft`}</span></div>}
            {report.report.claims.map((c, i) => <ClaimRow key={i} c={c} onEvidence={showEvidence} />)}
            <details className="noprint" style={{ marginTop: 12 }}>
              <summary>Agent trace</summary>
              <ol>
                {report.report.steps.map((s, i) => <li key={i}>{stepLabel(s)}</li>)}
                {report.report.notes.map((n) => <li key={n} className="mut">Investigator note: {n}</li>)}
              </ol>
            </details>
            {report.report.fingerprint && <p className="mono mut" style={{ fontSize: 12, wordBreak: "break-all" }}>Fingerprint (SHA-256): {report.report.fingerprint}</p>}
          </>
        )}
      </div>

      {hyps.length > 0 && (
        <div className="card">
          <h2>Hypotheses, not verified</h2>
          <p className="mut">Possible causes the evidence cannot establish. Each comes with a check an analyst can run.</p>
          {hyps.map((h, i) => (
            <div key={i} className={`row ${i === 0 ? "first" : ""}`} style={{ display: "block" }}>
              <p className="serif" style={{ fontSize: 19, margin: "0 0 6px" }}>{h.text}</p>
              <div className="row first" style={{ padding: 0 }}>
                <Chip kind="mid">Unverified</Chip>
                {h.evidence_ids.map((eid) => <button key={eid} className="btn ghost sm mono" onClick={() => showEvidence(eid)}>{eid}</button>)}
              </div>
              <p style={{ margin: "6px 0 0" }}><strong>Next check:</strong> {h.next_check}</p>
            </div>
          ))}
        </div>
      )}

      {report && (
        <div className="card">
          <div className="row first between">
            <div><h2 style={{ margin: 0 }}>Red team</h2>
              <p className="mut" style={{ margin: "4px 0 0" }}>Inject fabricated claims and see whether the verifier catches them.</p></div>
            {!user?.is_demo && <button className="btn noprint" disabled={busy !== ""} onClick={() => run("redteam")}>{busy === "redteam" ? `Testing… ${calls} calls so far` : "Inject fabricated claims"}</button>}
          </div>
          {report.redteam && (
            <>
              <p><strong>{report.redteam.caught} of {report.redteam.total}</strong> fabricated claims were rejected ({pct(report.redteam.caught, report.redteam.total)}).</p>
              {report.redteam.claims.map((c, i) => (
                <div key={i} className="row" style={{ display: "block" }}>
                  <p className="serif" style={{ fontSize: 19, margin: "0 0 6px" }}>{c.text}</p>
                  <div className="row first" style={{ padding: 0 }}>
                    <Chip kind={c.verdict === "unsupported" ? "ok" : "bad"}>{c.verdict === "unsupported" ? "Caught" : "Not caught"}</Chip>
                    <Verdict v={c.verdict} />
                  </div>
                  {c.reason && <p className="mut" style={{ margin: "6px 0 0" }}>{c.reason}</p>}
                </div>
              ))}
            </>
          )}
        </div>
      )}
    </main>
  );
}
