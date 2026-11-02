"""LLM-assisted column mapping, backed by deterministic validation. The user confirms it."""
from __future__ import annotations

import pandas as pd
from pydantic import BaseModel, Field

from . import audits
from .llm.guard import GuardedLLM, schema_payload

CANONICAL = {
    "timestamp": "when the model made the decision (datetime)",
    "channel": "channel of the transaction, e.g. mobile_app, ussd, web, pos, agent",
    "amount": "transaction amount (number)",
    "model_score": "the fraud model's risk score between 0 and 1",
    "decision_flagged": "whether the model flagged or alerted on the transaction (true/false)",
    "label_delay_days": "days between the transaction and its fraud label becoming known (number)",
}
SYSTEM = ("You map the columns of a bank's fraud-model decision log to a canonical schema. "
          "You see only column metadata and aggregates, never rows. Reply with JSON only, shaped as "
          '{"mappings":[{"source":"<column>","target":"<canonical name or null>","confidence":0.0-1.0,'
          '"reason":"<short>"}]}. Use each canonical name at most once, and null for columns that do not fit.')


class ColumnMap(BaseModel):
    source: str
    target: str | None = None
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(default="", max_length=300)


class MappingProposal(BaseModel):
    mappings: list[ColumnMap]


def propose_mapping(llm: GuardedLLM, raw: pd.DataFrame) -> list[ColumnMap]:
    payload = {"canonical": CANONICAL, "source_schema": schema_payload(raw)}
    return sanitize(raw, llm.structured("schema_mapping", SYSTEM, payload, MappingProposal))


def sanitize(raw: pd.DataFrame, proposal: MappingProposal) -> list[ColumnMap]:
    """Drop invented columns and invalid targets; resolve duplicate targets by confidence."""
    taken, seen, clean = set(), set(), []
    for m in sorted((m for m in proposal.mappings if m.source in raw.columns), key=lambda m: -m.confidence):
        if m.source in seen:
            continue
        seen.add(m.source)
        target = m.target if m.target in CANONICAL and m.target not in taken else None
        if target:
            taken.add(target)
        clean.append(m.model_copy(update={"target": target}))
    return clean


def _structural_issues(raw: pd.DataFrame, mapping: dict) -> list[str]:
    issues = [f"Unknown source column: {s}" for s in mapping if s not in raw.columns]
    issues += [f"Unknown target: {t}" for t in mapping.values() if t not in CANONICAL]
    targets = list(mapping.values())
    issues += [f"More than one column is mapped to {t}" for t in sorted({t for t in targets if targets.count(t) > 1})]
    issues += [f"No column is mapped to {t}" for t in CANONICAL if t not in targets]
    return issues


def apply_mapping(raw: pd.DataFrame, mapping) -> pd.DataFrame:
    """Rename source columns to canonical names and validate; raise ValueError if not usable."""
    if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("The mapping must be an object of source column to canonical name")
    issues = _structural_issues(raw, mapping)
    if issues:
        raise ValueError("; ".join(issues))
    return audits.canonicalise(raw[list(mapping)].rename(columns=mapping))


def validate_mapping(raw: pd.DataFrame, mapping: dict) -> list[str]:
    try:
        apply_mapping(raw, mapping)
    except ValueError as exc:
        return str(exc).split("; ")
    return []
