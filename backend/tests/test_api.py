import pytest
from fastapi.testclient import TestClient

from talo.api import create_app
from talo.synth import generate


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    return create_app(db_url=f"sqlite:///{tmp_path_factory.mktemp('db') / 't.db'}", secret="s" * 40)


@pytest.fixture(scope="module")
def csv_bytes():
    return generate(n=40_000)[0].to_csv(index=False).encode()


def signup(app, email, org="Acme Bank"):
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": email, "password": "correct-horse-1", "org_name": org})
    assert r.status_code == 201, r.text
    return c


def csrf(c):
    return {"X-CSRF-Token": c.cookies.get("talo_csrf")}


def upload(c, data, name="Q3 audit", headers=True):
    return c.post("/api/audits", data={"name": name}, files={"file": ("log.csv", data, "text/csv")},
                  headers=csrf(c) if headers else {})


def test_signup_sets_httponly_session_and_me_works(app):
    c = TestClient(app)
    r = c.post("/api/auth/signup", json={"email": "a@bank.ng", "password": "correct-horse-1", "org_name": "A Bank"})
    cookies = r.headers.get_list("set-cookie")
    assert any("talo_session" in h and "httponly" in h.lower() for h in cookies)
    assert c.get("/api/me").json()["org"]["name"] == "A Bank"


def test_weak_password_rejected(app):
    r = TestClient(app).post("/api/auth/signup", json={"email": "w@bank.ng", "password": "short", "org_name": "W Bank"})
    assert r.status_code == 422


def test_unauthenticated_is_401(app):
    assert TestClient(app).get("/api/audits").status_code == 401


def test_upload_requires_csrf_then_finds_all_issues(app, csv_bytes):
    c = signup(app, "b@bank.ng")
    assert upload(c, csv_bytes, headers=False).status_code == 403
    r = upload(c, csv_bytes)
    assert r.status_code == 201, r.text
    found = {(f["kind"], f["segment"]) for f in r.json()["findings"]}
    assert found == {("amount_drift", "all"), ("score_drift", "all"),
                     ("alert_rate_shift", "channel=ussd"), ("label_delay", "all")}


def test_tenant_isolation(app, csv_bytes):
    a, b = signup(app, "iso-a@bank.ng", "Org A"), signup(app, "iso-b@bank.ng", "Org B")
    audit_id = upload(a, csv_bytes).json()["id"]
    assert b.get(f"/api/audits/{audit_id}").status_code == 404
    assert audit_id not in [x["id"] for x in b.get("/api/audits").json()]
    assert a.get(f"/api/audits/{audit_id}").status_code == 200


def test_demo_gives_each_visitor_a_private_sandbox(app, csv_bytes):
    a, b = TestClient(app), TestClient(app)
    assert a.post("/api/auth/demo").json()["sandbox"] is True
    b.post("/api/auth/demo")
    assert len(a.get("/api/audits").json()) == 1      # starts with a copy of the sample audit
    assert upload(a, csv_bytes).status_code == 201    # sandbox visitors can upload
    assert len(a.get("/api/audits").json()) == 2
    assert len(b.get("/api/audits").json()) == 1      # and cannot see each other's data


def test_sample_downloads(app):
    c = TestClient(app)
    assert c.get("/api/sample.csv").text.startswith("timestamp,channel")
    assert c.get("/api/sample.csv?messy=1").text.startswith("account_id,txn_time")


def test_bad_uploads(app):
    c = signup(app, "c@bank.ng")
    assert upload(c, b"a,b\n1,2\n").status_code == 422
    assert upload(c, b"a,b\n" + b"1,2\n" * 3_000_000).status_code == 413


def test_login_failure_and_rate_limit(app):
    signup(app, "d@bank.ng")
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "d@bank.ng", "password": "wrong"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "d@bank.ng", "password": "correct-horse-1"}).status_code == 429


def test_segments_and_health(app, csv_bytes):
    c = signup(app, "seg@bank.ng")
    seg = {s["segment"]: s for s in upload(c, csv_bytes).json()["segments"]}
    assert seg["channel=ussd"]["alert_rate_current"] > 5 * seg["channel=ussd"]["alert_rate_baseline"]
    assert seg["channel=web"]["alert_rate_current"] < 2 * max(seg["channel=web"]["alert_rate_baseline"], 0.002)
    assert c.get("/api/health").json() == {"ok": True}


def test_migration_adds_segments_column(tmp_path):
    import sqlite3
    from talo.db import make_session_factory
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE audits (id INTEGER PRIMARY KEY, org_id INTEGER, created_by INTEGER, name TEXT, "
                "created_at DATETIME, n_rows INTEGER, cutoff TEXT, findings_json TEXT, timeline_json TEXT)")
    con.commit()
    con.close()
    make_session_factory(f"sqlite:///{path}")
    assert "segments_json" in [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(audits)")]


def test_audit_exposes_citable_segment_evidence(app, csv_bytes):
    c = signup(app, "ev@bank.ng")
    ev = upload(c, csv_bytes).json()["evidence"]
    assert [e["id"] for e in ev if e["id"].startswith("E-")] == ["E-001", "E-002", "E-003", "E-004"]
    assert len([e for e in ev if e["id"].startswith("S-")]) == 5


def test_serves_built_website_with_spa_fallback(tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>talo</html>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")
    (tmp_path / "favicon.svg").write_text("<svg/>")
    monkeypatch.setenv("TALO_STATIC_DIR", str(tmp_path))
    c = TestClient(create_app(db_url=f"sqlite:///{tmp_path / 'w.db'}", secret="s" * 40))
    assert c.get("/").text == "<html>talo</html>"
    assert c.get("/app/audits/7").text == "<html>talo</html>"
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/favicon.svg").text == "<svg/>"
    assert c.get("/api/health").json() == {"ok": True}      # the API still wins over the fallback
    assert c.get("/api/nope").status_code == 404
    assert "root:" not in c.get("/..%2f..%2f..%2fetc/passwd").text
