"""Egress guard: what a model may receive, and a record of everything it did receive."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import warnings

import pandas as pd

from .base import EgressViolation, LLMError, StructuredOutputError

MAX_STR, MAX_LIST, MAX_CATEGORIES = 300, 100, 20


def _num(x):
    x = float(x)
    return round(x, 4) if math.isfinite(x) else None


def schema_payload(df: pd.DataFrame) -> dict:
    """Column metadata and aggregates only. Never individual rows or identifiers."""
    cols = []
    for name in df.columns:
        s = df[name]
        info = {"name": str(name)[:80], "dtype": str(s.dtype),
                "null_rate": _num(s.isna().mean()), "n_unique": int(s.nunique())}
        if pd.api.types.is_bool_dtype(s):
            info["categories"] = sorted(str(v) for v in s.dropna().unique())
        elif pd.api.types.is_numeric_dtype(s):
            info.update(min=_num(s.min()), median=_num(s.median()), max=_num(s.max()))
        else:
            sample = s.dropna().astype(str).head(500)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                info["parse_rate_datetime"] = _num(pd.to_datetime(sample, errors="coerce").notna().mean()) if len(sample) else 0.0
            info["parse_rate_numeric"] = _num(pd.to_numeric(sample, errors="coerce").notna().mean()) if len(sample) else 0.0
            if info["n_unique"] <= MAX_CATEGORIES:  # label-like columns only (channel, region)
                info["categories"] = sorted(str(v) for v in s.dropna().unique())
        cols.append(info)
    return {"n_rows": int(len(df)), "columns": cols}


def check_payload(payload, depth: int = 0) -> None:
    """Allow only small, plain JSON structures. Anything that could be a row dump is blocked."""
    if depth > 6:
        raise EgressViolation("Payload is nested too deeply")
    if isinstance(payload, dict):
        for k, v in payload.items():
            if not isinstance(k, str):
                raise EgressViolation("Payload keys must be strings")
            check_payload(v, depth + 1)
    elif isinstance(payload, list):
        if len(payload) > MAX_LIST:
            raise EgressViolation(f"List of {len(payload)} items exceeds the limit of {MAX_LIST}")
        for v in payload:
            check_payload(v, depth + 1)
    elif isinstance(payload, str):
        if len(payload) > MAX_STR:
            raise EgressViolation("A string in the payload is too long to be metadata")
    elif isinstance(payload, bool) or payload is None:
        return
    elif isinstance(payload, (int, float)):
        if isinstance(payload, float) and not math.isfinite(payload):
            raise EgressViolation("Non-finite number in payload")
    else:
        raise EgressViolation(f"Disallowed payload type: {type(payload).__name__}")


def extract_json(text: str) -> str:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("no JSON object in the reply")
    return text[start:end + 1]


class GuardedLLM:
    """Every call goes through check_payload and is recorded, success or failure."""

    def __init__(self, provider, record):
        self.provider, self.record = provider, record

    def structured(self, purpose: str, system: str, payload: dict, model_cls, attempts: int = 2):
        check_payload(payload)
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        digest = hashlib.sha256(body.encode()).hexdigest()
        user, last = body, None
        for _ in range(attempts):
            sent = {"purpose": purpose, "provider": self.provider.name, "model": self.provider.model,
                    "bytes_out": len((system + user).encode()), "raw_rows": 0,
                    "payload_sha256": digest, "payload_json": user}
            logging.getLogger("uvicorn.error").info("model call [%s] started", purpose)
            try:
                res = self.provider.chat(system, user)
            except LLMError:
                self.record({**sent, "status": "error", "prompt_tokens": 0,
                             "completion_tokens": 0, "latency_ms": 0})
                raise
            logging.getLogger("uvicorn.error").info(
                "model call [%s] %s/%s: %d ms, %d tokens in, %d out, 0 raw rows",
                purpose, self.provider.name, self.provider.model, res.latency_ms,
                res.prompt_tokens, res.completion_tokens)
            self.record({**sent, "status": "ok", "prompt_tokens": res.prompt_tokens,
                         "completion_tokens": res.completion_tokens, "latency_ms": res.latency_ms})
            try:
                return model_cls.model_validate_json(extract_json(res.text))
            except ValueError as exc:  # includes pydantic ValidationError
                last = exc
                user = body + "\n\nYour previous reply was invalid (" + str(exc)[:200] + "). Reply with valid JSON only."
        raise StructuredOutputError(
            f"Model did not return valid {model_cls.__name__} after {attempts} attempts: {last}")
