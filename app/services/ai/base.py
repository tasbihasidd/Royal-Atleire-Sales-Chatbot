from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """OpenAI-compatible chat provider. Empty-text / think-tag handling stays in the caller."""

    @abstractmethod
    def generate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
    ) -> str:
        """Sync completion. Returns raw assistant text."""

    @abstractmethod
    async def agenerate(
        self,
        system: str,
        user: str,
        *,
        json_mode: bool = False,
        max_tokens: int | None = None,
        reasoning: str | None = None,
    ) -> str:
        """Async completion. Returns raw assistant text."""

    @abstractmethod
    def chat_model(self) -> Any:
        """LangChain ChatOpenAI (or compatible) for ainvoke / compose shim."""
