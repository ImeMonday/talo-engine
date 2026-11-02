import json
import types

import pandas as pd
import pytest

from talo.llm.base import EgressViolation, LLMError, LLMResult, StructuredOutputError
from talo.llm.guard import GuardedLLM, check_payload, schema_payload
from talo.llm.providers import OpenAICompatProvider
from talo.mapper import MappingProposal, apply_mapping, propose_mapping, sanitize, validate_mapping
from talo.synth import generate


class Scripted:
    name, model = "fake", "fake-1"

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat(self, system, user):
        self.seen.append(user)
        return LLMResult(self.replies.pop(0), self.model, self.name, 10, 5, 3)


RENAME = {"timestamp": "txn_time", "channel": "chan", "amount": "amt_ngn", "model_score": "risk",
          "decision_flagged": "alerted", "label_delay_days": "label_lag"}
GOOD = json.dumps({"mappings": [{"source": s, "target": t, "confidence": 0.9, "reason": "match"}
                                for t, s in RENAME.items()]})


@pytest.fixture(scope="module")
def messy():
    df = generate(n=3000)[0].rename(columns=RENAME)
    df["account_id"] = [f"ACC{i:07d}" for i in range(len(df))]
    return df


def test_guard_blocks_row_dumps_and_odd_types():
    with pytest.raises(EgressViolation):
        check_payload({"rows": [{"a": i} for i in range(500)]})
    with pytest.raises(EgressViolation):
        check_payload({"note": "x" * 1000})
    with pytest.raises(EgressViolation):
        check_payload({"df": pd.DataFrame({"a": [1]})})
    with pytest.raises(EgressViolation):
        check_payload({"v": float("nan")})


def test_schema_payload_has_no_raw_values(messy):
    body = json.dumps(schema_payload(messy))
    assert "ACC0" not in body                      # identifiers never leave
    assert str(messy["amt_ngn"].iloc[0]) not in body  # no individual amounts
    check_payload(json.loads(body))                # and it passes the guard


def test_structured_retries_once_then_succeeds(messy):
    events, llm = [], Scripted(["not json at all", GOOD])
    proposal = propose_mapping(GuardedLLM(llm, events.append), messy)
    assert {m.target for m in proposal if m.target} == set(RENAME)
    assert [e["status"] for e in events] == ["ok", "ok"] and all(e["raw_rows"] == 0 for e in events)


def test_structured_fails_after_attempts_and_still_records(messy):
    events = []
    with pytest.raises(StructuredOutputError):
        propose_mapping(GuardedLLM(Scripted(["nope", "still nope"]), events.append), messy)
    assert len(events) == 2


def test_provider_errors_are_recorded_and_raised():
    class Boom:
        name, model = "boom", "b-1"
        def chat(self, system, user):
            raise LLMError("down")
    events = []
    with pytest.raises(LLMError):
        GuardedLLM(Boom(), events.append).structured("t", "s", {"a": 1}, MappingProposal)
    assert events[0]["status"] == "error"


def test_contributor_tier_is_refused():
    with pytest.raises(LLMError):
        OpenAICompatProvider("meta", "http://x", "k", "muse-spark-1.3-contributor")


def test_provider_sends_openai_chat_format():
    calls = []

    def create(**kw):
        calls.append(kw)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content="hi"))],
                                     usage=types.SimpleNamespace(prompt_tokens=5, completion_tokens=2))

    client = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    res = OpenAICompatProvider("meta", "http://x", "k", "muse-spark-1.3", client=client).chat("sys", "usr")
    assert res.text == "hi" and res.prompt_tokens == 5
    assert calls[0]["model"] == "muse-spark-1.3" and calls[0]["messages"][1] == {"role": "user", "content": "usr"}


def test_sanitize_drops_invented_columns_and_duplicate_targets(messy):
    proposal = MappingProposal.model_validate({"mappings": [
        {"source": "chan", "target": "channel", "confidence": 0.9},
        {"source": "risk", "target": "channel", "confidence": 0.4},
        {"source": "ghost_col", "target": "amount", "confidence": 0.99},
        {"source": "alerted", "target": "not_canonical", "confidence": 0.8}]})
    clean = {m.source: m.target for m in sanitize(messy, proposal)}
    assert clean == {"chan": "channel", "risk": None, "alerted": None}


def test_validate_and_apply_mapping(messy):
    mapping = {v: k for k, v in RENAME.items()}
    assert validate_mapping(messy.assign(**{c: messy[c] for c in messy.columns}).iloc[:2000], mapping) == []
    out = apply_mapping(messy, mapping)
    assert list(out.columns) == list(RENAME) and out.decision_flagged.dtype == bool
    assert any("More than one" in i for i in validate_mapping(messy, {**mapping, "risk": "amount"}))
    assert any("No column is mapped to timestamp" in i for i in validate_mapping(messy, {k: v for k, v in mapping.items() if v != "timestamp"}))
    bad = {**mapping, "chan": "timestamp", "txn_time": "channel"}
    assert validate_mapping(messy, bad)  # swapped columns fail value checks


def test_provider_for_mode_model_override_and_contributor_guard(monkeypatch):
    from talo.llm.providers import provider_for_mode
    monkeypatch.setenv("MODEL_API_KEY", "test-key")
    assert provider_for_mode("cloud", "muse-spark-1.2").model == "muse-spark-1.2"
    with pytest.raises(LLMError):
        provider_for_mode("cloud", "muse-spark-1.2-contributor")
