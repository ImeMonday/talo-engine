import json

import pytest
from fastapi.testclient import TestClient

from talo.api import create_app
from talo.llm.base import LLMResult
from talo.synth import generate

RENAME = {"timestamp": "txn_time", "channel": "chan", "amount": "amt_ngn", "model_score": "risk",
          "decision_flagged": "alerted", "label_delay_days": "label_lag"}
MAPPING = {v: k for k, v in RENAME.items()}
REPLY = json.dumps({"mappings": [{"source": s, "target": t, "confidence": 0.95, "reason": "match"} for s, t in MAPPING.items()]
                    + [{"source": "account_id", "target": None, "confidence": 0.9, "reason": "identifier"}]})


class Fake:
    name, model = "fake", "fake-1"

    def chat(self, system, user):
        return LLMResult(REPLY, self.model, self.name, 12, 8, 5)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return create_app(db_url=f"sqlite:///{tmp_path_factory.mktemp('db') / 'l.db'}", secret="s" * 40,
                      llm_factory=lambda mode: Fake())


@pytest.fixture(scope="module")
def messy_csv():
    df = generate(n=40_000)[0].rename(columns=RENAME)
    df["account_id"] = [f"ACC{i:07d}" for i in range(len(df))]
    return df.to_csv(index=False).encode()


def signup(app, email):
    c = TestClient(app)
    assert c.post("/api/auth/signup", json={"email": email, "password": "correct-horse-1", "org_name": "Org"}).status_code == 201
    return c


def post(c, path, data, **form):
    return c.post(path, data=form, files={"file": ("log.csv", data, "text/csv")},
                  headers={"X-CSRF-Token": c.cookies.get("talo_csrf")})


def test_mapping_preview_ledger_and_audit_with_mapping(app, messy_csv):
    c, other = signup(app, "m1@bank.ng"), signup(app, "m2@bank.ng")
    r = post(c, "/api/audits/mapping-preview", messy_csv)
    assert r.status_code == 200, r.text
    assert r.json()["issues"] == [] and r.json()["model"] == "fake-1"

    ledger = c.get("/api/egress").json()
    assert ledger["calls"] == 1 and ledger["raw_rows_sent"] == 0
    detail = c.get(f"/api/egress/{ledger['events'][0]['id']}").json()
    assert "ACC0" not in detail["payload"] and detail["purpose"] == "schema_mapping"

    assert other.get("/api/egress").json()["calls"] == 0                      # tenant scoped
    assert other.get(f"/api/egress/{ledger['events'][0]['id']}").status_code == 404

    audit = post(c, "/api/audits", messy_csv, name="Messy export", mapping=json.dumps(MAPPING))
    assert audit.status_code == 201, audit.text
    assert {(f["kind"], f["segment"]) for f in audit.json()["findings"]} == {
        ("amount_drift", "all"), ("score_drift", "all"), ("alert_rate_shift", "channel=ussd"), ("label_delay", "all")}


def test_bad_mapping_is_rejected(app, messy_csv):
    c = signup(app, "m3@bank.ng")
    r = post(c, "/api/audits", messy_csv, name="x", mapping=json.dumps({"risk": "amount"}))
    assert r.status_code == 422


def test_sandbox_has_a_model_call_limit(app, messy_csv, monkeypatch):
    import talo.api as api_mod
    monkeypatch.setattr(api_mod, "SANDBOX_CALL_LIMIT", 1)
    c = TestClient(app)
    c.post("/api/auth/demo")
    assert post(c, "/api/audits/mapping-preview", messy_csv).status_code == 200
    assert post(c, "/api/audits/mapping-preview", messy_csv).status_code == 429


def test_no_provider_configured_is_503(tmp_path, messy_csv):
    c = signup(create_app(db_url=f"sqlite:///{tmp_path / 'n.db'}", secret="s" * 40, llm_factory=lambda mode: None), "n@bank.ng")
    assert post(c, "/api/audits/mapping-preview", messy_csv).status_code == 503


def test_mode_setting(app):
    c = signup(app, "m4@bank.ng")
    h = {"X-CSRF-Token": c.cookies.get("talo_csrf")}
    assert c.post("/api/settings/mode", json={"mode": "open_weights"}, headers=h).status_code == 200
    assert c.get("/api/settings").json()["llm_mode"] == "open_weights"
    assert c.post("/api/settings/mode", json={"mode": "bogus"}, headers=h).status_code == 422


def test_settings_report_the_model_in_use(app):
    c = signup(app, "m5@bank.ng")
    s = c.get("/api/settings").json()
    assert s["models"]["cloud"] == "fake-1" and s["available"]["cloud"] is True
