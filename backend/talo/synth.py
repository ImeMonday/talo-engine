"""Seeded synthetic fraud-model decision logs with documented, injectable drift.

Baseline window: Jul 1 - Aug 31. Current window: Sep 1 - Sep 30.
Every injected issue is returned in a manifest so detection can be scored.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

CHANNELS = ["mobile_app", "ussd", "web", "pos", "agent"]
REGIONS = ["NC", "NE", "NW", "SE", "SS", "SW"]
CUTOFF = pd.Timestamp("2026-09-01")
ALERT_THRESHOLD = 0.7
ALL_ISSUES = ("amount_drift", "score_drift", "alert_rate_shift", "label_delay")


@dataclass(frozen=True)
class InjectedIssue:
    kind: str
    segment: str
    magnitude: float


def generate(n: int = 60_000, seed: int = 7, issues: tuple[str, ...] = ALL_ISSUES):
    """Return (decision_log, manifest of injected issues)."""
    rng = np.random.default_rng(seed)
    ts = pd.Timestamp("2026-07-01") + pd.to_timedelta(rng.integers(0, 92 * 86400, n), unit="s")
    df = pd.DataFrame({"timestamp": ts})
    df["channel"] = rng.choice(CHANNELS, n, p=[0.38, 0.22, 0.18, 0.14, 0.08])
    df["region"] = rng.choice(REGIONS, n)
    df["amount"] = rng.lognormal(8.0, 1.0, n)
    fraud = rng.random(n) < 0.012
    score = np.where(fraud, rng.beta(5, 2, n), rng.beta(1.5, 8, n))
    delay = rng.lognormal(np.log(9), 0.4, n)
    cur = (df["timestamp"] >= CUTOFF).to_numpy()
    manifest: list[InjectedIssue] = []

    if "amount_drift" in issues:
        df.loc[cur, "amount"] *= 2.0
        manifest.append(InjectedIssue("amount_drift", "all", 2.0))
    if "score_drift" in issues:  # shift the bulk of low scores; the alert tail is untouched
        score = np.where(cur & (score < 0.5), score + 0.08, score)
        manifest.append(InjectedIssue("score_drift", "all", 0.08))
    decision = score >= ALERT_THRESHOLD
    if "alert_rate_shift" in issues:
        ussd = cur & (df["channel"].to_numpy() == "ussd")
        decision = decision | (ussd & (rng.random(n) < 0.04))
        manifest.append(InjectedIssue("alert_rate_shift", "channel=ussd", 0.04))
    if "label_delay" in issues:
        delay = np.where(cur, delay * 2.3, delay)
        manifest.append(InjectedIssue("label_delay", "all", 2.3))

    df["model_score"] = np.clip(score, 0, 1)
    df["decision_flagged"] = decision
    df["label_delay_days"] = delay
    df["is_fraud"] = fraud
    return df, manifest
