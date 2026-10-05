"""fal.ai any-llm provider. Chat completions go through FAL_KEY, not OpenRouter."""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.ai import UsageMetadata
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import ConfigDict, Field, PrivateAttr

from app.config import settings
from app.services.ai.base import AIProvider
from app.services.ai.fal_usage import parse_fal_llm_result
from app.services.fal_pricing import resolve_fal_call_usd
from app.services.cost_tracking import record_llm_usage
from app.services.fal_runtime import asubscribe, get_last_fal_request_id, subscribe

FAL_ANY_LLM = "fal-ai/any-llm"
FAL_ANY_LLM_VISION = "fal-ai/any-llm/vision"
JSON_ONLY = "\n\nReturn ONLY valid JSON. No markdown. No extra text."


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


class FalChatModel(BaseChatModel):
    """LangChain BaseChatModel over fal any-llm — enables LangSmith LLM spans + usage."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    model_name: str = Field(default="")
    temperature: float = 0.2
    max_tokens: int | None = 2048
    json_mode: bool = False
    _provider: Any = PrivateAttr()

    def __init__(self, provider: "FalProvider", **kwargs: Any) -> None:
        model_name = kwargs.pop("model_name", None) or getattr(provider, "model_name", "") or settings.FAL_LLM_MODEL
        super().__init__(model_name=model_name, **kwargs)
        self._provider = provider

    @property
    def _llm_type(self) -> str:
        return "fal-any-llm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "json_mode": self.json_mode,
        }

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        _ = stop, run_manager, kwargs
        system, user = _split_messages(messages)
        raw = subscribe(
            FAL_ANY_LLM,
            self._provider._arguments(
                system,
                user,
                json_mode=self.json_mode,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            ),
            label="any-llm",
        )
        return self._to_chat_result(raw, system=system, user=user)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        _ = stop, run_manager, kwargs
        system, user = _split_messages(messages)
        raw = await asubscribe(
            FAL_ANY_LLM,
            self._provider._arguments(
                system,
                user,
                json_mode=self.json_mode,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            ),
            label="any-llm",
        )
        return self._to_chat_result(raw, system=system, user=user)

    def _to_chat_result(self, raw: Any, *, system: str, user: str) -> ChatResult:
        text, prompt_tokens, completion_tokens, estimated = parse_fal_llm_result(
            raw, system=system, user=user
        )
        cost_usd, _source = resolve_fal_call_usd(
            endpoint_id=FAL_ANY_LLM,
            result=raw,
            request_id=get_last_fal_request_id(),
            label="any-llm",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        cost = record_llm_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model_name,
            estimated=estimated,
            cost_usd=cost_usd,
        )
        usage = UsageMetadata(
            input_tokens=prompt_tokens,
            output_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        message = AIMessage(
            content=text,
            usage_metadata=usage,
            response_metadata={
                "model_name": self.model_name,
                "estimated_tokens": estimated,
                "total_cost": cost,
                "token_usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
            },
        )
        generation = ChatGeneration(message=message)
        return ChatResult(
            generations=[generation],
            llm_output={
                "model_name": self.model_name,
                "token_usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": prompt_tokens + completion_tokens,
                },
                "estimated_tokens": estimated,
                "total_cost": cost,
            },
        )


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

    def _complete(
        self,
        result: Any,
        *,
        system: str,
        user: str,
    ) -> str:
        text, prompt_tokens, completion_tokens, estimated = parse_fal_llm_result(
            result, system=system, user=user
        )
        cost_usd, _source = resolve_fal_call_usd(
            endpoint_id=FAL_ANY_LLM,
            result=result,
            request_id=get_last_fal_request_id(),
            label="any-llm",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        record_llm_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.model_name,
            estimated=estimated,
            cost_usd=cost_usd,
        )
        return text

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
        _ = reasoning
        result = subscribe(
            FAL_ANY_LLM,
            self._arguments(system, user, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature),
            label="any-llm",
        )
        return self._complete(result, system=system, user=user)

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
        _ = reasoning
        result = await asubscribe(
            FAL_ANY_LLM,
            self._arguments(system, user, json_mode=json_mode, max_tokens=max_tokens, temperature=temperature),
            label="any-llm",
        )
        return self._complete(result, system=system, user=user)

    def chat_model(
        self,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 2048,
        json_mode: bool = False,
        reasoning: str | None = None,
    ) -> FalChatModel:
        _ = reasoning
        return FalChatModel(
            self,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=json_mode,
            model_name=self.model_name,
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
        text, prompt_tokens, completion_tokens, estimated = parse_fal_llm_result(
            result, system=system_prompt, user=prompt
        )
        cost_usd, _source = resolve_fal_call_usd(
            endpoint_id=FAL_ANY_LLM_VISION,
            result=result,
            request_id=get_last_fal_request_id(),
            label="any-llm/vision",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        record_llm_usage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=self.vision_model,
            estimated=estimated,
            cost_usd=cost_usd,
        )
        return text
