"""Evaluation harness.

    python -m talo.evals --models muse-spark-1.1,muse-spark-1.2,muse-spark-1.3 --seeds 3

Deterministic detection quality is scored against injected ground truth. For each model provider it
then runs the full agent pipeline and the red-team claims on fresh synthetic audits and records
claim-verification rates, red-team catch rate, latency and token use. Writes evals/results.json.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import statistics
from dataclasses import asdict
from pathlib import Path

from .agents import build_report, redteam
from .evidence import detect, detection_scores, segment_breakdown, segment_evidence
from .llm.guard import GuardedLLM
from .llm.providers import provider_for_mode
from .synth import CUTOFF, generate

N = 40_000


def detection_eval(seeds: int) -> dict:
    rec, prec, false_pos = [], [], 0
    for seed in range(seeds):
        df, manifest = generate(n=N, seed=seed)
        s = detection_scores(manifest, detect(df))
        rec.append(s["recall"])
        prec.append(s["precision"])
        false_pos += len(detect(generate(n=N, seed=100 + seed, issues=())[0]))
    return {"datasets": seeds, "recall": statistics.mean(rec), "precision": statistics.mean(prec),
            "clean_datasets": seeds, "clean_false_positives": false_pos}


def provider_eval(mode: str, seeds: int, model: str | None = None) -> dict | None:
    provider = provider_for_mode(mode, model)
    if provider is None:
        print(f"skipping {mode}: no API key set")
        return None
    events: list[dict] = []
    llm = GuardedLLM(provider, events.append)
    t = dict(claims=0, supported=0, partial=0, insufficient=0, rewritten=0, first_pass_supported=0,
             redteam_total=0, redteam_caught=0, failures=0)
    for seed in range(seeds):
        print(f"  {provider.model}: audit {seed + 1} of {seeds}", flush=True)
        df, _ = generate(n=N, seed=seed)
        findings = [asdict(f) for f in detect(df)]
        findings += segment_evidence(segment_breakdown(df, CUTOFF), findings)
        try:
            rep = build_report(llm, findings, {"n_rows": len(df), "cutoff": "2026-09-01"})
            rt = redteam(llm, findings)
        except Exception as exc:  # keep going: failures are themselves a result
            t["failures"] += 1
            print(f"{mode} seed {seed} failed: {exc}")
            continue
        for k in ("claims", "supported", "partial", "insufficient", "rewritten", "first_pass_supported"):
            t[k] += rep["stats"][k]
        t["redteam_total"] += rt["total"]
        t["redteam_caught"] += rt["caught"]
    ok = [e for e in events if e["status"] == "ok"]
    return {"mode": mode, "provider": provider.name, "model": provider.model, "audits": seeds - t["failures"], **t,
            "calls": len(events), "call_errors": len(events) - len(ok),
            "avg_latency_ms": int(statistics.mean(e["latency_ms"] for e in ok)) if ok else 0,
            "prompt_tokens": sum(e["prompt_tokens"] for e in ok),
            "completion_tokens": sum(e["completion_tokens"] for e in ok)}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S")
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="cloud")
    ap.add_argument("--models", default="", help="comma-separated Meta model ids to compare, e.g. muse-spark-1.2,muse-spark-1.3")
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()
    path = Path(os.getenv("TALO_EVALS_PATH", "evals/results.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    print("Scoring drift detection (no model calls)...", flush=True)
    out = {"available": True, "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "detection": detection_eval(args.seeds), "providers": []}
    path.write_text(json.dumps(out, indent=2))
    runs = ([("cloud", m.strip()) for m in args.models.split(",") if m.strip()]
            or [(m.strip(), None) for m in args.modes.split(",")])
    try:
        for mode, model in runs:
            print(f"Running {model or mode} ...", flush=True)
            res = provider_eval(mode, args.seeds, model)
            if res:
                out["providers"].append(res)
                path.write_text(json.dumps(out, indent=2))  # save after every model, so nothing is lost
                print(f"Saved results for {res['model']}", flush=True)
    except KeyboardInterrupt:
        print("Interrupted. Keeping the results finished so far.")
    print(f"wrote {path}")


if __name__ == "__main__":
    main()
