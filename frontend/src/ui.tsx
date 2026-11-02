import { ReactNode } from "react";
import type { Day } from "./api";

export function Wordmark({ size = 28 }: { size?: number }) {
  return (
    <span className="brand" style={{ display: "inline-flex", alignItems: "center", gap: 10 }}>
      <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true">
        <rect width="28" height="28" rx="7" fill="#16201B" />
        <path d="M7 9h14M14 9v11" stroke="#F4F1EA" strokeWidth="2.6" strokeLinecap="round" fill="none" />
        <circle cx="21" cy="20" r="2.4" fill="#2FA37A" />
      </svg>
      TALO
    </span>
  );
}

export const Skeleton = () => <div className="skeleton" aria-hidden="true" />;
export const Chip = ({ kind, children }: { kind: "ok" | "bad" | "mid"; children: ReactNode }) => (
  <span className={`chip ${kind}`}>{children}</span>
);
export const sevKind = (s: string): "ok" | "bad" | "mid" => (s === "high" ? "bad" : s === "medium" ? "mid" : "ok");
export const Err = ({ msg }: { msg: string | null }) => (msg ? <div className="err" role="alert">{msg}</div> : null);
export const pct = (n: number, d: number) => (d ? `${Math.round((100 * n) / d)}%` : "n/a");

export function Stat({ label, value, sub, dark }: { label: string; value: ReactNode; sub?: string; dark?: boolean }) {
  return (
    <div className={`card ${dark ? "dark" : ""}`}>
      <div className="lbl">{label}</div>
      <div className="big">{value}</div>
      {sub && <div className={dark ? "" : "mut"}>{sub}</div>}
    </div>
  );
}

const VERDICT: Record<string, ["ok" | "bad" | "mid", string]> = {
  supported: ["ok", "Supported"], partial: ["mid", "Partially supported"],
  unsupported: ["bad", "Rejected"], insufficient_evidence: ["bad", "Insufficient evidence"],
};
export function Verdict({ v }: { v: string }) {
  const [kind, text] = VERDICT[v] ?? ["mid", v];
  return <span className={`chip stamp ${kind}`}>{text}</span>;
}

/** Daily drift "seismograph": one series, the baseline/current cutoff marked. */
export function Timeline({ series, cutoff, field, title, fmt }: {
  series: Day[]; cutoff: string; field: "amount_psi" | "alert_rate" | "score_ks"; title: string; fmt: (n: number) => string;
}) {
  if (series.length < 2) return <p className="mut">Not enough days of data for a timeline.</p>;
  const W = 1000, H = 150, P = 10;
  const max = Math.max(...series.map((d) => d[field]), 1e-6);
  const x = (i: number) => P + (i * (W - 2 * P)) / (series.length - 1);
  const y = (v: number) => H - P - (v / max) * (H - 2 * P);
  const ci = series.findIndex((d) => d.date >= cutoff);
  const first = series[0].date, last = series[series.length - 1].date;
  return (
    <figure style={{ margin: "0 0 12px" }}>
      <figcaption className="lbl">{title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H + 22}`} role="img" aria-label={`${title} by day, ${first} to ${last}`} style={{ width: "100%", height: "auto" }}>
        <polyline fill="none" stroke="#0F6B4F" strokeWidth={2.5} points={series.map((d, i) => `${x(i)},${y(d[field])}`).join(" ")} />
        {ci > 0 && <line x1={x(ci)} x2={x(ci)} y1={0} y2={H} stroke="#C2410C" strokeWidth={1.5} strokeDasharray="6 6" />}
        <g fill="#5B665F" fontSize="13">
          <text x={P} y={H + 16}>{first}</text>
          <text x={W - P} y={H + 16} textAnchor="end">{last}</text>
          <text x={W - P} y={14} textAnchor="end">peak {fmt(max)}</text>
          {ci > 0 && <text x={x(ci) + 6} y={14} fill="#8A2A0B">cutoff {cutoff}</text>}
        </g>
      </svg>
    </figure>
  );
}
