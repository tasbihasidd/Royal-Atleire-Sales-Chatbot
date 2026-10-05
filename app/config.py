from pydantic_settings import BaseSettings, SettingsConfigDict
import logging
import os
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger(__name__)

# Provider selection — fal.ai is the Royal Atelier default.
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "fal").strip().lower()
LLM_MODEL = os.getenv("LLM_MODEL") or os.getenv("FAL_LLM_MODEL") or os.getenv("OPENAI_MODEL") or os.getenv("OPENROUTER_MODEL") or ""
LLM_BASE_URL = os.getenv("LLM_BASE_URL") or ""
LLM_API_KEY = os.getenv("LLM_API_KEY") or ""

FAL_KEY = os.getenv("FAL_KEY") or ""
FAL_LLM_MODEL = os.getenv("FAL_LLM_MODEL") or "google/gemini-2.5-flash"
FAL_VISION_MODEL = os.getenv("FAL_VISION_MODEL") or FAL_LLM_MODEL
FAL_IMAGE_MODEL = os.getenv("FAL_IMAGE_MODEL") or "fal-ai/bytedance/seedream/v5/lite/edit"
FAL_IMAGE_T2I_MODEL = os.getenv("FAL_IMAGE_T2I_MODEL") or "fal-ai/bytedance/seedream/v5/lite/text-to-image"
FAL_TRYON_MODEL = os.getenv("FAL_TRYON_MODEL") or "bria/fibo-edit-1.5/virtual-try-on"

# OpenRouter (legacy, unused when LLM_PROVIDER=fal).
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENAI_MODEL = (
    os.getenv("OPENAI_MODEL")
    or os.getenv("OPENROUTER_MODEL")
    or os.getenv("LLM_MODEL")
    or FAL_LLM_MODEL
    or "google/gemini-2.5-flash"
)

FIREWORKS_API_KEY = os.getenv("FIREWORKS_API_KEY") or ""
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY") or ""
DEEPSEEK_SEND_REASONING = os.getenv("DEEPSEEK_SEND_REASONING", "false").lower() in ("true", "1", "yes")

BACKEND_API_BASE_URL = os.getenv("BACKEND_API_BASE_URL")
BACKEND_API_TOKEN = os.getenv("BACKEND_API_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")

