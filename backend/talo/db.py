"""SQLAlchemy models. Every tenant-owned row carries org_id."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_mode: Mapped[str] = mapped_column(String(20), default="cloud")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)


class Audit(Base):
    __tablename__ = "audits"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    n_rows: Mapped[int]
    cutoff: Mapped[str] = mapped_column(String(32))
    findings_json: Mapped[str] = mapped_column(Text)
    timeline_json: Mapped[str] = mapped_column(Text, default="[]")
    segments_json: Mapped[str] = mapped_column(Text, default="[]")


class Report(Base):
    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    audit_id: Mapped[int] = mapped_column(ForeignKey("audits.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    report_json: Mapped[str] = mapped_column(Text)
    redteam_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class EgressEvent(Base):
    """One outbound model call: exactly what left the environment."""
    __tablename__ = "egress_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    purpose: Mapped[str] = mapped_column(String(60))
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20))
    bytes_out: Mapped[int]
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[int] = mapped_column(default=0)
    raw_rows: Mapped[int] = mapped_column(default=0)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text)


def _migrate(engine) -> None:
    """Add columns introduced after a database was first created, so nobody has to delete talo.db."""
    from sqlalchemy import inspect, text
    cols = {c["name"] for c in inspect(engine).get_columns("audits")}
    with engine.begin() as conn:
        if "segments_json" not in cols:
            conn.execute(text("ALTER TABLE audits ADD COLUMN segments_json TEXT DEFAULT '[]'"))


def make_session_factory(url: str) -> sessionmaker:
    args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    engine = create_engine(url, connect_args=args)
    Base.metadata.create_all(engine)
    _migrate(engine)
    return sessionmaker(engine, expire_on_commit=False)
