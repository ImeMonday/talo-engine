import { useEffect, useState } from "react";
import { api, EgressEvent, errMsg } from "../api";
import { Chip, Err, Stat } from "../ui";

type Data = { raw_rows_sent: number; calls: number; cost_usd: number; events: EgressEvent[] };

const pretty = (s: string) => {
  try { return JSON.stringify(JSON.parse(s), null, 2); } catch { return s; }
};

export default function Ledger() {
  const [data, setData] = useState<Data | null>(null);
  const [sel, setSel] = useState<EgressEvent | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api<Data>("/api/egress").then(setData).catch((e) => setErr(errMsg(e)));
  }, []);
  const open = (id: number) => api<EgressEvent>(`/api/egress/${id}`).then(setSel).catch((e) => setErr(errMsg(e)));

  return (
    <main className="wrap">
      <h1>Egress Ledger</h1>
      <p className="mut">Every request this workspace has sent to a model, in order, with the exact payload.</p>
      <Err msg={err} />
      <div className="grid" style={{ marginTop: 16 }}>
        <Stat dark label="Raw customer rows sent" value={data?.raw_rows_sent ?? 0} sub="Blocked by the egress guard if attempted" />
        <Stat label="Model calls" value={data?.calls ?? 0} sub="Schema mapping, investigator, writer, verifier" />
        <Stat label="Estimated model spend" value={`$${(data?.cost_usd ?? 0).toFixed(3)}`} sub="At Meta's list price per token" />
      </div>
      <div className="card scroll">
        {data && data.events.length === 0 && <p className="mut">Nothing has left this workspace yet.</p>}
        {data && data.events.length > 0 && (
          <table>
            <thead><tr><th>Time</th><th>Purpose</th><th>Model</th><th>Size</th><th>Tokens in/out</th><th>Latency</th><th>Status</th><th /></tr></thead>
            <tbody>
              {data.events.map((e) => (
                <tr key={e.id}>
                  <td className="mono">{new Date(e.created_at).toLocaleTimeString()}</td>
                  <td>{e.purpose.replace(/_/g, " ")}</td>
                  <td className="mono">{e.provider} / {e.model}</td>
                  <td className="mono">{(e.bytes_out / 1024).toFixed(1)} KB</td>
                  <td className="mono">{e.prompt_tokens}/{e.completion_tokens}</td>
                  <td className="mono">{e.latency_ms} ms</td>
                  <td><Chip kind={e.status === "ok" ? "ok" : "bad"}>{e.status}</Chip></td>
                  <td><button className="btn ghost sm" onClick={() => open(e.id)}>View payload</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {sel && (
        <div className="card">
          <div className="row first between"><h2 style={{ margin: 0 }}>Payload #{sel.id}</h2><button className="btn ghost sm" onClick={() => setSel(null)}>Close</button></div>
          <p className="mut mono">sha256 {sel.payload_sha256}</p>
          <pre>{pretty(sel.payload ?? "")}</pre>
        </div>
      )}
    </main>
  );
}