FABRIC_ANALYSIS_MODEL = os.getenv("FABRIC_ANALYSIS_MODEL") or FAL_VISION_MODEL
IMAGE_MODEL = os.getenv("IMAGE_MODEL") or FAL_IMAGE_MODEL
BASE_URL = os.getenv("BASE_URL")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
USE_MOCK_DATA = os.getenv("USE_MOCK_DATA", "false").lower() in ("true", "1", "yes")
OPENROUTER_HTTP_REFERER = os.getenv("OPENROUTER_HTTP_REFERER", "http://localhost:8015")
OPENROUTER_APP_TITLE = os.getenv("OPENROUTER_APP_TITLE", "Royal Atelier Sales Agent")
_pkr_per_gbp = os.getenv("PKR_PER_GBP")
PKR_PER_GBP = float(_pkr_per_gbp) if _pkr_per_gbp else None
DEFAULT_MARKET_CURRENCY = (os.getenv("DEFAULT_MARKET_CURRENCY") or "PKR").strip().upper()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "on")


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# LangSmith (opt-in). Also accept LANGCHAIN_TRACING_V2 as alias.
LANGSMITH_TRACING = _env_bool("LANGSMITH_TRACING") or _env_bool("LANGCHAIN_TRACING_V2")
LANGSMITH_API_KEY = (os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY") or "").strip()
LANGSMITH_PROJECT = (
    os.getenv("LANGSMITH_PROJECT") or os.getenv("LANGCHAIN_PROJECT") or "royal-atelier-sales-agent"
).strip()
LANGSMITH_ENDPOINT = (os.getenv("LANGSMITH_ENDPOINT") or os.getenv("LANGCHAIN_ENDPOINT") or "").strip()
LANGCHAIN_HIDE_INPUTS = _env_bool("LANGCHAIN_HIDE_INPUTS")
LANGCHAIN_HIDE_OUTPUTS = _env_bool("LANGCHAIN_HIDE_OUTPUTS")
INCLUDE_COST_IN_RESPONSE = _env_bool("INCLUDE_COST_IN_RESPONSE")

# Fallback USD only when Fal Platform pricing/billing APIs are unreachable.
# Primary cost source: GET api.fal.ai/v1/models/pricing + billing-events per request_id.
LLM_COST_INPUT_PER_1M_USD = _env_float("LLM_COST_INPUT_PER_1M_USD", 0.15)
LLM_COST_OUTPUT_PER_1M_USD = _env_float("LLM_COST_OUTPUT_PER_1M_USD", 0.60)
FAL_IMAGE_COST_USD = _env_float("FAL_IMAGE_COST_USD", 0.04)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# Chatbot daily quotas (Option A). Limits hardcoded until Royal Attire GET is live.
CHATBOT_QUOTA_ENFORCE = _env_bool("CHATBOT_QUOTA_ENFORCE", True)
CHATBOT_QUOTA_USE_BACKEND = _env_bool("CHATBOT_QUOTA_USE_BACKEND", False)
CHATBOT_AI_MESSAGES_PER_DAY = _env_int("CHATBOT_AI_MESSAGES_PER_DAY", 10)
CHATBOT_CUSTOM_IMAGES_PER_DAY = _env_int("CHATBOT_CUSTOM_IMAGES_PER_DAY", 2)


class Settings(BaseSettings):
    LLM_PROVIDER: str = LLM_PROVIDER or "fal"
    LLM_MODEL: str = LLM_MODEL or ""
    LLM_BASE_URL: str = LLM_BASE_URL or ""
    LLM_API_KEY: str = LLM_API_KEY or ""
    FIREWORKS_API_KEY: str = FIREWORKS_API_KEY or ""
    DEEPSEEK_API_KEY: str = DEEPSEEK_API_KEY or ""
    DEEPSEEK_SEND_REASONING: bool = DEEPSEEK_SEND_REASONING

    FAL_KEY: str = FAL_KEY or ""
    FAL_LLM_MODEL: str = FAL_LLM_MODEL
    FAL_VISION_MODEL: str = FAL_VISION_MODEL
    FAL_IMAGE_MODEL: str = FAL_IMAGE_MODEL
    FAL_IMAGE_T2I_MODEL: str = FAL_IMAGE_T2I_MODEL
    FAL_TRYON_MODEL: str = FAL_TRYON_MODEL

    OPENROUTER_API_KEY: str = OPENROUTER_API_KEY or ""
    OPENROUTER_BASE_URL: str = OPENROUTER_BASE_URL
    OPENAI_API_KEY: str = OPENROUTER_API_KEY or ""
    OPENAI_MODEL: str = OPENAI_MODEL
    FABRIC_ANALYSIS_MODEL: str = FABRIC_ANALYSIS_MODEL
    IMAGE_MODEL: str = IMAGE_MODEL
    BASE_URL: str = BASE_URL
    OPENROUTER_HTTP_REFERER: str = OPENROUTER_HTTP_REFERER
    OPENROUTER_APP_TITLE: str = OPENROUTER_APP_TITLE

    BACKEND_API_BASE_URL: str = BACKEND_API_BASE_URL
    BACKEND_API_TOKEN: str = BACKEND_API_TOKEN or ""

    DATABASE_URL: str = DATABASE_URL

    LOG_LEVEL: str = LOG_LEVEL

    USE_MOCK_DATA: bool = USE_MOCK_DATA

    PKR_PER_GBP: float | None = PKR_PER_GBP
    DEFAULT_MARKET_CURRENCY: str = DEFAULT_MARKET_CURRENCY

    LANGSMITH_TRACING: bool = LANGSMITH_TRACING
    LANGSMITH_API_KEY: str = LANGSMITH_API_KEY
    LANGSMITH_PROJECT: str = LANGSMITH_PROJECT
    LANGSMITH_ENDPOINT: str = LANGSMITH_ENDPOINT
    LANGCHAIN_HIDE_INPUTS: bool = LANGCHAIN_HIDE_INPUTS
    LANGCHAIN_HIDE_OUTPUTS: bool = LANGCHAIN_HIDE_OUTPUTS
    INCLUDE_COST_IN_RESPONSE: bool = INCLUDE_COST_IN_RESPONSE

    LLM_COST_INPUT_PER_1M_USD: float = LLM_COST_INPUT_PER_1M_USD
    LLM_COST_OUTPUT_PER_1M_USD: float = LLM_COST_OUTPUT_PER_1M_USD
    FAL_IMAGE_COST_USD: float = FAL_IMAGE_COST_USD

    CHATBOT_QUOTA_ENFORCE: bool = CHATBOT_QUOTA_ENFORCE
    CHATBOT_QUOTA_USE_BACKEND: bool = CHATBOT_QUOTA_USE_BACKEND
    CHATBOT_AI_MESSAGES_PER_DAY: int = CHATBOT_AI_MESSAGES_PER_DAY
    CHATBOT_CUSTOM_IMAGES_PER_DAY: int = CHATBOT_CUSTOM_IMAGES_PER_DAY

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()


def configure_langsmith() -> bool:
    """
    Sync LangSmith / LangChain tracing env vars before any graph/LLM run.
    Returns True when tracing is enabled.
    """
    enabled = bool(settings.LANGSMITH_TRACING)
    if enabled:
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        os.environ["LANGSMITH_TRACING"] = "true"
        if settings.LANGSMITH_API_KEY:
            os.environ["LANGSMITH_API_KEY"] = settings.LANGSMITH_API_KEY
            os.environ["LANGCHAIN_API_KEY"] = settings.LANGSMITH_API_KEY
        if settings.LANGSMITH_PROJECT:
            os.environ["LANGSMITH_PROJECT"] = settings.LANGSMITH_PROJECT
            os.environ["LANGCHAIN_PROJECT"] = settings.LANGSMITH_PROJECT
        if settings.LANGSMITH_ENDPOINT:
            os.environ["LANGSMITH_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
            os.environ["LANGCHAIN_ENDPOINT"] = settings.LANGSMITH_ENDPOINT
        os.environ["LANGCHAIN_HIDE_INPUTS"] = "true" if settings.LANGCHAIN_HIDE_INPUTS else "false"
        os.environ["LANGCHAIN_HIDE_OUTPUTS"] = "true" if settings.LANGCHAIN_HIDE_OUTPUTS else "false"
        if not settings.LANGSMITH_API_KEY:
            logger.warning(
                "LangSmith tracing enabled but LANGSMITH_API_KEY is empty — runs will not upload"
            )
        else:
            logger.info(
                "LangSmith tracing enabled project=%s",
                settings.LANGSMITH_PROJECT,
            )
    else:
        os.environ["LANGCHAIN_TRACING_V2"] = "false"
        os.environ["LANGSMITH_TRACING"] = "false"
        logger.info("LangSmith tracing disabled")
    return enabled


configure_langsmith()
