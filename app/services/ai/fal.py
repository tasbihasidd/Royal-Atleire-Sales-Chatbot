"""fal.ai any-llm provider. Chat completions go through FAL_KEY, not OpenRouter."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.ai.base import AIProvider
from app.services.fal_runtime import asubscribe, subscribe

FAL_ANY_LLM = "fal-ai/any-llm"
FAL_ANY_LLM_VISION = "fal-ai/any-llm/vision"
JSON_ONLY = "\n\nReturn ONLY valid JSON. No markdown. No extra text."


class _FalChatResult:
    def __init__(self, content: str) -> None:
        self.content = content


def _split_messages(messages: list[Any]) -> tuple[str, str]:
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "type", None) or getattr(msg, "role", None) or ""
        content = getattr(msg, "content", "") or ""
        if not isinstance(content, str):
            content = str(content)
        role_l = str(role).lower()
        if role_l in ("system",):
            system_parts.append(content)
        else:
            user_parts.append(content)
    return "\n\n".join(system_parts).strip(), "\n\n".join(user_parts).strip()


class FalChatModel:
    """Duck-typed LangChain chat model: invoke / ainvoke return .content."""

    def __init__(
        self,
        provider: "FalProvider",
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 2048,
        json_mode: bool = False,
    ) -> None:
        self._provider = provider
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.json_mode = json_mode

    def invoke(self, messages: list[Any]) -> _FalChatResult:
        system, user = _split_messages(messages)
        text = self._provider.generate(
            system,
            user,
            json_mode=self.json_mode,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return _FalChatResult(text)

    async def ainvoke(self, messages: list[Any]) -> _FalChatResult:
        system, user = _split_messages(messages)
        text = await self._provider.agenerate(
            system,
            user,
            json_mode=self.json_mode,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
        )
        return _FalChatResult(text)


class FalProvider(AIProvider):
    name = "fal"

    def __init__(self) -> None:
        self.model_name = (settings.FAL_LLM_MODEL or settings.LLM_MODEL or "google/gemini-2.5-flash").strip()
        self.vision_model = (settings.FAL_VISION_MODEL or self.model_name).strip()

    def _arguments(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool,
        max_tokens: int | None,
        temperature: float | None,
    ) -> dict[str, Any]:
        prompt = user or ""
        system_prompt = system or ""
        if json_mode:
            system_prompt = (system_prompt + JSON_ONLY).strip()
        args: dict[str, Any] = {
            "prompt": prompt,
            "model": self.model_name,
            "priority": "latency",
        }
        if system_prompt:
            args["system_prompt"] = system_prompt
        if temperature is not None:
            args["temperature"] = temperature
        if max_tokens is not None:
            args["max_tokens"] = max_tokens
        return args

    def generate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
        temperature: float | None = 0.2,
    ) -> str:
        result = subscribe(
            FAL_ANY_LLM,
            self._arguments(system, user, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature),
            label="any-llm",
        )
        return _output_text(result)

    async def agenerate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
        temperature: float | None = 0.2,
    ) -> str:
        result = await asubscribe(
            FAL_ANY_LLM,
            self._arguments(system, user, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature),
            label="any-llm",
        )
        return _output_text(result)

    def chat_model(
        self,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 2048,
        json_mode: bool = False,
        reasoning: str | None = None,
    ) -> FalChatModel:
        return FalChatModel(
            self,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
        )

    async def analyze_image(self, prompt: str, image_urls: list[str], *, system_prompt: str = "") -> str:
        args: dict[str, Any] = {
            "prompt": prompt,
            "model": self.vision_model,
            "image_urls": image_urls,
            "priority": "latency",
        }
        if system_prompt:
            args["system_prompt"] = system_prompt
        result = await asubscribe(FAL_ANY_LLM_VISION, args, label="any-llm/vision")
        return _output_text(result)


def _output_text(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("output") or result.get("text") or "")
    return str(getattr(result, "output", "") or "")
