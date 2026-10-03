from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from dotenv import load_dotenv

load_dotenv(override=True)

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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
