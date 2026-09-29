"""
Royal Atelier / Turabees chatbot evaluation suite.

A three-layer harness for a live LangGraph sales agent:

1. **Deterministic assertions** (`assertions.py`) — free, reproducible checks on
   the structured `/chat` response: price grounding, confidential-data leaks,
   language register, graph execution, slot retention, latency.
2. **Multi-model LLM judging** (`judges.py`, `rubric.py`) — ten rubric dimensions
   scored independently by three different model families, reconciled by
   majority vote, with panel disagreement surfaced rather than averaged away.
3. **Persistence probes** (`probes.py`) — direct inspection of Postgres and Redis
   after each session, because the app degrades silently when either is missing.

Twenty personas (`personas.py`) drive real conversations through the live API via
a simulated customer (`simulated_user.py`).

Run it with:

    uv run python -m tests.eval_suite.run_eval --fast
"""

__all__ = [
    "assertions",
    "config",
    "judges",
    "llm",
    "models",
    "personas",
    "probes",
    "reporting",
    "rubric",
    "runner",
    "simulated_user",
]
