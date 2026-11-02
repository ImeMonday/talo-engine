"""OpenAI-compatible adapters. Both Meta Model API and Baseten Model APIs speak
the OpenAI Chat Completions format, so one adapter serves both.

Docs: Meta  https://ai.developer.meta.com/docs  (base https://api.meta.ai/v1, key MODEL_API_KEY)
      Baseten https://docs.baseten.co/quickstart (base https://inference.baseten.co/v1)
"""
from __future__ import annotations

import os
import time

from .base import LLMError, LLMResult


class OpenAICompatProvider:
    def __init__(self, name: str, base_url: str, api_key: str, model: str, *,
                 client=None, timeout: float = 90.0, max_retries: int = 3):
        if model.endswith("-contributor"):
            raise LLMError("Contributor-tier models may train on prompts and completions. "
                           "Talo only uses Standard-tier or open-weights models.")
        self.name, self.model = name, model
        if client is None:
            from openai import OpenAI  # SDK handles retries and backoff on 429/5xx
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
        self._client = client

    def chat(self, system: str, user: str) -> LLMResult:
        start = time.perf_counter()
        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except Exception as exc:  # network, auth, rate limit after retries
            raise LLMError(f"{self.name} request failed: {exc}") from exc
        usage = getattr(resp, "usage", None)
        return LLMResult(
            text=resp.choices[0].message.content or "", model=self.model, provider=self.name,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=int((time.perf_counter() - start) * 1000))


def meta_provider(env=os.environ, model=None):
    key = env.get("MODEL_API_KEY")
    if not key:
        return None
    return OpenAICompatProvider("meta-model-api", env.get("META_BASE_URL", "https://api.meta.ai/v1"),
                                key, model or env.get("META_MODEL", "muse-spark-1.3"))


def baseten_provider(env=os.environ, model=None):
    key = env.get("BASETEN_API_KEY")
    if not key:
        return None
    return OpenAICompatProvider("baseten", env.get("BASETEN_BASE_URL", "https://inference.baseten.co/v1"),
                                key, model or env.get("BASETEN_MODEL", "meta-models/muse-spark-open-1026"))


def provider_for_mode(mode: str, model: str | None = None):
    """'cloud' = Muse Spark on Meta Model API; 'open_weights' = Muse Spark Open on Baseten."""
    return {"cloud": meta_provider, "open_weights": baseten_provider}[mode](model=model)
