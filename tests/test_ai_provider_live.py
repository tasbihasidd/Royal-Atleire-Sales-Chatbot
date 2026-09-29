"""
Optional live provider smoke test — off by default.

  pytest tests/test_ai_provider_live.py --llm-provider openrouter
  pytest tests/test_ai_provider_live.py --llm-provider deepseek

Skipped unless --llm-provider is openrouter|deepseek and a matching key is set.
"""
from __future__ import annotations

import os

import pytest

from app.services.ai.factory import AIProviderFactory


@pytest.fixture
def live_provider(request):
    name = request.config.getoption("--llm-provider")
    if name not in ("openrouter", "deepseek"):
        pytest.skip("pass --llm-provider openrouter|deepseek to run live generate()")
    key_env = {
        "openrouter": ("OPENROUTER_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY"),
        "deepseek": ("DEEPSEEK_API_KEY", "LLM_API_KEY"),
    }[name]
    if not any(os.getenv(k) for k in key_env):
        pytest.skip(f"no API key for {name} ({', '.join(key_env)})")
    return name


def test_live_generate(live_provider, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", live_provider)
    from app.config import settings

    monkeypatch.setattr(settings, "LLM_PROVIDER", live_provider)
    provider = AIProviderFactory.make(live_provider)
    assert provider is not None
    text = provider.generate(
        "You are a concise assistant.",
        "Reply with exactly: ok",
        max_tokens=32,
    )
    assert isinstance(text, str)
    assert text.strip()
