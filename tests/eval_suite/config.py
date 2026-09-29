"""
Configuration for the Royal Atelier / Turabees evaluation suite.

Every knob the suite needs lives here so a run can be reproduced from a single
file. Nothing in this module performs I/O beyond reading ``.env``.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


# ── System under test ───────────────────────────────────────────────────────
CHATBOT_BASE_URL = os.getenv("EVAL_CHATBOT_URL", os.getenv("BASE_URL", "http://localhost:8015"))
CHAT_URL = f"{CHATBOT_BASE_URL}/chat"
HEALTH_URL = f"{CHATBOT_BASE_URL}/health"
CLEAR_SESSION_URL = f"{CHATBOT_BASE_URL}/api/sessions/{{session_id}}/clear"
SESSION_MESSAGES_URL = f"{CHATBOT_BASE_URL}/sessions/{{session_id}}/messages"

DATABASE_URL = os.getenv("DATABASE_URL", "")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
BACKEND_API_BASE_URL = os.getenv("BACKEND_API_BASE_URL", "")


# ── LLM provider ────────────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY", "")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
COMPLETIONS_URL = f"{OPENROUTER_BASE_URL}/chat/completions"


# ── Model roster ────────────────────────────────────────────────────────────
# All three were probed for strict `json_schema` support before being added.
# Deliberately three *different* model families so the judge panel does not
# share a single set of blind spots (and so no judge is scoring its own family's
# output — the chatbot itself runs on DeepSeek).
DEEPSEEK = "deepseek/deepseek-v4.1-flash"
GEMINI = "google/gemini-2.5-flash-lite"
GPT_MINI = "openai/gpt-4o-mini"

# Rejected after probing, kept here so the next person does not retry them:
#   qwen/qwen3.7-flash   — reasoning model; spends the whole token budget on
#                          hidden reasoning and returns empty content.
#   z-ai/glm-5.3-flash   — ignores `json_schema` and emits prose analysis first,
#                          then runs out of tokens before closing the JSON.
#   z-ai/glm-4.7-flash   — same, truncates a 3 KB verdict mid-object.

# The simulated customer. One model for all personas keeps user-side behaviour
# comparable across sessions; the *persona prompt* is what varies.
USER_SIM_MODEL = os.getenv("EVAL_USER_MODEL", DEEPSEEK)

# The judge panel. Each turn is scored independently by every judge, then
# reconciled in `judges.consensus()`.
JUDGE_PANEL: tuple[str, ...] = (DEEPSEEK, GEMINI, GPT_MINI)

# Single judge used for cheap/fast smoke runs (`--fast`).
FAST_JUDGE = GEMINI

# USD per 1M tokens, mirrored from the OpenRouter model list. Used for cost
# reporting only — it never gates a run.
PRICING: dict[str, dict[str, float]] = {
    DEEPSEEK: {"prompt": 0.30, "completion": 1.20},
    GEMINI: {"prompt": 0.10, "completion": 0.40},
    GPT_MINI: {"prompt": 0.15, "completion": 0.60},
}


# ── Run limits ──────────────────────────────────────────────────────────────
MAX_TURNS = int(os.getenv("EVAL_MAX_TURNS", "12"))
SESSION_CONCURRENCY = int(os.getenv("EVAL_CONCURRENCY", "4"))

CHAT_TIMEOUT_S = float(os.getenv("EVAL_CHAT_TIMEOUT", "180"))
LLM_TIMEOUT_S = float(os.getenv("EVAL_LLM_TIMEOUT", "120"))

# Retries apply to transport/5xx failures, not to assertion failures.
CHAT_MAX_ATTEMPTS = 3
LLM_MAX_ATTEMPTS = 4
RETRY_BACKOFF_S = 2.0

# A single 500 from /chat should not silently pass as "bot handled it".
# Instead it is recorded as a hard defect and the session continues so we still
# learn how the rest of the funnel behaves.
ABORT_SESSION_AFTER_CONSECUTIVE_ERRORS = 3

USER_SIM_TEMPERATURE = 0.85
JUDGE_TEMPERATURE = 0.0
# Keep judge/user budgets small so OpenRouter credit reservation stays under
# remaining balance (full-context max_tokens reservations cause HTTP 402).
JUDGE_MAX_TOKENS = 800
USER_SIM_MAX_TOKENS = 400


# ── Quality gates (used for CI exit codes and regression diffing) ───────────
GATE_MIN_MEAN_SCORE = float(os.getenv("EVAL_GATE_SCORE", "7.0"))
GATE_MIN_TURN_PASS_RATE = float(os.getenv("EVAL_GATE_PASS_RATE", "0.75"))
GATE_MAX_CRITICAL_DEFECTS = int(os.getenv("EVAL_GATE_CRITICAL", "0"))
GATE_MAX_P95_LATENCY_MS = float(os.getenv("EVAL_GATE_P95_LATENCY", "20000"))
# A score drop larger than this vs the baseline counts as a regression.
REGRESSION_SCORE_TOLERANCE = 0.3


# ── Output ──────────────────────────────────────────────────────────────────
RESULTS_DIR = PROJECT_ROOT / "test_results"
TRANSCRIPT_DIR = RESULTS_DIR / "transcripts"
BASELINE_PATH = RESULTS_DIR / "baseline.json"
LOG_DIR = PROJECT_ROOT / "logs"

for _d in (RESULTS_DIR, TRANSCRIPT_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def validate() -> list[str]:
    """Return a list of fatal configuration problems (empty means OK)."""
    problems: list[str] = []
    if not OPENROUTER_API_KEY:
        problems.append("OPENROUTER_API_KEY is not set (checked OPENROUTER_API_KEY, OPENAI_API_KEY)")
    if not DATABASE_URL:
        problems.append("DATABASE_URL is not set — state probes will be skipped")
    return problems
