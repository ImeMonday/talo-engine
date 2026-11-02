"""Generate the real report and red-team run for the demo workspace (needs model keys).

    python -m talo.seed_report --mode cloud
"""
from __future__ import annotations

import argparse
import json
import os

from sqlalchemy import select

from .agents import build_report, redteam
from .api import seed_demo
from .db import Audit, Organization, make_session_factory
from .llm.guard import GuardedLLM
from .llm.providers import provider_for_mode
from .reports import UsageMeter, audit_context, audit_evidence, fingerprint, make_recorder, save_report


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["cloud", "open_weights"], default="cloud")
    mode = ap.parse_args().mode
    provider = provider_for_mode(mode)
    if provider is None:
        raise SystemExit("Set MODEL_API_KEY (cloud) or BASETEN_API_KEY (open_weights) first.")
    sf = make_session_factory(os.getenv("TALO_DB_URL", "sqlite:///./talo.db"))
    seed_demo(sf)
    with sf() as s:
        org = s.scalar(select(Organization).where(Organization.is_demo))
        audit = s.scalar(select(Audit).where(Audit.org_id == org.id).order_by(Audit.id))
        meter = UsageMeter(make_recorder(sf, org.id))
        llm = GuardedLLM(provider, meter.record)
        findings = audit_evidence(audit)
        report = build_report(llm, findings, audit_context(audit, findings))
        report["usage"], report["fingerprint"] = meter.summary(), fingerprint(findings, report["claims"])
        row = save_report(s, org.id, audit.id, provider, report)
        row.redteam_json = json.dumps(redteam(llm, findings))
        s.commit()
    print(f"Demo report saved using {provider.name} / {provider.model}")


if __name__ == "__main__":
    main()
