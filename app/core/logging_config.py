from __future__ import annotations

import logging
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any, Iterator

from app.config import settings

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|password|secret|authorization|api[_-]?key|auth)",
    re.IGNORECASE,
)

_LOGGING_CONFIGURED = False


def get_request_id() -> str:
    return _request_id_ctx.get()


def set_request_id(request_id: str) -> Token[str]:
    return _request_id_ctx.set(request_id)


def reset_request_id(token: Token[str]) -> None:
    _request_id_ctx.reset(token)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def safe_len(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, (bytes, bytearray)):
        return len(value)
    return len(str(value))


def sanitize_query_params(params: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in params.items():
        if _SENSITIVE_KEY_PATTERN.search(str(key)):
            sanitized[key] = "***"
        else:
            sanitized[key] = value
    return sanitized


def truncate_text(text: str, max_len: int = 80) -> str:
    if len(text) <= max_len:
        return text
    return f"{text[:max_len]}..."


@contextmanager
def log_openai_call(logger: logging.Logger, *, operation: str, model: str) -> Iterator[None]:
    logger.info("OpenAI call start operation=%s model=%s", operation, model)
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "OpenAI call end operation=%s model=%s duration_ms=%.2f",
            operation,
            model,
            duration_ms,
        )


def setup_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    log_level_name = (settings.LOG_LEVEL or "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    log_format = (
        "%(asctime)s | %(levelname)s | %(name)s | [%(request_id)s] | %(message)s"
    )
    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.addFilter(RequestIdFilter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(logger_name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    _LOGGING_CONFIGURED = True
