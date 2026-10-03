from __future__ import annotations

from typing import Any

from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.ai.base import AIProvider
from app.services.ai.deepseek import DeepSeekProvider
from app.services.ai.fal import FalProvider
from app.services.ai.openrouter import OpenRouterProvider


class _LegacyOpenAICompatibleProvider(AIProvider):
    """openai / groq / anthropic — same ChatOpenAI path as historical build_chat_model."""

    def __init__(self, name: str) -> None:
        self.name = name.strip().lower()
        self._model = (settings.LLM_MODEL or settings.OPENAI_MODEL or "openai/gpt-4o-mini").strip()
        self._base_url = self._resolve_base_url()
        self._api_key = (
            settings.LLM_API_KEY
            or settings.OPENROUTER_API_KEY
            or settings.OPENAI_API_KEY
            or ""
        )

    def _resolve_base_url(self) -> str:
        if settings.LLM_BASE_URL:
            return settings.LLM_BASE_URL.rstrip("/")
        defaults = {
            "openai": "https://api.openai.com/v1",
            "groq": "https://api.groq.com/openai/v1",
            "anthropic": settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1",
        }
        return defaults.get(self.name, settings.OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1")

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
        if self.name in ("anthropic",) or "openrouter" in self._base_url:
            kwargs["default_headers"] = {
                "HTTP-Referer": settings.OPENROUTER_HTTP_REFERER,
                "X-Title": settings.OPENROUTER_APP_TITLE,
            }
        model_kwargs: dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        if reasoning:
            model_kwargs["reasoning_effort"] = reasoning
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs
        return ChatOpenAI(**kwargs)

    def generate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
    ) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self.chat_model(
            max_tokens=max_tokens if max_tokens is not None else 2048,
            json_mode=json_mode,
            reasoning=reasoning,
        )
        response = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
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
        from langchain_core.messages import HumanMessage, SystemMessage

        llm = self.chat_model(
            max_tokens=max_tokens if max_tokens is not None else 2048,
            json_mode=json_mode,
            reasoning=reasoning,
        )
        response = await llm.ainvoke([SystemMessage(content=system), HumanMessage(content=user)])
        return (getattr(response, "content", None) or "") if response is not None else ""


class AIProviderFactory:
    """Build a fresh provider on every make() — never cache clients across env switches."""

    @staticmethod
    def make(name: str | None = None) -> AIProvider | None:
        raw = (name if name is not None else settings.LLM_PROVIDER) or ""
        provider_name = str(raw).strip().lower()
        if not provider_name or provider_name in ("none", "off", "disabled"):
            return None
        if provider_name == "fal":
            return FalProvider()
        if provider_name in ("openrouter", "fireworks"):
            return OpenRouterProvider(profile=provider_name)
        if provider_name == "deepseek":
            return DeepSeekProvider()
        if provider_name in ("openai", "groq", "anthropic"):
            return _LegacyOpenAICompatibleProvider(provider_name)
        raise ValueError(
            f"Unsupported LLM_PROVIDER={provider_name!r}. "
            "Use fal, openrouter, fireworks, deepseek, openai, groq, anthropic, or none."
        )
