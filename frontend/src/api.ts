export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}
export const errMsg = (e: unknown) => (e instanceof Error ? e.message : String(e));

const csrf = () => document.cookie.split("; ").find((c) => c.startsWith("talo_csrf="))?.split("=")[1] ?? "";

/** Fetch wrapper: same-origin cookies, CSRF header on writes, readable errors. */
export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.method && init.method !== "GET") headers.set("X-CSRF-Token", csrf());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers, credentials: "same-origin" });
  if (res.status === 401 && !path.startsWith("/api/auth/") && path !== "/api/me") {
    window.location.assign("/login"); // session expired
  }
  if (!res.ok) {
    let msg = res.statusText || `Request failed (${res.status})`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* keep status text */ }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export type User = { id: number; email: string; is_demo: boolean; sandbox?: boolean; org: { id: number; name: string } };
export type Finding = {
  id: string; kind: string; metric: string; value: number; threshold: number; segment: string;
  baseline: string; window: string; method: string; severity: "high" | "medium" | "low";
};
export type Day = { date: string; n: number; amount_psi: number; score_ks: number; alert_rate: number };
export type AuditSummary = { id: number; name: string; created_at: string; n_rows: number; cutoff: string; n_findings: number };
export type Segment = {
  segment: string; n_baseline: number; n_current: number; alert_rate_baseline: number; alert_rate_current: number;
  median_amount_ratio: number | null; amount_psi: number; score_ks: number;
};
export type Evidence = Omit<Finding, "severity"> & { severity: string; detail?: string };
export type Audit = AuditSummary & { findings: Finding[]; timeline: Day[]; segments: Segment[]; evidence?: Evidence[] };
export type ClaimResult = {
  text: string; original_text: string | null; evidence_ids: string[]; confidence: number;
  verdict: "supported" | "partial" | "unsupported" | "insufficient_evidence";
  reason: string; rewritten: boolean; injected: boolean; checks: string[];
};
export type Usage = { calls: number; prompt_tokens: number; completion_tokens: number; cost_usd: number; seconds: number };
export type Report = {
  id: number; created_at: string; provider: string; model: string;
  report: {
    claims: ClaimResult[]; notes: string[]; steps: { step: string; action: string; id: string | null }[];
    stats: { claims: number; supported: number; partial: number; insufficient: number; rewritten: number; first_pass_supported?: number };
    hypotheses?: { text: string; evidence_ids: string[]; next_check: string }[];
    usage?: Usage; fingerprint?: string;
  };
  redteam: { claims: ClaimResult[]; caught: number; total: number; usage?: Usage } | null;
};
export type EgressEvent = {
  id: number; created_at: string; purpose: string; provider: string; model: string; status: string;
  bytes_out: number; prompt_tokens: number; completion_tokens: number; latency_ms: number; raw_rows: number;
  payload_sha256: string; payload?: string;
};
export type Settings = { llm_mode: "cloud" | "open_weights"; available: Record<string, boolean>; models?: Record<string, string | null> };
export type MappingRow = { source: string; target: string | null; confidence: number; reason: string };
export type Preview = { provider: string; model: string; proposal: MappingRow[]; issues: string[] };
export type EvalProvider = {
  mode: string; provider: string; model: string; audits: number; claims: number; supported: number; partial: number;
  insufficient: number; rewritten: number; first_pass_supported?: number; redteam_total: number; redteam_caught: number; failures: number;
  calls: number; call_errors: number; avg_latency_ms: number; prompt_tokens: number; completion_tokens: number;
};
export type Evals = {
  available: boolean; generated_at?: string;
  detection?: { datasets: number; recall: number; precision: number; clean_datasets: number; clean_false_positives: number };
  providers?: EvalProvider[];
};
