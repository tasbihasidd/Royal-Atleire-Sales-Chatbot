from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.ai.base import AIProvider

logger = logging.getLogger(__name__)

FIREWORKS_DEFAULT_MODEL = "accounts/fireworks/models/deepseek-v3p1"
OPENROUTER_DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
FIREWORKS_BASE_URL = "https://api.fireworks.ai/inference/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def _is_fireworks_model_id(model: str | None) -> bool:
    if not model:
        return False
    m = model.strip().lower()
    return m.startswith("accounts/fireworks/") or "/fireworks/" in m


class OpenRouterProvider(AIProvider):
    """OpenAI-compatible client for OpenRouter *or* Fireworks (two profiles, not shared defaults)."""

    def __init__(self, profile: str = "openrouter") -> None:
        profile = (profile or "openrouter").strip().lower()
        if profile not in ("openrouter", "fireworks"):
            raise ValueError(f"OpenRouterProvider profile must be openrouter or fireworks, got {profile!r}")
        self.profile = profile
        self._model = self._resolve_model()
        self._base_url = self._resolve_base_url()
        self._api_key = self._resolve_api_key()
        self._default_headers = self._resolve_headers()

    def _resolve_model(self) -> str:
        configured = (settings.LLM_MODEL or settings.OPENAI_MODEL or "").strip()
        if self.profile == "fireworks":
            if configured and _is_fireworks_model_id(configured):
                return configured
            if configured and not _is_fireworks_model_id(configured):
                # Explicit non-Fireworks id on fireworks profile — still allow if user set LLM_MODEL
                # but never send an OpenRouter-style id unless they clearly want it; prefer FW default
                # when the configured id looks like OpenRouter (provider/model).
                if "/" in configured and not configured.startswith("accounts/"):
                    return FIREWORKS_DEFAULT_MODEL
                return configured or FIREWORKS_DEFAULT_MODEL
            return FIREWORKS_DEFAULT_MODEL
        # openrouter profile — never send a Fireworks model id
        if configured and _is_fireworks_model_id(configured):
            return OPENROUTER_DEFAULT_MODEL
        return configured or OPENROUTER_DEFAULT_MODEL

    def _resolve_base_url(self) -> str:
        if settings.LLM_BASE_URL:
            return settings.LLM_BASE_URL.rstrip("/")
        if self.profile == "fireworks":
            return FIREWORKS_BASE_URL
        return (settings.OPENROUTER_BASE_URL or OPENROUTER_BASE_URL).rstrip("/")

    def _resolve_api_key(self) -> str:
        if settings.LLM_API_KEY:
            return settings.LLM_API_KEY
        if self.profile == "fireworks":
            return settings.FIREWORKS_API_KEY or settings.OPENROUTER_API_KEY or settings.OPENAI_API_KEY or ""
        return (
            settings.OPENROUTER_API_KEY
            or settings.OPENAI_API_KEY
            or settings.LLM_API_KEY
            or ""
        )

    def _resolve_headers(self) -> dict[str, str]:
        if self.profile == "openrouter":
            return {
                "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
                "X-Title": settings.OPENROUTER_APP_TITLE,
            }
        return {}

    def chat_model(
        self,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 2048,
        json_mode: bool = False,
        reasoning: str | None = None,
    ) -> ChatOpenAI:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "api_key": self._api_key,
            "base_url": self._base_url,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if self._default_headers:
            kwargs["default_headers"] = self._default_headers
        model_kwargs: dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        if reasoning:
            # Fireworks / OpenRouter may accept reasoning_effort; omit if empty.
            model_kwargs["reasoning_effort"] = reasoning
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        return ChatOpenAI(**kwargs)

    def _messages(self, system: str, user: str) -> list:
        return [SystemMessage(content=system), HumanMessage(content=user)]

    def generate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
    ) -> str:
        llm = self.chat_model(
            max_tokens=max_tokens if max_tokens is not None else 2048,
            json_mode=json_mode,
            reasoning=reasoning,
        )
        response = llm.invoke(self._messages(system, user))
        return (getattr(response, "content", None) or "") if response is not None else ""

    async def agenerate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
    ) -> str:
        llm = self.chat_model(
            max_tokens=max_tokens if max_tokens is not None else 2048,
            json_mode=json_mode,
            reasoning=reasoning,
        )
        response = await llm.ainvoke(self._messages(system, user))
        return (getattr(response, "content", None) or "") if response is not None else ""

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def base_url(self) -> str:
        return self._base_url
