"""Talo HTTP API: auth, tenant-scoped audits, read-only demo workspace."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import secrets
from typing import Literal
from dataclasses import asdict

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import audits
from .auth import Auth, LoginLimiter, check_csrf, ph_hash, ph_verify
from .db import Audit, EgressEvent, Organization, User, make_session_factory
from .agents import build_report, redteam
from .evidence import daily_series, detect, segment_breakdown, segment_evidence
from .llm.base import EgressViolation, LLMError
from .llm.guard import GuardedLLM
from .llm.providers import provider_for_mode
from .reports import (UsageMeter, audit_context, audit_evidence, fingerprint, latest_report,
                      make_recorder, report_out, save_report, token_cost)
from .mapper import apply_mapping, propose_mapping, validate_mapping
from .synth import CUTOFF, generate

log = logging.getLogger("talo")
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


SANDBOX_PREFIX, SANDBOX_CALL_LIMIT = "sandbox-", 60


def is_sandbox(user: User) -> bool:
    return user.email.startswith(SANDBOX_PREFIX)


class LoginIn(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ModeIn(BaseModel):
    mode: Literal["cloud", "open_weights"]


class SignupIn(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=10, max_length=128)
    org_name: str = Field(min_length=2, max_length=120)


def seed_demo(session_factory) -> None:
    """Create the read-only demo workspace with one audit on synthetic data."""
    with session_factory() as s:
        if s.scalar(select(Organization).where(Organization.is_demo)):
            audit = s.scalar(select(Audit).join(Organization, Audit.org_id == Organization.id)
                             .where(Organization.is_demo).order_by(Audit.id))
            if audit and audit.segments_json in (None, "", "[]"):  # created before segments existed
                audit.segments_json = json.dumps(segment_breakdown(generate()[0], CUTOFF))
                s.commit()
            return
        org = Organization(name="Demo workspace (Sample data)", is_demo=True)
        s.add(org)
        s.flush()
        user = User(email="demo@talo.demo", password_hash=ph_hash(secrets.token_urlsafe(32)),
                    org_id=org.id, is_demo=True)
        s.add(user)
        s.flush()
        df, _ = generate()
        s.add(Audit(org_id=org.id, created_by=user.id, name="Retail fraud model v4, Q3 (Sample data)",
                    n_rows=len(df), cutoff=str(CUTOFF.date()),
                    findings_json=json.dumps([asdict(f) for f in detect(df)]),
                    timeline_json=json.dumps(daily_series(df, CUTOFF)),
                    segments_json=json.dumps(segment_breakdown(df, CUTOFF))))
        s.commit()


def create_app(db_url: str | None = None, secret: str | None = None, llm_factory=None) -> FastAPI:
    db_url = db_url or os.getenv("TALO_DB_URL", "sqlite:///./talo.db")
    secret = secret or os.getenv("TALO_JWT_SECRET")
    if not secret:
        key_file = Path(".talo_secret")  # development convenience: restarts do not sign everyone out
        if key_file.exists():
            secret = key_file.read_text().strip()
        else:
            secret = secrets.token_urlsafe(48)
            key_file.write_text(secret)
        log.warning("TALO_JWT_SECRET not set: using the secret in .talo_secret. Set TALO_JWT_SECRET in production.")
    session_factory = make_session_factory(db_url)
    seed_demo(session_factory)
    auth = Auth(secret, os.getenv("TALO_SECURE_COOKIES", "0") == "1")
    limiter = LoginLimiter()
    demo_limiter = LoginLimiter(limit=20, window=3600)
    llm_factory = llm_factory or provider_for_mode

    app = FastAPI(title="Talo API")
    app.add_middleware(
        CORSMiddleware, allow_credentials=True, allow_methods=["GET", "POST"],
        allow_origins=[o for o in os.getenv("TALO_CORS_ORIGINS", "http://localhost:3000").split(",") if o],
        allow_headers=["Content-Type", "X-CSRF-Token"])

    def db():
        with session_factory() as s:
            yield s

    def current_user(request: Request, s: Session = Depends(db)) -> User:
        uid = auth.user_id(request)
        user = s.get(User, uid) if uid else None
        if not user:
            raise HTTPException(401, "Not authenticated")
        return user

    def user_out(user: User, s: Session) -> dict:
        org = s.get(Organization, user.org_id)
        return {"id": user.id, "email": user.email, "is_demo": user.is_demo, "sandbox": is_sandbox(user),
                "org": {"id": org.id, "name": org.name}}

    def audit_out(a: Audit, full: bool = True) -> dict:
        findings = json.loads(a.findings_json)
        out = {"id": a.id, "name": a.name, "created_at": a.created_at.isoformat(),
               "n_rows": a.n_rows, "cutoff": a.cutoff, "n_findings": len(findings)}
        if full:
            out["findings"] = findings
            out["timeline"] = json.loads(a.timeline_json)
            out["segments"] = json.loads(a.segments_json or "[]")
            out["evidence"] = findings + segment_evidence(out["segments"], findings)
        return out

    def enforce_quota(user: User, s: Session) -> None:
        """Sandbox visitors share our model credits, so each gets a limited number of calls."""
        if is_sandbox(user):
            used = s.scalar(select(func.count()).select_from(EgressEvent).where(EgressEvent.org_id == user.org_id))
            if used >= SANDBOX_CALL_LIMIT:
                raise HTTPException(429, "Sandbox model-call limit reached. Create an account to keep going.")

    def read_upload(file: UploadFile) -> bytes:
        if file.content_type not in audits.ALLOWED_TYPES:
            raise HTTPException(415, "Upload a CSV file")
        data = file.file.read(audits.MAX_BYTES + 1)
        if len(data) > audits.MAX_BYTES:
            raise HTTPException(413, "File too large (maximum 10 MB)")
        return data

    def egress_out(e: EgressEvent, full: bool = False) -> dict:
        out = {"id": e.id, "created_at": e.created_at.isoformat(), "purpose": e.purpose,
               "provider": e.provider, "model": e.model, "status": e.status, "bytes_out": e.bytes_out,
               "prompt_tokens": e.prompt_tokens, "completion_tokens": e.completion_tokens,
               "latency_ms": e.latency_ms, "raw_rows": e.raw_rows, "payload_sha256": e.payload_sha256}
        if full:
            out["payload"] = e.payload_json
        return out

    @app.post("/api/auth/signup", status_code=201)
    def signup(body: SignupIn, response: Response, s: Session = Depends(db)):
        email = body.email.lower()
        if s.scalar(select(User).where(User.email == email)):
            raise HTTPException(409, "An account with this email already exists")
        org = Organization(name=body.org_name.strip())
        s.add(org)
        s.flush()
        user = User(email=email, password_hash=ph_hash(body.password), org_id=org.id)
        s.add(user)
        s.commit()
        auth.issue(response, user.id)
        return user_out(user, s)

    @app.post("/api/auth/login")
    def login(body: LoginIn, request: Request, response: Response, s: Session = Depends(db)):
        key = f"{request.client.host if request.client else '?'}|{body.email.lower()}"
        limiter.check(key)
        user = s.scalar(select(User).where(User.email == body.email.lower()))
        if not ph_verify(user.password_hash if user else None, body.password):
            limiter.fail(key)
            raise HTTPException(401, "Incorrect email or password")
        limiter.reset(key)
        auth.issue(response, user.id)
        return user_out(user, s)

    @app.post("/api/auth/demo")
    def demo_login(request: Request, response: Response, s: Session = Depends(db)):
        """Each visitor gets a private sandbox that starts with a copy of the sample audit."""
        ip = request.client.host if request.client else "?"
        demo_limiter.check(ip)
        demo_limiter.fail(ip)
        template_user = s.scalar(select(User).where(User.is_demo))
        template = s.scalar(select(Audit).where(Audit.org_id == template_user.org_id).order_by(Audit.id))
        org = Organization(name="Sandbox (Sample data)")
        s.add(org)
        s.flush()
        user = User(email=f"{SANDBOX_PREFIX}{secrets.token_hex(6)}@talo.demo",
                    password_hash=ph_hash(secrets.token_urlsafe(32)), org_id=org.id)
        s.add(user)
        s.flush()
        s.add(Audit(org_id=org.id, created_by=user.id, name=template.name, n_rows=template.n_rows,
                    cutoff=template.cutoff, findings_json=template.findings_json,
                    timeline_json=template.timeline_json, segments_json=template.segments_json))
        s.commit()
        auth.issue(response, user.id)
        return user_out(user, s)

    @app.post("/api/auth/logout")
    def logout(response: Response):
        auth.clear(response)
        return {"ok": True}

    @app.api_route("/api/health", methods=["GET", "HEAD"])
    def health():
        return {"ok": True}

    @app.get("/api/me")
    def me(user: User = Depends(current_user), s: Session = Depends(db)):
        return user_out(user, s)

    @app.get("/api/audits")
    def list_audits(user: User = Depends(current_user), s: Session = Depends(db)):
        rows = s.scalars(select(Audit).where(Audit.org_id == user.org_id).order_by(Audit.id.desc()))
        return [audit_out(a, full=False) for a in rows]

    @app.get("/api/audits/{audit_id}")
    def get_audit(audit_id: int, user: User = Depends(current_user), s: Session = Depends(db)):
        a = s.scalar(select(Audit).where(Audit.id == audit_id, Audit.org_id == user.org_id))
        if not a:
            raise HTTPException(404, "Audit not found")
        return audit_out(a)

    @app.post("/api/audits", status_code=201, dependencies=[Depends(check_csrf)])
    def create_audit(file: UploadFile = File(...), name: str = Form(..., min_length=1, max_length=120),
                     cutoff: str | None = Form(None), mapping: str | None = Form(None),
                     user: User = Depends(current_user),
                     s: Session = Depends(db)):
        if user.is_demo:
            raise HTTPException(403, "The demo workspace is read-only")
        data = read_upload(file)
        try:
            raw = audits.read_csv_bytes(data)
            df = apply_mapping(raw, json.loads(mapping)) if mapping else audits.canonicalise(raw)
            cut = pd.Timestamp(cutoff) if cutoff else audits.default_cutoff(df)
            findings = detect(df, cut)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        a = Audit(org_id=user.org_id, created_by=user.id, name=name.strip(), n_rows=len(df),
                  cutoff=str(cut.date()), findings_json=json.dumps([asdict(f) for f in findings]),
                  timeline_json=json.dumps(daily_series(df, cut)),
                  segments_json=json.dumps(segment_breakdown(df, cut)))
        s.add(a)
        s.commit()
        return audit_out(a)

    @app.get("/api/settings")
    def get_settings(user: User = Depends(current_user), s: Session = Depends(db)):
        org = s.get(Organization, user.org_id)
        providers = {m: llm_factory(m) for m in ("cloud", "open_weights")}
        return {"llm_mode": org.llm_mode, "available": {m: p is not None for m, p in providers.items()},
                "models": {m: (p.model if p else None) for m, p in providers.items()}}

    @app.post("/api/settings/mode", dependencies=[Depends(check_csrf)])
    def set_mode(body: ModeIn, user: User = Depends(current_user), s: Session = Depends(db)):
        if user.is_demo:
            raise HTTPException(403, "The demo workspace is read-only")
        s.get(Organization, user.org_id).llm_mode = body.mode
        s.commit()
        return {"llm_mode": body.mode}

    @app.post("/api/audits/mapping-preview", dependencies=[Depends(check_csrf)])
    def mapping_preview(file: UploadFile = File(...), user: User = Depends(current_user),
                        s: Session = Depends(db)):
        if user.is_demo:
            raise HTTPException(403, "The demo workspace is read-only")
        mode = s.get(Organization, user.org_id).llm_mode
        provider = llm_factory(mode)
        if provider is None:
            raise HTTPException(503, f"No model is configured for '{mode}' mode (set MODEL_API_KEY or BASETEN_API_KEY)")
        enforce_quota(user, s)
        try:
            raw = audits.read_csv_bytes(read_upload(file))
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        org_id = user.org_id

        def record(event: dict) -> None:
            with session_factory() as ss:
                ss.add(EgressEvent(org_id=org_id, **event))
                ss.commit()

        try:
            proposal = propose_mapping(GuardedLLM(provider, record), raw)
        except EgressViolation as exc:
            raise HTTPException(422, f"Blocked by the egress guard: {exc}")
        except LLMError as exc:
            raise HTTPException(502, f"The model call failed: {exc}")
        chosen = {m.source: m.target for m in proposal if m.target}
        return {"provider": provider.name, "model": provider.model,
                "proposal": [m.model_dump() for m in proposal], "issues": validate_mapping(raw, chosen)}

    @app.get("/api/egress")
    def egress(user: User = Depends(current_user), s: Session = Depends(db)):
        rows = s.scalars(select(EgressEvent).where(EgressEvent.org_id == user.org_id)
                         .order_by(EgressEvent.id.desc())).all()
        return {"raw_rows_sent": sum(r.raw_rows for r in rows), "calls": len(rows),
                "cost_usd": round(sum(token_cost(r.prompt_tokens, r.completion_tokens) for r in rows), 4),
                "events": [egress_out(r) for r in rows]}

    @app.get("/api/egress/{event_id}")
    def egress_detail(event_id: int, user: User = Depends(current_user), s: Session = Depends(db)):
        e = s.scalar(select(EgressEvent).where(EgressEvent.id == event_id, EgressEvent.org_id == user.org_id))
        if not e:
            raise HTTPException(404, "Event not found")
        return egress_out(e, full=True)

    sample_cache: dict[bool, bytes] = {}

    @app.get("/api/sample.csv")
    def sample_csv(messy: bool = False):
        """Synthetic decision log to download. messy=1 renames the columns so the model mapping can be tried."""
        if messy not in sample_cache:
            cols = ["timestamp", "channel", "amount", "model_score", "decision_flagged", "label_delay_days"]
            df = generate(n=40_000)[0][cols]
            if messy:
                df = df.rename(columns={"timestamp": "txn_time", "channel": "chan", "amount": "amt_ngn",
                                        "model_score": "risk", "decision_flagged": "alerted",
                                        "label_delay_days": "label_lag"})
                df.insert(0, "account_id", [f"ACC{i:07d}" for i in range(len(df))])
            sample_cache[messy] = df.to_csv(index=False).encode()
        name = "talo-sample-messy.csv" if messy else "talo-sample.csv"
        return Response(sample_cache[messy], media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    def audit_or_404(audit_id: int, user: User, s: Session) -> Audit:
        a = s.scalar(select(Audit).where(Audit.id == audit_id, Audit.org_id == user.org_id))
        if not a:
            raise HTTPException(404, "Audit not found")
        return a

    def guarded_llm(user: User, s: Session):
        mode = s.get(Organization, user.org_id).llm_mode
        provider = llm_factory(mode)
        if provider is None:
            raise HTTPException(503, f"No model is configured for '{mode}' mode (set MODEL_API_KEY or BASETEN_API_KEY)")
        enforce_quota(user, s)
        meter = UsageMeter(make_recorder(session_factory, user.org_id))
        return provider, GuardedLLM(provider, meter.record), meter

    @app.get("/api/audits/{audit_id}/report")
    def get_report(audit_id: int, user: User = Depends(current_user), s: Session = Depends(db)):
        audit_or_404(audit_id, user, s)
        r = latest_report(s, user.org_id, audit_id)
        if not r:
            raise HTTPException(404, "No report yet")
        return report_out(r)

    @app.post("/api/audits/{audit_id}/report", dependencies=[Depends(check_csrf)])
    def create_report(audit_id: int, user: User = Depends(current_user), s: Session = Depends(db)):
        if user.is_demo:
            raise HTTPException(403, "The demo workspace is read-only")
        audit = audit_or_404(audit_id, user, s)
        provider, llm, meter = guarded_llm(user, s)
        findings = audit_evidence(audit)
        try:
            report = build_report(llm, findings, audit_context(audit, findings))
        except EgressViolation as exc:
            raise HTTPException(422, f"Blocked by the egress guard: {exc}")
        except LLMError as exc:
            raise HTTPException(502, f"The model call failed: {exc}")
        report["usage"], report["fingerprint"] = meter.summary(), fingerprint(findings, report["claims"])
        return report_out(save_report(s, user.org_id, audit.id, provider, report))

    @app.post("/api/audits/{audit_id}/report/redteam", dependencies=[Depends(check_csrf)])
    def run_redteam(audit_id: int, user: User = Depends(current_user), s: Session = Depends(db)):
        if user.is_demo:
            raise HTTPException(403, "The demo workspace is read-only")
        audit = audit_or_404(audit_id, user, s)
        row = latest_report(s, user.org_id, audit_id)
        if not row:
            raise HTTPException(409, "Generate the report first")
        _, llm, meter = guarded_llm(user, s)
        try:
            rt = redteam(llm, audit_evidence(audit))
            rt["usage"] = meter.summary()
            row.redteam_json = json.dumps(rt)
        except EgressViolation as exc:
            raise HTTPException(422, f"Blocked by the egress guard: {exc}")
        except LLMError as exc:
            raise HTTPException(502, f"The model call failed: {exc}")
        s.commit()
        return report_out(row)

    @app.get("/api/evals")
    def evals(user: User = Depends(current_user)):
        path = Path(os.getenv("TALO_EVALS_PATH", "evals/results.json"))
        return json.loads(path.read_text()) if path.exists() else {"available": False}

    static = Path(os.getenv("TALO_STATIC_DIR", "static"))
    if (static / "index.html").exists():  # production: this one service also serves the built website
        if (static / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=static / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def website(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "Not found")
            file = (static / path).resolve()
            if path and file.is_file() and static.resolve() in file.parents:
                return FileResponse(file)
            return FileResponse(static / "index.html")  # client-side routes fall back to the app

    return app