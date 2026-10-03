from __future__ import annotations

"""LangChain chat-model shim — compose / nodes import this, not the factory directly."""

import re
from typing import Any

from app.services.ai.factory import AIProviderFactory

_THINK_RE = re.compile(r"<think>[\s\S]*?</think>", re.IGNORECASE)


def build_chat_model(**kwargs: Any):
    """
    Return a LangChain ChatOpenAI for the configured LLM_PROVIDER.

    Thin shim over AIProviderFactory.make().chat_model() so existing call sites
    and monkeypatches keep working.
    """
    provider = AIProviderFactory.make()
    if provider is None:
        raise RuntimeError(
            "LLM_PROVIDER is none/empty — no chat model available. "
            "Set LLM_PROVIDER=fal and FAL_KEY in .env."
        )
    return provider.chat_model(**kwargs)


def strip_think_tags(text: str) -> str:
    return _THINK_RE.sub("", text or "").strip()


def call_llm(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    max_tokens: int | None = None,
    reasoning: str | None = None,
    retries: int = 2,
) -> str:
    """
    Agent-facing sync generate with think-tag stripping and light retries.
    Empty text after strip is treated as failure so the caller can soft-fail.
    """
    last_err: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            provider = AIProviderFactory.make()
            if provider is None:
                raise RuntimeError("LLM_PROVIDER is none/empty")
            raw = provider.generate(
                system,
                user,
                json_mode=json_mode,
                max_tokens=max_tokens,
                reasoning=reasoning,
            )
            text = strip_think_tags(str(raw or ""))
            if text:
                return text
            last_err = RuntimeError("empty LLM reply")
        except Exception as exc:  # noqa: BLE001 — caller decides soft vs hard fail
            last_err = exc
    raise RuntimeError(f"LLM unavailable: {last_err}") from last_err


async def acall_llm(
    system: str,
    user: str,
    *,
    json_mode: bool = False,
    max_tokens: int | None = None,
    reasoning: str | None = None,
    retries: int = 2,
) -> str:
    """Async twin of call_llm."""
    last_err: Exception | None = None
    for _ in range(max(1, retries)):
        try:
            provider = AIProviderFactory.make()
            if provider is None:
                raise RuntimeError("LLM_PROVIDER is none/empty")
            raw = await provider.agenerate(
                system,
                user,
                json_mode=json_mode,
                max_tokens=max_tokens,
                reasoning=reasoning,
            )
            text = strip_think_tags(str(raw or ""))
            if text:
                return text
            last_err = RuntimeError("empty LLM reply")
        except Exception as exc:  # noqa: BLE001
            last_err = exc
    raise RuntimeError(f"LLM unavailable: {last_err}") from last_err
