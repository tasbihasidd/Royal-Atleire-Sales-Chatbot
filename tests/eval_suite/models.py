"""Dataclasses shared across the evaluation suite."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Persona:
    """A simulated customer.

    `expectations` drives the deterministic assertion layer; `strategy` drives
    the LLM that role-plays the customer. Keeping them separate means a persona
    can be scored on hard facts even when the judge LLM is unavailable.
    """

    name: str
    headline: str
    language: str
    strategy: str
    # Free-form tags used to slice the report (e.g. "negotiation", "adversarial").
    tags: list[str] = field(default_factory=list)
    # Deterministic expectations, consumed by assertions.py.
    expect_nodes: list[str] = field(default_factory=list)
    expect_stages: list[str] = field(default_factory=list)
    expect_products_shown: bool = False
    forbid_nodes: list[str] = field(default_factory=list)
    max_turns: int | None = None


@dataclass
class LLMCall:
    """One OpenRouter request, for cost and latency accounting."""

    model: str
    role: str  # "user_sim" | "judge"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    attempts: int = 1
    error: str | None = None


@dataclass
class DimensionScore:
    score: float | None = None
    verdict: str = "na"  # "pass" | "fail" | "na"
    note: str = ""


@dataclass
class JudgeVerdict:
    """One judge model's opinion of one turn."""

    model: str
    overall_score: float | None = None
    overall_pass: bool | None = None
    dimensions: dict[str, DimensionScore] = field(default_factory=dict)
    defects: list[dict[str, str]] = field(default_factory=list)
    coaching: str = ""
    parse_ok: bool = True
    error: str | None = None
    raw: str = ""


@dataclass
class Assertion:
    """A deterministic, non-LLM check on the bot's structured output."""

    name: str
    passed: bool
    severity: str  # "critical" | "major" | "minor"
    detail: str


@dataclass
class Turn:
    index: int
    user_message: str
    bot_reply: str
    http_status: int = 200
    error: str | None = None
    latency_ms: float = 0.0
    image_url: str = ""
    executed_nodes: list[str] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    assertions: list[Assertion] = field(default_factory=list)
    verdicts: list[JudgeVerdict] = field(default_factory=list)
    llm_calls: list[LLMCall] = field(default_factory=list)

    # ── Derived views ───────────────────────────────────────────────────────
    @property
    def scored_verdicts(self) -> list[JudgeVerdict]:
        return [v for v in self.verdicts if v.parse_ok and v.overall_score is not None]

    @property
    def consensus_score(self) -> float | None:
        scores = [v.overall_score for v in self.scored_verdicts]
        return round(statistics.mean(scores), 2) if scores else None

    @property
    def score_spread(self) -> float | None:
        """Max-min across judges. A wide spread means the turn is ambiguous."""
        scores = [v.overall_score for v in self.scored_verdicts]
        return round(max(scores) - min(scores), 2) if len(scores) > 1 else None

    @property
    def consensus_pass(self) -> bool | None:
        """Majority vote. Ties resolve to fail — we prefer false alarms here."""
        votes = [v.overall_pass for v in self.scored_verdicts if v.overall_pass is not None]
        if not votes:
            return None
        return sum(votes) * 2 > len(votes)

    @property
    def failed_assertions(self) -> list[Assertion]:
        return [a for a in self.assertions if not a.passed]

    @property
    def critical_assertion_failures(self) -> list[Assertion]:
        return [a for a in self.failed_assertions if a.severity == "critical"]

    def dimension_scores(self) -> dict[str, float]:
        """Mean score per rubric dimension across the judge panel."""
        buckets: dict[str, list[float]] = {}
        for v in self.scored_verdicts:
            for dim, ds in v.dimensions.items():
                if ds.score is not None:
                    buckets.setdefault(dim, []).append(ds.score)
        return {d: round(statistics.mean(s), 2) for d, s in buckets.items()}


@dataclass
class StateProbe:
    """Post-session inspection of Postgres / Redis / API, independent of the bot."""

    db_messages: int = 0
    db_profile_present: bool = False
    db_profile: dict[str, Any] = field(default_factory=dict)
    db_session_context_keys: list[str] = field(default_factory=list)
    redis_key_present: bool = False
    redis_profile_matches_db: bool | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class Session:
    persona: Persona
    session_id: str
    turns: list[Turn] = field(default_factory=list)
    probe: StateProbe = field(default_factory=StateProbe)
    started_at: str = ""
    ended_at: str = ""
    end_reason: str = ""
    session_assertions: list[Assertion] = field(default_factory=list)

    @property
    def mean_score(self) -> float | None:
        scores = [t.consensus_score for t in self.turns if t.consensus_score is not None]
        return round(statistics.mean(scores), 2) if scores else None

    @property
    def turn_pass_rate(self) -> float | None:
        votes = [t.consensus_pass for t in self.turns if t.consensus_pass is not None]
        return round(sum(votes) / len(votes), 3) if votes else None

    @property
    def all_assertions(self) -> list[Assertion]:
        out = list(self.session_assertions)
        for t in self.turns:
            out.extend(t.assertions)
        return out

    @property
    def http_errors(self) -> int:
        return sum(1 for t in self.turns if t.error or t.http_status >= 400)

    @property
    def cost_usd(self) -> float:
        return round(sum(c.cost_usd for t in self.turns for c in t.llm_calls), 6)

    @property
    def latencies(self) -> list[float]:
        return [t.latency_ms for t in self.turns if t.latency_ms > 0]
