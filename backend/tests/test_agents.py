import json
from dataclasses import asdict

import pytest

from talo.agents import build_report, check_claim, Claim, redteam
from talo.evidence import detect
from talo.llm.base import LLMResult
from talo.llm.guard import GuardedLLM
from talo.synth import generate


@pytest.fixture(scope="module")
def findings():
    return [asdict(f) for f in detect(generate(n=40_000)[0])]


class ScriptedModel:
    """Plays all four agent roles. Role is picked from the system prompt, like a real model would."""
    name, model = "scripted", "s-1"

    def __init__(self, findings):
        self.f, self.step = findings, 0

    def chat(self, system, user):
        payload = json.loads(user.split("\n\n")[0])
        f0 = self.f[0]
        if "investigator" in system:
            self.step += 1
            reply = [{"action": "list_findings"}, {"action": "get_evidence", "id": f0["id"]},
                     {"action": "finish", "notes": [f"{f0['id']} is the largest shift."]}][min(self.step, 3) - 1]
        elif "findings section" in system:
            reply = {"claims": [
                {"text": f"Transaction amounts shifted upward, with a {f0['metric']} of {f0['value']}.", "evidence_ids": [f0["id"]], "confidence": 0.9},
                {"text": "Fraud losses rose by 9.99 million.", "evidence_ids": [f0["id"]], "confidence": 0.5},
                {"text": "The drift was caused by a new product launch.", "evidence_ids": [f0["id"]], "confidence": 0.4},
                {"text": "Nothing was cited here.", "evidence_ids": [], "confidence": 0.2}],
                "hypotheses": [{"text": "A change in customer mix may explain this.", "evidence_ids": [f0["id"]], "next_check": "Compare amounts by customer segment."},
                               {"text": "Invalid citation.", "evidence_ids": ["E-999"], "next_check": "n/a"}]}
        elif "adversarial auditor" in system:
            reply = {"verdicts": [{"i": c["i"], "verdict": "unsupported" if "caused" in c["text"] else "supported",
                                   "reason": "checked"} for c in payload["claims"]]}
        else:
            reply = {"rewrites": [{"i": c["i"], "text": "INSUFFICIENT" if "caused" in c["text"]
                                   else f"Transaction amounts shifted, {f0['metric']} {f0['value']}."} for c in payload["claims"]]}
        return LLMResult(json.dumps(reply), self.model, self.name, 10, 5, 3)


def test_pipeline_end_to_end(findings):
    events = []
    rep = build_report(GuardedLLM(ScriptedModel(findings), events.append), findings, {"n_rows": 40000, "cutoff": "2026-09-01"})
    verdicts = [c["verdict"] for c in rep["claims"]]
    # claim 2 fails the number check, is rewritten once, and then verifies; claims 3 and 4 cannot be rescued
    assert verdicts == ["supported", "supported", "insufficient_evidence", "insufficient_evidence"]
    assert rep["claims"][1]["rewritten"] and rep["claims"][1]["original_text"].startswith("Fraud losses")
    assert rep["stats"] == {"claims": 4, "supported": 2, "partial": 0, "insufficient": 2, "rewritten": 1, "first_pass_supported": 1}
    assert [s["action"] for s in rep["steps"] if s["step"] == "tool"] == ["list_findings", "get_evidence"]
    assert [h["next_check"] for h in rep["hypotheses"]] == ["Compare amounts by customer segment."]  # the bad citation is dropped
    assert all(e["raw_rows"] == 0 for e in events) and {e["purpose"] for e in events} >= {"investigator", "report_writer", "verifier"}


def test_number_checker_catches_invented_numbers(findings):
    by_id = {f["id"]: f for f in findings}
    f0 = findings[0]
    assert check_claim(Claim(text=f"{f0['metric']} was {f0['value']}.", evidence_ids=[f0["id"]]), by_id) == []
    assert check_claim(Claim(text="Losses were 12.5 million.", evidence_ids=[f0["id"]]), by_id)
    assert check_claim(Claim(text="Something happened.", evidence_ids=[]), by_id) == ["cites no evidence"]
    assert check_claim(Claim(text="See E-999.", evidence_ids=["E-999"]), by_id)


def test_redteam_counts_caught(findings):
    rt = redteam(GuardedLLM(ScriptedModel(findings), lambda e: None), findings)
    assert rt["total"] == 8 and rt["caught"] == 4   # code stops 3; the scripted verifier rejects the causal claim


def test_usage_meter_and_fingerprint(findings):
    from talo.reports import UsageMeter, fingerprint, token_cost
    seen = []
    m = UsageMeter(seen.append)
    m.record({"prompt_tokens": 1000, "completion_tokens": 2000})
    m.record({"prompt_tokens": 500, "completion_tokens": 0})
    s = m.summary()
    assert (s["calls"], s["prompt_tokens"], s["completion_tokens"]) == (2, 1500, 2000) and len(seen) == 2
    assert s["cost_usd"] == round(token_cost(1500, 2000), 4)
    claims = [{"text": "a", "verdict": "supported", "evidence_ids": ["E-001"]}]
    assert fingerprint(findings, claims) == fingerprint(findings, claims)
    assert fingerprint(findings, claims) != fingerprint(findings, [{**claims[0], "text": "b"}])


def test_segment_evidence_is_citable(findings):
    from talo.evidence import segment_breakdown, segment_evidence
    ev = segment_evidence(segment_breakdown(generate(n=40_000)[0]), findings)
    by_id = {e["id"]: e for e in findings + ev}
    ussd = next(e for e in ev if e["segment"] == "channel=ussd")
    assert ussd["id"].startswith("S-")
    ok = Claim(text=f"USSD moved: {ussd['detail'].split(';')[0]}.", evidence_ids=[ussd["id"]])
    assert check_claim(ok, by_id) == []
    assert check_claim(Claim(text="USSD flagged rate reached 12.3%.", evidence_ids=[ussd["id"]]), by_id)


def test_redteam_suite_is_varied_and_every_claim_cites_real_ids(findings):
    from talo.agents import redteam_claims
    from talo.evidence import segment_breakdown, segment_evidence
    ev = findings + segment_evidence(segment_breakdown(generate(n=40_000)[0]), findings)
    claims = redteam_claims(ev)
    assert len(claims) == 10 and len({c.text for c in claims}) == 10
    ids = {e["id"] for e in ev}
    assert all(set(c.evidence_ids) <= ids for c in claims)
