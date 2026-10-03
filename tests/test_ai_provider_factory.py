"""Unit tests for AI provider factory — no network, no live keys."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.ai.deepseek import DEEPSEEK_DEFAULT_MODEL, DeepSeekProvider
from app.services.ai.factory import AIProviderFactory
from app.services.ai.fal import FalProvider
from app.services.ai.openrouter import (
    FIREWORKS_DEFAULT_MODEL,
    OPENROUTER_DEFAULT_MODEL,
    OpenRouterProvider,
)
from app.services.llm import build_chat_model, call_llm, strip_think_tags


FIREWORKS_MODEL = "accounts/fireworks/models/deepseek-v3p1"


def test_factory_fal():
    with patch("app.services.ai.factory.settings") as s:
        s.LLM_PROVIDER = "fal"
        provider = AIProviderFactory.make()
    assert isinstance(provider, FalProvider)
    assert provider.model_name


def test_factory_openrouter():
    with patch("app.services.ai.factory.settings") as s:
        s.LLM_PROVIDER = "openrouter"
        provider = AIProviderFactory.make()
    assert isinstance(provider, OpenRouterProvider)
    assert provider.profile == "openrouter"


def test_factory_fireworks():
    with patch("app.services.ai.factory.settings") as s:
        s.LLM_PROVIDER = "fireworks"
        provider = AIProviderFactory.make("fireworks")
    assert isinstance(provider, OpenRouterProvider)
    assert provider.profile == "fireworks"


def test_factory_deepseek():
    with patch("app.services.ai.factory.settings") as s:
        s.LLM_PROVIDER = "deepseek"
        provider = AIProviderFactory.make()
    assert isinstance(provider, DeepSeekProvider)


def test_factory_none():
    assert AIProviderFactory.make("none") is None
    assert AIProviderFactory.make("") is None


def test_factory_unknown_raises():
    with pytest.raises(ValueError, match="Unsupported LLM_PROVIDER"):
        AIProviderFactory.make("not-a-provider")


def test_fireworks_model_not_copied_to_openrouter():
    with patch("app.services.ai.openrouter.settings") as s:
        s.LLM_MODEL = FIREWORKS_MODEL
        s.OPENAI_MODEL = FIREWORKS_MODEL
        s.LLM_BASE_URL = ""
        s.LLM_API_KEY = ""
        s.OPENROUTER_API_KEY = "sk-or-test"
        s.OPENAI_API_KEY = ""
        s.FIREWORKS_API_KEY = ""
        s.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
        s.OPENROUTER_HTTP_REFERER = "http://localhost"
        s.OPENROUTER_APP_TITLE = "test"
        provider = OpenRouterProvider(profile="openrouter")
    assert provider.model_name == OPENROUTER_DEFAULT_MODEL
    assert "fireworks" not in provider.model_name
    assert "openrouter.ai" in provider.base_url


def test_fireworks_model_not_copied_to_deepseek():
    with patch("app.services.ai.deepseek.settings") as s:
        s.LLM_MODEL = FIREWORKS_MODEL
        s.OPENAI_MODEL = FIREWORKS_MODEL
        s.LLM_BASE_URL = ""
        s.LLM_API_KEY = ""
        s.DEEPSEEK_API_KEY = "sk-ds-test"
        s.DEEPSEEK_SEND_REASONING = False
        provider = DeepSeekProvider()
    assert provider.model_name == DEEPSEEK_DEFAULT_MODEL
    assert not provider.model_name.startswith("accounts/fireworks/")


def test_fireworks_profile_keeps_fireworks_model():
    with patch("app.services.ai.openrouter.settings") as s:
        s.LLM_MODEL = FIREWORKS_MODEL
        s.OPENAI_MODEL = FIREWORKS_MODEL
        s.LLM_BASE_URL = ""
        s.LLM_API_KEY = ""
        s.OPENROUTER_API_KEY = ""
        s.OPENAI_API_KEY = ""
        s.FIREWORKS_API_KEY = "fw-test"
        s.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
        s.OPENROUTER_HTTP_REFERER = "http://localhost"
        s.OPENROUTER_APP_TITLE = "test"
        provider = OpenRouterProvider(profile="fireworks")
    assert provider.model_name == FIREWORKS_MODEL
    assert "fireworks.ai" in provider.base_url


def test_make_builds_fresh_client_each_time():
    with patch("app.services.ai.factory.settings") as s:
        s.LLM_PROVIDER = "openrouter"
        a = AIProviderFactory.make()
        b = AIProviderFactory.make()
    assert a is not b


def test_strip_think_tags():
    assert strip_think_tags("<think>secret</think>Hello") == "Hello"
    assert strip_think_tags("plain") == "plain"


def test_call_llm_retries_and_strips_think():
    fake = MagicMock()
    fake.generate.side_effect = [
        RuntimeError("transient"),
        "<think>x</think>Final answer",
    ]
    with patch("app.services.llm.AIProviderFactory.make", return_value=fake):
        text = call_llm("sys", "user", retries=3)
    assert text == "Final answer"
    assert fake.generate.call_count == 2


def test_build_chat_model_returns_model():
    with patch("app.services.llm.AIProviderFactory.make") as make:
        provider = MagicMock()
        provider.chat_model.return_value = MagicMock(name="ChatOpenAI")
        make.return_value = provider
        model = build_chat_model(temperature=0.1)
    assert model is provider.chat_model.return_value
    provider.chat_model.assert_called_once()


def test_build_chat_model_none_raises():
    with patch("app.services.llm.AIProviderFactory.make", return_value=None):
        with pytest.raises(RuntimeError, match="LLM_PROVIDER is none"):
            build_chat_model()


def test_openrouter_default_when_no_model():
    with patch("app.services.ai.openrouter.settings") as s:
        s.LLM_MODEL = ""
        s.OPENAI_MODEL = ""
        s.LLM_BASE_URL = ""
        s.LLM_API_KEY = "k"
        s.OPENROUTER_API_KEY = "k"
        s.OPENAI_API_KEY = ""
        s.FIREWORKS_API_KEY = ""
        s.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
        s.OPENROUTER_HTTP_REFERER = "http://localhost"
        s.OPENROUTER_APP_TITLE = "test"
        provider = OpenRouterProvider(profile="openrouter")
    assert provider.model_name == OPENROUTER_DEFAULT_MODEL


def test_fireworks_default_when_openrouter_model_configured():
    with patch("app.services.ai.openrouter.settings") as s:
        s.LLM_MODEL = "openai/gpt-4o-mini"
        s.OPENAI_MODEL = "openai/gpt-4o-mini"
        s.LLM_BASE_URL = ""
        s.LLM_API_KEY = ""
        s.OPENROUTER_API_KEY = ""
        s.OPENAI_API_KEY = ""
        s.FIREWORKS_API_KEY = "fw"
        s.OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
        s.OPENROUTER_HTTP_REFERER = "http://localhost"
        s.OPENROUTER_APP_TITLE = "test"
        provider = OpenRouterProvider(profile="fireworks")
    assert provider.model_name == FIREWORKS_DEFAULT_MODEL
