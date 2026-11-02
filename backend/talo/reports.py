"""Report persistence helpers shared by the API and the seed CLI."""
from __future__ import annotations

import hashlib
import json
import os
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Audit, EgressEvent, Report
from .evidence import segment_evidence


PRICE_IN = float(os.getenv("TALO_PRICE_IN_PER_M", "1.25"))    # USD per million input tokens (Muse Spark list price)
PRICE_OUT = float(os.getenv("TALO_PRICE_OUT_PER_M", "4.25"))  # USD per million output tokens


def token_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * PRICE_IN + completion_tokens * PRICE_OUT) / 1_000_000


class UsageMeter:
    """Wraps the ledger recorder and totals what one run used."""

    def __init__(self, inner):
        self.inner, self.calls, self.pt, self.ct, self.t0 = inner, 0, 0, 0, time.perf_counter()

    def record(self, event: dict) -> None:
        self.calls += 1
        self.pt += event["prompt_tokens"]
        self.ct += event["completion_tokens"]
        self.inner(event)

    def summary(self) -> dict:
        return {"calls": self.calls, "prompt_tokens": self.pt, "completion_tokens": self.ct,
                "cost_usd": round(token_cost(self.pt, self.ct), 4), "seconds": round(time.perf_counter() - self.t0, 1)}


def fingerprint(findings: list[dict], claims: list[dict]) -> str:
    """SHA-256 over the evidence and the verified claims, so a printed report can be checked later."""
    body = {"findings": findings, "claims": [[c["text"], c["verdict"], c["evidence_ids"]] for c in claims]}
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def audit_findings(audit: Audit) -> list[dict]:
    return json.loads(audit.findings_json)


def audit_evidence(audit: Audit) -> list[dict]:
    """Everything a report may cite: threshold findings (E-) plus per-channel profiles (S-)."""
    findings = json.loads(audit.findings_json)
    return findings + segment_evidence(json.loads(audit.segments_json or "[]"), findings)


def audit_context(audit: Audit, findings: list[dict]) -> dict:
    return {"n_rows": audit.n_rows, "cutoff": audit.cutoff,
            "baseline": findings[0]["baseline"] if findings else "",
            "current": findings[0]["window"] if findings else ""}


def make_recorder(session_factory, org_id: int):
    """Returns the sink GuardedLLM calls for every outbound model request."""
    def record(event: dict) -> None:
        with session_factory() as s:
            s.add(EgressEvent(org_id=org_id, **event))
            s.commit()
    return record


def save_report(s: Session, org_id: int, audit_id: int, provider, report: dict) -> Report:
    row = Report(org_id=org_id, audit_id=audit_id, provider=provider.name, model=provider.model,
                 report_json=json.dumps(report))
    s.add(row)
    s.commit()
    return row


def latest_report(s: Session, org_id: int, audit_id: int) -> Report | None:
    return s.scalar(select(Report).where(Report.org_id == org_id, Report.audit_id == audit_id)
                    .order_by(Report.id.desc()))


def report_out(r: Report) -> dict:
    return {"id": r.id, "created_at": r.created_at.isoformat(), "provider": r.provider, "model": r.model,
            "report": json.loads(r.report_json),
            "redteam": json.loads(r.redteam_json) if r.redteam_json else None}
