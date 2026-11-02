"""Shared LLM types. Providers are interchangeable behind `chat(system, user)`."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class LLMError(RuntimeError):
    """A model call failed."""


class StructuredOutputError(LLMError):
    """The model never produced output matching the requested schema."""


class EgressViolation(LLMError):
    """A payload was blocked because it could contain raw data."""


@dataclass(frozen=True)
class LLMResult:
    text: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int


class LLMProvider(Protocol):
    name: str
    model: str

    def chat(self, system: str, user: str) -> LLMResult: ...
