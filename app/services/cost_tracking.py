"""In-memory per-session AI cost ledger + request-scoped session_id for tracing."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from typing import Any, Iterator

from app.config import settings

logger = logging.getLogger(__name__)

_session_id_ctx: ContextVar[str | None] = ContextVar("cost_session_id", default=None)
_lock = threading.Lock()
_ledger: dict[str, "SessionCost"] = {}

_IMAGE_LABEL_HINTS = (
    "image",
    "try-on",
    "tryon",
    "seedream",
    "fibo",
    "text-to-image",
    "virtual-try",
)


@dataclass
class SessionCost:
    session_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    image_calls: int = 0
    estimated_usd: float = 0.0
    has_estimated_tokens: bool = False
    models: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["estimated_usd"] = round(float(self.estimated_usd), 6)
        return data


def get_session_id() -> str | None:
    return _session_id_ctx.get()


def set_session_id(session_id: str | None) -> Token[str | None]:
    return _session_id_ctx.set(session_id)


def reset_session_id(token: Token[str | None]) -> None:
    _session_id_ctx.reset(token)


@contextmanager
def session_cost_scope(session_id: str | None) -> Iterator[str | None]:
    token = set_session_id(session_id)
    try:
        yield session_id
    finally:
        reset_session_id(token)


def estimate_tokens(text: str) -> int:
    """Rough char/4 estimate when provider omits usage."""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def llm_cost_usd(prompt_tokens: int, completion_tokens: int) -> float:
    inp = (max(0, prompt_tokens) / 1_000_000.0) * float(settings.LLM_COST_INPUT_PER_1M_USD)
    out = (max(0, completion_tokens) / 1_000_000.0) * float(settings.LLM_COST_OUTPUT_PER_1M_USD)
    return inp + out


def is_image_like_label(label: str, model_id: str = "") -> bool:
    blob = f"{label} {model_id}".lower()
    if "any-llm" in blob:
        return False
    return any(h in blob for h in _IMAGE_LABEL_HINTS)


def _ensure(session_id: str) -> SessionCost:
    slot = _ledger.get(session_id)
    if slot is None:
        slot = SessionCost(session_id=session_id)
        _ledger[session_id] = slot
    return slot


def record_llm_usage(
    *,
    prompt_tokens: int,
    completion_tokens: int,
    model: str | None = None,
    estimated: bool = False,
    session_id: str | None = None,
    cost_usd: float | None = None,
) -> float:
    sid = session_id or get_session_id()
    usd = cost_usd if cost_usd is not None else llm_cost_usd(prompt_tokens, completion_tokens)
    if not sid:
        return usd
    with _lock:
        slot = _ensure(sid)
        slot.prompt_tokens += max(0, int(prompt_tokens))
        slot.completion_tokens += max(0, int(completion_tokens))
        slot.total_tokens = slot.prompt_tokens + slot.completion_tokens
        slot.llm_calls += 1
        slot.estimated_usd += usd
        if estimated:
            slot.has_estimated_tokens = True
        if model:
            slot.models[model] = slot.models.get(model, 0) + 1
    return usd


def record_image_usage(
    *,
    label: str,
    model_id: str = "",
    session_id: str | None = None,
    cost_usd: float | None = None,
) -> float:
    sid = session_id or get_session_id()
    usd = float(settings.FAL_IMAGE_COST_USD if cost_usd is None else cost_usd)
    if not is_image_like_label(label, model_id):
        return 0.0
    if not sid:
        return usd
    with _lock:
        slot = _ensure(sid)
        slot.image_calls += 1
        slot.estimated_usd += usd
        key = model_id or label
        if key:
            slot.models[key] = slot.models.get(key, 0) + 1
    return usd


def get_session_cost(session_id: str) -> dict[str, Any]:
    with _lock:
        slot = _ledger.get(session_id)
        if slot is None:
            return SessionCost(session_id=session_id).to_dict()
        return slot.to_dict()


def clear_session_cost(session_id: str) -> None:
    with _lock:
        _ledger.pop(session_id, None)
