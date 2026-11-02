"""Agentic audit pipeline.

investigator (tool loop over the evidence store) -> report writer -> deterministic number checks
-> adversarial verifier -> one rewrite of unsupported claims -> re-verify -> else "insufficient evidence".
Every model call goes through GuardedLLM, so every payload is checked and recorded in the Egress Ledger.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from .llm.guard import GuardedLLM

MAX_STEPS = 5


class Action(BaseModel):
    action: Literal["list_findings", "get_evidence", "finish"]
    id: str | None = None
    notes: list[str] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _as_list(cls, v):
        return [v] if isinstance(v, str) else (v or [])


class Claim(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = 0.5


class Hypothesis(BaseModel):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    next_check: str = ""


class Report(BaseModel):
    claims: list[Claim]
    hypotheses: list[Hypothesis] = Field(default_factory=list)


class Verdict(BaseModel):
    i: int
    verdict: Literal["supported", "partial", "unsupported"]
    reason: str = ""


class Verdicts(BaseModel):
    verdicts: list[Verdict]


class Rewrite(BaseModel):
    i: int
    text: str


class Rewrites(BaseModel):
    rewrites: list[Rewrite]


INVESTIGATOR = (
    "You are an audit investigator for a bank's fraud/KYC/AML model. You can look at the evidence only through tools. "
    'Reply with JSON only: {"action":"list_findings"|"get_evidence"|"finish","id":"E-001","notes":["..."]}. '
    "Use id only with get_evidence. Start with list_findings, then get_evidence for findings that matter, then finish "
    "with up to 6 short notes, each citing evidence ids. Ids starting with S- are per-channel profiles: check them to see "
    "whether a finding is concentrated in one channel. Never invent evidence.")
WRITER = (
    "You write the findings section of a model audit report for a bank's compliance team. "
    'Reply with JSON only: {"claims":[{"text":"...","evidence_ids":["E-001"],"confidence":0.0}],'
    '"hypotheses":[{"text":"...","evidence_ids":["E-001"],"next_check":"..."}]}. '
    "Write 5 to 8 claims. Each claim is one factual sentence under 280 characters that cites at least one evidence id "
    "from the input. Write in plain, natural audit language: say what moved, by how much, and how it compares with its "
    "threshold, for example 'Transaction amounts shifted materially (PSI 0.4444, above the 0.25 threshold)'. Never write "
    "'the value was X against a threshold of Y'. Evidence ids starting with E- are findings. Ids starting with S- are "
    "per-channel profiles: use them to say whether a shift is concentrated in one channel or present in all of them, and "
    "which channel's flagged rate moved; when a claim covers all channels, cite every S- id. Write one claim per finding, plus at least two claims that connect evidence "
    "across several ids. Any number in a claim must appear in the cited evidence (value, threshold or detail) exactly as "
    "given. A claim may state only what the evidence establishes: no causes, losses, customer behaviour or business impact. "
    "Put possible causes in hypotheses instead, up to 3. Each hypothesis is a short guess under 200 characters that cites "
    "evidence ids, with a concrete next_check under 200 characters that an analyst could run. A hypothesis must be "
    "consistent with the S- evidence: never blame one channel for a shift the S- evidence shows in every channel. "
    "Hypotheses are shown to the reader as unverified.")
VERIFIER = (
    "You are an adversarial auditor checking claims against evidence. For each claim decide: "
    '"supported" only if every assertion in it is directly established by the cited evidence; '
    '"partial" only if every assertion is true but the claim overstates how strong or certain the evidence is; '
    '"unsupported" if any assertion (a cause, impact, loss, trend or consequence) is not established by the evidence, or the evidence contradicts it. '
    'Reply with JSON only: {"verdicts":[{"i":0,"verdict":"supported|partial|unsupported","reason":"one short sentence"}]}. '
    "Judge only from the evidence given.")
REWRITER = (
    "Rewrite each claim as one natural sentence under 280 characters that states only what its cited evidence "
    "establishes. Remove the part the evidence does not establish; do not list raw data. Keep every number exactly as "
    'in the evidence. Reply with JSON only: {"rewrites":[{"i":0,"text":"..."}]}. '
    'If nothing can be said from the evidence, use the text "INSUFFICIENT".')


class EvidenceStore:
    """The only window the investigator has onto the data: aggregates, via tools."""

    def __init__(self, findings: list[dict], context: dict):
        self.by_id = {f["id"]: f for f in findings}
        self.context = context

    def call(self, action: str, ev_id: str | None):
        if action == "list_findings":
            keys = ("id", "kind", "severity", "segment", "metric", "value")
            return [{k: f[k] for k in keys} for f in self.by_id.values()]
        return self.by_id.get(ev_id or "") or {"error": f"unknown evidence id {ev_id}"}


def investigate(llm: GuardedLLM, store: EvidenceStore, steps: list[dict]) -> list[str]:
    transcript: list[dict] = []
    for _ in range(MAX_STEPS):
        a = llm.structured("investigator", INVESTIGATOR,
                           {"context": store.context, "transcript": transcript[-6:]}, Action)
        if a.action == "finish":
            return [n[:290] for n in a.notes[:6]]
        transcript.append({"action": a.action, "id": a.id, "result": store.call(a.action, a.id)})
        steps.append({"step": "tool", "action": a.action, "id": a.id})
    return []


def _clean(c: Claim) -> Claim:
    ids = list(dict.fromkeys(c.evidence_ids))[:5]
    return Claim(text=c.text.strip()[:290], evidence_ids=ids, confidence=min(max(c.confidence, 0.0), 1.0))


def write_report(llm: GuardedLLM, findings: list[dict], notes: list[str]) -> tuple[list[Claim], list[Hypothesis]]:
    out = llm.structured("report_writer", WRITER, {"findings": findings, "investigator_notes": notes}, Report)
    return [_clean(c) for c in out.claims[:10]], out.hypotheses


def clean_hypotheses(items: list[Hypothesis], by_id: dict) -> list[dict]:
    """Hypotheses are never verified as facts, but they must cite real evidence and quote only real numbers."""
    out = []
    for h in items[:3]:
        c = Claim(text=h.text.strip()[:290], evidence_ids=list(dict.fromkeys(h.evidence_ids))[:5])
        if c.text and not check_claim(c, by_id):
            out.append({"text": c.text, "evidence_ids": c.evidence_ids, "next_check": h.next_check.strip()[:290]})
    return out


# ---- deterministic checks -------------------------------------------------------------------

def _numbers(text: str) -> list[float]:
    text = re.sub(r"\bE-\d{3}\b", " ", text)
    text = re.sub(r"\d{4}-\d{2}-\d{2}", " ", text)
    return [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]


def _allowed(ev: dict) -> set[float]:
    vals = {float(ev["value"]), float(ev["threshold"])}
    if str(ev["metric"]).endswith("ratio"):
        vals |= {round((ev["value"] - 1) * 100, 1), round(ev["value"] * 100, 1)}
    for key in ("baseline", "window"):
        vals |= {float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(ev[key]))}
    vals |= {float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(ev.get("detail", "")))}
    return vals


def check_claim(claim: Claim, by_id: dict) -> list[str]:
    """Code, not a model: cited evidence must exist and every number must come from it."""
    if not claim.evidence_ids:
        return ["cites no evidence"]
    issues = [f"cites unknown evidence {i}" for i in claim.evidence_ids if i not in by_id]
    allowed: set[float] = set()
    for i in claim.evidence_ids:
        if i in by_id:
            allowed |= _allowed(by_id[i])
    for n in _numbers(claim.text):
        if not any(abs(n - a) <= max(0.006, 0.02 * abs(a)) for a in allowed):
            issues.append(f"the number {n:g} is not in the cited evidence")
    return issues


# ---- verification -------------------------------------------------------------------------

def _claim_payload(claims: list[Claim], by_id: dict) -> list[dict]:
    return [{"i": i, "text": c.text, "evidence": [by_id[e] for e in c.evidence_ids if e in by_id]}
            for i, c in enumerate(claims)]


def verify_claims(llm: GuardedLLM, claims: list[Claim], by_id: dict, injected: bool = False) -> list[dict]:
    results, to_check = [], []
    for idx, c in enumerate(claims):
        issues = check_claim(c, by_id)
        r = {"text": c.text, "original_text": None, "evidence_ids": c.evidence_ids, "confidence": c.confidence,
             "injected": injected, "rewritten": False, "checks": issues, "verdict": "unsupported", "reason": ""}
        if issues:
            r["reason"] = ("Failed the number and citation check: " + "; ".join(issues))[:280]
        else:
            to_check.append(idx)
        results.append(r)
    if to_check:
        sub = [claims[i] for i in to_check]
        out = llm.structured("verifier", VERIFIER, {"claims": _claim_payload(sub, by_id)}, Verdicts)
        got = {v.i: v for v in out.verdicts}
        for pos, idx in enumerate(to_check):
            v = got.get(pos)
            results[idx]["verdict"] = v.verdict if v else "unsupported"
            results[idx]["reason"] = (v.reason if v else "The verifier returned no verdict.")[:280]
    return results


def _rewrite_once(llm: GuardedLLM, results: list[dict], by_id: dict) -> None:
    bad = [i for i, r in enumerate(results) if r["verdict"] == "unsupported" and not r["injected"]]
    if not bad:
        return
    payload = {"claims": [{"i": k, "text": results[i]["text"], "reason": results[i]["reason"],
                           "evidence": [by_id[e] for e in results[i]["evidence_ids"] if e in by_id]}
                          for k, i in enumerate(bad)]}
    out = llm.structured("rewriter", REWRITER, payload, Rewrites)
    new, where = [], []
    for rw in out.rewrites:
        text = rw.text.strip()
        if 0 <= rw.i < len(bad) and text and text.upper() != "INSUFFICIENT":
            src = results[bad[rw.i]]
            new.append(_clean(Claim(text=text, evidence_ids=src["evidence_ids"], confidence=src["confidence"])))
            where.append(bad[rw.i])
    if not new:
        return
    for i, nr in zip(where, verify_claims(llm, new, by_id)):
        if nr["verdict"] != "unsupported":
            results[i].update(original_text=results[i]["text"], text=nr["text"], verdict=nr["verdict"],
                              reason=nr["reason"], rewritten=True, checks=nr["checks"])


def build_report(llm: GuardedLLM, findings: list[dict], context: dict) -> dict:
    """Run the whole pipeline and return claims with verdicts, notes, tool steps and stats."""
    steps: list[dict] = []
    if not findings:
        return {"claims": [], "hypotheses": [], "notes": ["No finding crossed a detection threshold."], "steps": steps,
                "stats": {"claims": 0, "supported": 0, "partial": 0, "insufficient": 0, "rewritten": 0, "first_pass_supported": 0}}
    by_id = {f["id"]: f for f in findings}
    notes = investigate(llm, EvidenceStore(findings, context), steps)
    claims, hyps = write_report(llm, findings, notes)
    results = verify_claims(llm, claims, by_id)
    for r in results:
        r["first_verdict"] = r["verdict"]
    steps.append({"step": "verify", "action": "verify_claims", "id": None})
    _rewrite_once(llm, results, by_id)
    for r in results:
        if r["verdict"] == "unsupported":
            r.update(original_text=r["original_text"] or r["text"], verdict="insufficient_evidence",
                     text="Insufficient evidence to state this claim.")
    count = lambda v: sum(r["verdict"] == v for r in results)
    return {"claims": results, "hypotheses": clean_hypotheses(hyps, by_id), "notes": notes, "steps": steps,
            "stats": {"claims": len(results), "supported": count("supported"), "partial": count("partial"),
                      "insufficient": count("insufficient_evidence"), "rewritten": sum(r["rewritten"] for r in results),
                      "first_pass_supported": sum(r["first_verdict"] == "supported" for r in results)}}


def redteam_claims(findings: list[dict]) -> list[Claim]:
    """Fabricated claims. The first three are stopped by code alone; the rest need the verifier to reason."""
    if not findings:
        return []
    f, ids = findings[0], [x["id"] for x in findings]
    s_items = [x for x in findings if x["id"].startswith("S-")]
    claims = [
        Claim(text="The model's false-positive rate doubled last month.", evidence_ids=[]),
        Claim(text=f"The {f['metric']} for {f['kind'].replace('_', ' ')} reached {round(f['value'] * 3 + 0.37, 2)}.",
              evidence_ids=[f["id"]]),
        Claim(text="Customer fraud losses rose by NGN 4.2 billion this quarter.", evidence_ids=[f["id"]]),
        Claim(text="The drift was caused by the fraud team changing its detection rules.", evidence_ids=[f["id"]]),
        Claim(text="No meaningful drift was detected in any channel or segment.", evidence_ids=ids[:2]),
        Claim(text=f"The {f['metric']} of {f['value']} is below the {f['threshold']} threshold.", evidence_ids=[f["id"]]),
        Claim(text="The shift began in the second week of the current window.", evidence_ids=[f["id"]]),
    ]
    ratio = next((x for x in findings if x["id"].startswith("E-") and str(x["metric"]).endswith("ratio")), None)
    if ratio:
        kind = ratio["kind"].replace("_", " ")
        claims.append(Claim(text=f"The {kind} for {ratio['segment']} decreased by a factor of {ratio['value']}.",
                            evidence_ids=[ratio["id"]]))
        other = next((s["segment"] for s in s_items if s["segment"] != ratio["segment"]), None)
        if other:
            claims.append(Claim(text=f"The {kind} with ratio {ratio['value']} occurred in {other}.",
                                evidence_ids=[ratio["id"]]))
    if s_items:
        claims.append(Claim(text="Amount drift was confined to a single channel.", evidence_ids=[s_items[0]["id"]]))
    return claims


def redteam(llm: GuardedLLM, findings: list[dict]) -> dict:
    by_id = {f["id"]: f for f in findings}
    results = verify_claims(llm, redteam_claims(findings), by_id, injected=True)
    return {"claims": results, "caught": sum(r["verdict"] == "unsupported" for r in results), "total": len(results)}
