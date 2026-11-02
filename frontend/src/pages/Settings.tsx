import { useCallback, useEffect, useState } from "react";
import { api, errMsg, Settings as S } from "../api";
import { useAuth } from "../auth";
import { Chip, Err } from "../ui";

export default function Settings() {
  const { user } = useAuth();
  const [s, setS] = useState<S | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => api<S>("/api/settings").then(setS).catch((e) => setErr(errMsg(e))), []);
  useEffect(() => { void load(); }, [load]);

  async function choose(mode: string) {
    setErr(null);
    try {
      await api("/api/settings/mode", { method: "POST", body: JSON.stringify({ mode }) });
      await load();
    } catch (e) {
      setErr(errMsg(e));
    }
  }

  const options = [
    { id: "cloud", title: `Muse Spark${s?.models?.cloud ? ` (${s.models.cloud})` : ""}`,
      body: "Meta Model API, Standard tier: prompts and completions are not used for training. Raw rows are never sent, and the Egress Ledger shows every request." },
    { id: "open_weights", title: `Muse Spark Open${s?.models?.open_weights ? ` (${s.models.open_weights})` : ""}`,
      body: "Open-weights model through your Baseten endpoint." },
  ].filter((o) => s?.available[o.id]);

  return (
    <main className="wrap">
      <h1>Settings</h1>
      <Err msg={err} />
      <div className="card" style={{ marginTop: 16 }}>
        <h2>Model</h2>
        {s && options.length === 0 && <p className="mut">No model is connected. Add MODEL_API_KEY to your .env file and restart the API.</p>}
        {options.map((o, i) => (
          <div key={o.id} className={`row ${i === 0 ? "first" : ""}`}>
            <div className="grow"><strong>{o.title}</strong><div className="mut">{o.body}</div></div>
            {options.length === 1 || s?.llm_mode === o.id
              ? <Chip kind="ok">{options.length === 1 ? "Connected" : "In use"}</Chip>
              : <button className="btn ghost sm" disabled={user?.is_demo} onClick={() => choose(o.id)}>Use this</button>}
          </div>
        ))}
      </div>
      <div className="card">
        <h2>Account</h2>
        <div className="row first"><span className="mut grow">Email</span><span>{user?.email}</span></div>
        <div className="row"><span className="mut grow">Organisation</span><span>{user?.org.name}</span></div>
      </div>
    </main>
  );
}
