from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.ai.base import AIProvider
from app.services.ai.openrouter import _is_fireworks_model_id

logger = logging.getLogger(__name__)

DEEPSEEK_DEFAULT_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


def _is_deepseek_model_id(model: str | None) -> bool:
    if not model:
        return False
    m = model.strip().lower()
    return m.startswith("deepseek") and not _is_fireworks_model_id(m)


class DeepSeekProvider(AIProvider):
    """Direct DeepSeek API (not the Fireworks-hosted DeepSeek model)."""

    def __init__(self) -> None:
        self._model = self._resolve_model()
        self._base_url = self._resolve_base_url()
        self._api_key = self._resolve_api_key()

    def _resolve_model(self) -> str:
        configured = (settings.LLM_MODEL or settings.OPENAI_MODEL or "").strip()
        if not configured or _is_fireworks_model_id(configured):
            return DEEPSEEK_DEFAULT_MODEL
        # OpenRouter-style ids like deepseek/deepseek-v4.1-flash → use bare deepseek default
        if "/" in configured and not _is_deepseek_model_id(configured.split("/")[-1]):
            # e.g. openai/gpt-4o-mini — not a DeepSeek model
            if not configured.lower().startswith("deepseek"):
                return DEEPSEEK_DEFAULT_MODEL
        if configured.lower().startswith("deepseek/"):
            # Prefer native API model name
            return DEEPSEEK_DEFAULT_MODEL
        if _is_deepseek_model_id(configured):
            return configured
        return DEEPSEEK_DEFAULT_MODEL

    def _resolve_base_url(self) -> str:
        if settings.LLM_BASE_URL:
            return settings.LLM_BASE_URL.rstrip("/")
        return DEEPSEEK_BASE_URL

    def _resolve_api_key(self) -> str:
        if settings.LLM_API_KEY:
            return settings.LLM_API_KEY
        return settings.DEEPSEEK_API_KEY or ""

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
        model_kwargs: dict[str, Any] = {}
        if json_mode:
            model_kwargs["response_format"] = {"type": "json_object"}
        # DeepSeek may not accept reasoning_effort — only send if explicitly supported.
        # Omit by default to avoid 400s taking the bot down.
        if reasoning and settings.DEEPSEEK_SEND_REASONING:
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
