"""
Test harness configuration for Royal Atelier E2E Persona Tests.
"""
from __future__ import annotations

import os
from pathlib import Path
from dotenv import load_dotenv

# Load project .env
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# ── Chatbot under test ──────────────────────────────────────────────
CHATBOT_BASE_URL = os.getenv("BASE_URL", "http://localhost:8015")
CHATBOT_CHAT_URL = f"{CHATBOT_BASE_URL}/chat"
CHATBOT_HEALTH_URL = f"{CHATBOT_BASE_URL}/health"
CHATBOT_CLEAR_SESSION_URL = f"{CHATBOT_BASE_URL}/api/sessions/{{session_id}}/clear"

# ── LLM for testers & judge (DeepSeek v4.1 Flash via OpenRouter) ───
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
TESTER_MODEL = "deepseek/deepseek-v4.1-flash"
JUDGE_MODEL = "deepseek/deepseek-v4.1-flash"

# ── Limits ──────────────────────────────────────────────────────────
MAX_TURNS_PER_PERSONA = 12          # safety cutoff
HTTP_TIMEOUT_SECONDS = 120          # chatbot can be slow on first turn
LLM_TIMEOUT_SECONDS = 60

# ── Output ──────────────────────────────────────────────────────────
RESULTS_DIR = _PROJECT_ROOT / "test_results"
RESULTS_DIR.mkdir(exist_ok=True)
