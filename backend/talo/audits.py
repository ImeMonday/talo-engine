"""CSV validation and audit execution. Uploads are untrusted input."""
from __future__ import annotations

import io

import pandas as pd

REQUIRED = ["timestamp", "channel", "amount", "model_score", "decision_flagged", "label_delay_days"]
MAX_BYTES, MAX_ROWS, MIN_ROWS = 10 * 1024 * 1024, 500_000, 1_000
ALLOWED_TYPES = {"text/csv", "application/vnd.ms-excel", "text/plain"}


def read_csv_bytes(data: bytes) -> pd.DataFrame:
    """Read an untrusted CSV with a row cap; raise ValueError if unreadable."""
    try:
        raw = pd.read_csv(io.BytesIO(data), nrows=MAX_ROWS + 1)
    except Exception as exc:
        raise ValueError("Could not read the file as CSV") from exc
    if len(raw) > MAX_ROWS:
        raise ValueError(f"Too many rows (maximum {MAX_ROWS:,})")
    return raw


TRUE, FALSE = {"true", "1", "yes", "y", "t"}, {"false", "0", "no", "n", "f"}


def canonicalise(raw: pd.DataFrame) -> pd.DataFrame:
    """Validate and coerce a frame that already uses the canonical column names."""
    missing = [c for c in REQUIRED if c not in raw.columns]
    if missing:
        raise ValueError("Missing required columns: " + ", ".join(missing))
    if len(raw) < MIN_ROWS:
        raise ValueError(f"Too few rows (minimum {MIN_ROWS:,})")
    df = raw[REQUIRED].copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    for col in ("amount", "model_score", "label_delay_days"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    if df[["timestamp", "amount", "model_score", "label_delay_days"]].isna().any().any():
        raise ValueError("Some timestamp or numeric values could not be parsed")
    if not df["model_score"].between(0, 1).all():
        raise ValueError("model_score must be between 0 and 1")
    flag = df["decision_flagged"].astype(str).str.lower()
    if not flag.isin(TRUE | FALSE).all():
        raise ValueError("decision_flagged must be true/false (or 1/0, yes/no)")
    df["decision_flagged"] = flag.isin(TRUE)
    df["channel"] = df["channel"].astype(str)
    return df


def parse_csv(data: bytes) -> pd.DataFrame:
    return canonicalise(read_csv_bytes(data))


def default_cutoff(df: pd.DataFrame) -> pd.Timestamp:
    """Baseline = first ~two thirds of the time range, current = the rest."""
    return pd.Timestamp(df.timestamp.quantile(0.67)).normalize()
