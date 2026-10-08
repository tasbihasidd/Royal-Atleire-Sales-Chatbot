"""Map fal.ai HTTP / quota failures to a stable app error (distinct from chatbot daily quota)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Literal

FalErrorCode = Literal[
    "FAL_QUOTA_EXCEEDED",
    "FAL_RATE_LIMITED",
    "FAL_AUTH_ERROR",
    "FAL_UPSTREAM_ERROR",
    "FAL_KEY_MISSING",
]

MSG_QUOTA = (
    "Our AI image studio has reached its provider limit for now. "
    "Please try again later, or contact a Style Consultant on WhatsApp."
)
MSG_RATE = (
    "Our AI studio is momentarily busy (provider rate limit). "
    "Please wait a moment and try again."
)
MSG_AUTH = (
    "AI studio authentication failed. Please contact support — the design service is temporarily unavailable."
)
MSG_UPSTREAM = (
    "Our AI studio is temporarily unavailable. Please try again shortly."
)
MSG_KEY = "FAL_KEY is missing. Set it in the server environment."

_QUOTA_HINTS = re.compile(
    r"quota|credit|credits|billing|balance|insufficient|payment.?required|"
    r"out of (funds|credit)|usage.?limit|spend.?limit|account.?limit",
    re.I,
)
_RATE_HINTS = re.compile(r"rate.?limit|too many requests|throttl", re.I)


@dataclass
class FalProviderError(Exception):
    """Raised when fal.ai rejects a call (quota, rate limit, auth, upstream)."""

    code: FalErrorCode
    message: str
    http_status: int
    fal_status: int | None = None
    retry_after: str | None = None
    provider_message: str | None = None

    def __str__(self) -> str:
        return self.message

    def to_detail(self) -> dict[str, Any]:
        payload = asdict(self)
        # Keep API body lean for clients.
        return {
            "code": self.code,
            "message": self.message,
            "fal_status": self.fal_status,
            "retry_after": self.retry_after,
            "provider_message": (self.provider_message or "")[:300] or None,
        }


def _header_retry_after(headers: Any) -> str | None:
    if not headers:
        return None
    try:
        if hasattr(headers, "get"):
            val = headers.get("Retry-After") or headers.get("retry-after")
            return str(val) if val else None
        if isinstance(headers, dict):
            for key, val in headers.items():
                if str(key).lower() == "retry-after" and val:
                    return str(val)
    except Exception:
        return None
    return None


def classify_fal_failure(
    exc: BaseException,
    *,
    status_code: int | None = None,
    message: str | None = None,
    response_headers: Any = None,
) -> FalProviderError:
    """Turn a fal_client (or similar) exception into FalProviderError."""
    text = (message if message is not None else str(exc) or "").strip()
    status = status_code
    headers = response_headers

    # Prefer structured fal_client.FalClientHTTPError fields when present.
    if status is None and hasattr(exc, "status_code"):
        try:
            status = int(getattr(exc, "status_code"))
        except (TypeError, ValueError):
            status = None
    if not text and hasattr(exc, "message"):
        text = str(getattr(exc, "message") or "")
    if headers is None and hasattr(exc, "response_headers"):
        headers = getattr(exc, "response_headers")

    retry_after = _header_retry_after(headers)
    lower = text.lower()

    if "FAL_KEY" in text or "fal_key is missing" in lower:
        return FalProviderError(
            code="FAL_KEY_MISSING",
            message=MSG_KEY,
            http_status=500,
            fal_status=status,
            provider_message=text[:300] or None,
        )

    # Explicit HTTP statuses from fal.
    if status == 429 or _RATE_HINTS.search(text):
        return FalProviderError(
            code="FAL_RATE_LIMITED",
            message=MSG_RATE,
            http_status=429,
            fal_status=status or 429,
            retry_after=retry_after,
            provider_message=text[:300] or None,
        )

    quota_like = bool(_QUOTA_HINTS.search(text))
    if status == 402 or quota_like:
        return FalProviderError(
            code="FAL_QUOTA_EXCEEDED",
            message=MSG_QUOTA,
            http_status=429,
            fal_status=status or 402,
            retry_after=retry_after,
            provider_message=text[:300] or None,
        )

    if status in (401, 403):
        return FalProviderError(
            code="FAL_AUTH_ERROR",
            message=MSG_AUTH,
            http_status=503,
            fal_status=status,
            provider_message=text[:300] or None,
        )

    if status is not None and status >= 500:
        return FalProviderError(
            code="FAL_UPSTREAM_ERROR",
            message=MSG_UPSTREAM,
            http_status=502,
            fal_status=status,
            retry_after=retry_after,
            provider_message=text[:300] or None,
        )

    # Unclassified fal client errors → soft upstream.
    return FalProviderError(
        code="FAL_UPSTREAM_ERROR",
        message=MSG_UPSTREAM if status else (text[:280] or MSG_UPSTREAM),
        http_status=502,
        fal_status=status,
        retry_after=retry_after,
        provider_message=text[:300] or None,
    )


def raise_as_fal_provider_error(exc: BaseException) -> None:
    """Re-raise FalProviderError; otherwise classify and raise."""
    if isinstance(exc, FalProviderError):
        raise exc
    raise classify_fal_failure(exc) from exc
