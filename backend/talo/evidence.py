"""Deterministic evidence engine. No LLM is involved anywhere in this module."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

from .synth import CUTOFF

_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    metric: str
    value: float
    threshold: float
    segment: str
    baseline: str
    window: str
    method: str
    severity: str


def psi(base: np.ndarray, cur: np.ndarray, bins: int = 10) -> float:
    """Population Stability Index using baseline quantile bins."""
    edges = np.unique(np.quantile(base, np.linspace(0, 1, bins + 1)))
    edges[0], edges[-1] = -np.inf, np.inf
    b = np.histogram(base, edges)[0] / len(base)
    c = np.histogram(cur, edges)[0] / len(cur)
    b, c = np.clip(b, 1e-6, None), np.clip(c, 1e-6, None)
    return float(np.sum((c - b) * np.log(c / b)))


def _window(ts: pd.Series) -> str:
    return f"{ts.min():%Y-%m-%d}..{ts.max():%Y-%m-%d}"


def detect(df: pd.DataFrame, cutoff=CUTOFF) -> list[Evidence]:
    """Compare rows before `cutoff` (baseline) with rows from it onward (current).

    Returns findings with stable IDs (E-001, E-002, ...), most severe first.
    """
    cutoff = pd.Timestamp(cutoff)
    base, cur = df[df.timestamp < cutoff], df[df.timestamp >= cutoff]
    if base.empty or cur.empty:
        raise ValueError("Both the baseline and current windows need rows; adjust the cutoff.")
    bw, cw = _window(base.timestamp), _window(cur.timestamp)
    out: list[Evidence] = []

    def ev(kind, metric, value, threshold, segment, method, severity):
        out.append(Evidence("", kind, metric, round(float(value), 4), threshold,
                            segment, bw, cw, method, severity))

    v = psi(base.amount.to_numpy(), cur.amount.to_numpy())
    if v >= 0.25:
        ev("amount_drift", "PSI", v, 0.25, "all", "psi_deciles", "high")

    ks = stats.ks_2samp(base.model_score, cur.model_score)
    if ks.statistic >= 0.05 and ks.pvalue < 1e-3:
        ev("score_drift", "KS", ks.statistic, 0.05, "all", "ks_2samp",
           "high" if ks.statistic >= 0.2 else "medium")

    for ch in sorted(df.channel.unique()):  # two-proportion z-test on alert rate
        b, c = base[base.channel == ch], cur[cur.channel == ch]
        if b.empty or c.empty:
            continue
        p1, p2 = b.decision_flagged.mean(), c.decision_flagged.mean()
        pool = (b.decision_flagged.sum() + c.decision_flagged.sum()) / (len(b) + len(c))
        se = np.sqrt(pool * (1 - pool) * (1 / len(b) + 1 / len(c)))
        z = (p2 - p1) / se if se else 0.0
        if p1 > 0 and p2 / p1 >= 1.5 and z > 3.29 and c.decision_flagged.sum() >= 30:
            ev("alert_rate_shift", "alert_rate_ratio", p2 / p1, 1.5,
               f"channel={ch}", "two_proportion_z", "high")

    ratio = cur.label_delay_days.median() / base.label_delay_days.median()
    if ratio >= 1.5:
        ev("label_delay", "median_delay_ratio", ratio, 1.5, "all", "median_ratio", "medium")

    out.sort(key=lambda e: _RANK[e.severity])
    return [Evidence(f"E-{i:03d}", *tuple(e.__dict__.values())[1:]) for i, e in enumerate(out, 1)]


def daily_series(df: pd.DataFrame, cutoff=CUTOFF) -> list[dict]:
    """Per-day drift signals vs the baseline window, for the timeline chart."""
    base = df[df.timestamp < pd.Timestamp(cutoff)]
    if base.empty:
        return []
    amt, score = base.amount.to_numpy(), base.model_score
    out = []
    for day, g in df.groupby(df.timestamp.dt.normalize()):
        if len(g) < 50:
            continue
        out.append({"date": f"{day:%Y-%m-%d}", "n": int(len(g)),
                    "amount_psi": round(psi(amt, g.amount.to_numpy()), 4),
                    "score_ks": round(float(stats.ks_2samp(score, g.model_score).statistic), 4),
                    "alert_rate": round(float(g.decision_flagged.mean()), 4)})
    return out


def segment_breakdown(df: pd.DataFrame, cutoff=CUTOFF) -> list[dict]:
    """Per-channel view of where the drift sits: alert rate, amount and score shift."""
    cutoff = pd.Timestamp(cutoff)
    base, cur = df[df.timestamp < cutoff], df[df.timestamp >= cutoff]
    rows = []
    for ch in sorted(df.channel.unique()):
        b, c = base[base.channel == ch], cur[cur.channel == ch]
        if len(b) < 30 or len(c) < 30:
            continue
        mb = float(b.amount.median())
        rows.append({
            "segment": f"channel={ch}", "n_baseline": int(len(b)), "n_current": int(len(c)),
            "alert_rate_baseline": round(float(b.decision_flagged.mean()), 4),
            "alert_rate_current": round(float(c.decision_flagged.mean()), 4),
            "median_amount_ratio": round(float(c.amount.median()) / mb, 3) if mb else None,
            "amount_psi": round(psi(b.amount.to_numpy(), c.amount.to_numpy()), 4),
            "score_ks": round(float(stats.ks_2samp(b.model_score, c.model_score).statistic), 4)})
    return rows


def segment_evidence(segments: list[dict], findings: list[dict]) -> list[dict]:
    """Per-channel profile as citable evidence (S-001, ...), so a report can say where drift is and is not."""
    if not segments or not findings:
        return []
    out = []
    for i, s in enumerate(segments, 1):
        ratio = "n/a" if s["median_amount_ratio"] is None else s["median_amount_ratio"]
        out.append({
            "id": f"S-{i:03d}", "kind": "segment_profile", "metric": "amount_psi", "value": s["amount_psi"],
            "threshold": 0.25, "segment": s["segment"], "baseline": findings[0]["baseline"],
            "window": findings[0]["window"], "method": "per_channel_comparison", "severity": "info",
            "detail": (f"flagged rate {s['alert_rate_baseline'] * 100:.1f}% to {s['alert_rate_current'] * 100:.1f}%; "
                       f"median amount ratio {ratio}; score KS {s['score_ks']}; rows {s['n_baseline']} to {s['n_current']}")})
    return out


def detection_scores(manifest, findings) -> dict[str, float]:
    """Score findings against the injected-issue manifest (used by the eval harness)."""
    truth = {(m.kind, m.segment) for m in manifest}
    found = {(f.kind, f.segment) for f in findings}
    tp = len(truth & found)
    return {"recall": tp / len(truth) if truth else 1.0,
            "precision": tp / len(found) if found else 1.0}
