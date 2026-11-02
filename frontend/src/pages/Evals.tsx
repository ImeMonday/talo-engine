import { useEffect, useState } from "react";
import { api, errMsg, EvalProvider, Evals as EvalsData } from "../api";
import { Err, pct } from "../ui";

const ROWS: [string, (p: EvalProvider) => string][] = [
  ["Audits completed", (p) => `${p.audits} of ${p.audits + p.failures}`],
  ["Supported on the first draft", (p) => (p.first_pass_supported === undefined ? "n/a" : pct(p.first_pass_supported, p.claims))],
  ["Fully supported after rewrite", (p) => pct(p.supported, p.claims)],
  ["Claims partially supported", (p) => pct(p.partial, p.claims)],
  ["Left as insufficient evidence", (p) => pct(p.insufficient, p.claims)],
  ["Claims rewritten once", (p) => pct(p.rewritten, p.claims)],
  ["Fabricated claims rejected (10 per audit)", (p) => `${p.redteam_caught} of ${p.redteam_total} (${pct(p.redteam_caught, p.redteam_total)})`],
  ["Average latency per call", (p) => `${p.avg_latency_ms} ms`],
  ["Tokens per audit (report and red team)", (p) => (p.audits ? Math.round((p.prompt_tokens + p.completion_tokens) / p.audits).toLocaleString() : "n/a")],
  ["Failed calls", (p) => `${p.call_errors} of ${p.calls}`],
];

export default function Evals() {
  const [d, setD] = useState<EvalsData | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<EvalsData>("/api/evals").then(setD).catch((e) => setErr(errMsg(e)));
  }, []);

  return (
    <main className="wrap">
      <h1>Evals</h1>
      <p className="mut">Measured results on synthetic data with known, injected problems. Nothing here is estimated.</p>
      <Err msg={err} />
      {d && !d.available && (
        <div className="card"><h2>No results yet</h2>
          <p className="mut">Run the harness from the backend folder:</p>
          <pre>python -m talo.evals --models muse-spark-1.1,muse-spark-1.2,muse-spark-1.3 --seeds 3</pre></div>
      )}
      {d?.available && d.detection && (
        <div className="card">
          <h2>Drift detection (code, no model)</h2>
          <p>Recall <strong>{pct(d.detection.recall, 1)}</strong> and precision <strong>{pct(d.detection.precision, 1)}</strong> across {d.detection.datasets} datasets
            with injected issues. {d.detection.clean_false_positives} false alarms across {d.detection.clean_datasets} clean datasets.</p>
          <p className="mut">Generated {new Date(d.generated_at ?? "").toLocaleString()}</p>
        </div>
      )}
      {d?.providers && d.providers.length > 0 && (
        <div className="card scroll">
          <h2>Meta models compared</h2>
          <p className="mut">The same audits, run end to end with each model.</p>
          <table>
            <thead><tr><th>Measure</th>{d.providers.map((p) => <th key={p.model} className="mono">{p.model}</th>)}</tr></thead>
            <tbody>
              {ROWS.map(([label, fn]) => (
                <tr key={label}><td>{label}</td>{d.providers!.map((p) => <td key={p.model} className="mono">{fn(p)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </main>
  );
}
