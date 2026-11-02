import json
import sys

from talo import evals
from talo.llm.base import LLMResult


class Fake:
    name, model = "fake", "fake-1"

    def __init__(self):
        self.step = 0

    def chat(self, system, user):
        payload = json.loads(user.split("\n\n")[0])
        if "investigator" in system:
            self.step += 1
            reply = {"action": "list_findings"} if self.step == 1 else {"action": "finish", "notes": ["done"]}
        elif "findings section" in system:
            f = payload["findings"][0]
            reply = {"claims": [{"text": f"{f['metric']} was {f['value']}.", "evidence_ids": [f["id"]], "confidence": 0.9}]}
        elif "adversarial auditor" in system:
            reply = {"verdicts": [{"i": c["i"], "verdict": "supported", "reason": "ok"} for c in payload["claims"]]}
        else:
            reply = {"rewrites": []}
        return LLMResult(json.dumps(reply), self.model, self.name, 7, 3, 5)


def test_evals_cli_writes_results(tmp_path, monkeypatch):
    out = tmp_path / "results.json"
    monkeypatch.setenv("TALO_EVALS_PATH", str(out))
    monkeypatch.setattr(evals, "provider_for_mode", lambda mode, model=None: Fake())
    monkeypatch.setattr(sys, "argv", ["evals", "--models", "fake-1", "--seeds", "1"])
    evals.main()
    data = json.loads(out.read_text())
    assert data["detection"]["recall"] == 1.0 and data["detection"]["clean_false_positives"] == 0
    p = data["providers"][0]
    assert p["model"] == "fake-1" and p["audits"] == 1
    assert p["redteam_total"] == 10 and p["redteam_caught"] == 3  # code alone stops three of the ten
