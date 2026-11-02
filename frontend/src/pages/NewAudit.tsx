import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, Audit, errMsg, MappingRow, Preview } from "../api";
import { useAuth } from "../auth";
import { Err } from "../ui";

const CANON = ["timestamp", "channel", "amount", "model_score", "decision_flagged", "label_delay_days"];

export default function NewAudit() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [name, setName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [cutoff, setCutoff] = useState("");
  const [rows, setRows] = useState<MappingRow[] | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [via, setVia] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<"" | "analyse" | "run">("");

  if (user?.is_demo) {
    return (
      <main className="wrap"><div className="card"><h1>The demo is read-only</h1>
        <p className="mut">Create an account to upload your own decision log.</p></div></main>
    );
  }

  async function analyse() {
    if (!file) return;
    setBusy("analyse");
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const p = await api<Preview>("/api/audits/mapping-preview", { method: "POST", body: fd });
      setRows(p.proposal);
      setIssues(p.issues);
      setVia(`${p.provider} / ${p.model}`);
    } catch (e) {
      setErr(errMsg(e));
    } finally {
      setBusy("");
    }
  }

  async function run(useMapping: boolean) {
    if (!file) return;
    setBusy("run");
    setErr(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("name", name.trim() || file.name);
      if (cutoff) fd.append("cutoff", cutoff);
      if (useMapping && rows) {
        const map = Object.fromEntries(rows.filter((r) => r.target).map((r) => [r.source, r.target]));
        fd.append("mapping", JSON.stringify(map));
      }
      const a = await api<Audit>("/api/audits", { method: "POST", body: fd });
      nav(`/app/audits/${a.id}`);
    } catch (e) {
      setErr(errMsg(e));
      setBusy("");
    }
  }

  const setTarget = (i: number, target: string) =>
    setRows((rs) => rs && rs.map((r, k) => (k === i ? { ...r, target: target || null } : r)));

  return (
    <main className="wrap">
      <h1>New audit</h1>
      <div className="card" style={{ marginTop: 16 }}>
        <h2>1. Upload a decision log</h2>
        <p className="mut">CSV, up to 10 MB. Use synthetic or anonymised data only.</p>
        <p>No file? Download a sample: <a href="/api/sample.csv" download>standard columns</a> or{" "}
          <a href="/api/sample.csv?messy=1" download>different column names</a> (use this one to watch the model map the columns).</p>
        <label htmlFor="name">Audit name</label>
        <input id="name" value={name} onChange={(e) => setName(e.target.value)} placeholder="Retail fraud model v4, Q3" maxLength={120} />
        <label htmlFor="file">CSV file</label>
        <input id="file" type="file" accept=".csv,text/csv" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setRows(null); }} />
        <label htmlFor="cutoff">Baseline ends on (optional)</label>
        <input id="cutoff" type="date" value={cutoff} onChange={(e) => setCutoff(e.target.value)} />
        <p className="mut">Rows before this date form the baseline. Leave empty to use the first two thirds of the date range.</p>
        <div className="actions">
          <button className="btn" disabled={!file || busy !== ""} onClick={analyse}>
            {busy === "analyse" ? "Analysing columns…" : "Map my columns with the model"}
          </button>
          <button className="btn ghost" disabled={!file || busy !== ""} onClick={() => run(false)}>
            {busy === "run" && !rows ? "Running…" : "My columns already match, run audit"}
          </button>
        </div>
        <Err msg={err} />
        <p className="mut">Standard column names: {CANON.join(", ")}.</p>
      </div>

      {rows && (
        <div className="card">
          <h2>2. Confirm the column mapping</h2>
          <p className="mut">Proposed by {via} from column names and aggregates only. No rows were sent. Check it, change anything that is wrong, then run.</p>
          <div className="scroll">
            <table>
              <thead><tr><th>Your column</th><th>Maps to</th><th>Confidence</th><th>Why</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={r.source}>
                    <td className="mono">{r.source}</td>
                    <td>
                      <select aria-label={`Standard name for ${r.source}`} value={r.target ?? ""} onChange={(e) => setTarget(i, e.target.value)}>
                        <option value="">(ignore)</option>
                        {CANON.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </td>
                    <td className="mono">{Math.round(r.confidence * 100)}%</td>
                    <td className="mut">{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {issues.length > 0 && <div className="err" role="alert"><strong>Needs attention:</strong><ul>{issues.map((i) => <li key={i}>{i}</li>)}</ul></div>}
          <div className="actions">
            <button className="btn" disabled={busy !== ""} onClick={() => run(true)}>{busy === "run" ? "Running audit…" : "Confirm and run audit"}</button>
          </div>
        </div>
      )}
    </main>
  );
}
