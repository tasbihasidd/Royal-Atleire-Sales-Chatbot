"""
Report generation: machine-readable JSON, a human Markdown report, full
transcripts, and a regression diff against a stored baseline.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tests.eval_suite import config
from tests.eval_suite.models import Session
from tests.eval_suite.rubric import DIMENSIONS


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(int(round(pct / 100 * (len(ordered) - 1))), len(ordered) - 1)
    return ordered[index]


def aggregate(sessions: list[Session]) -> dict[str, Any]:
    turns = [t for s in sessions for t in s.turns]
    scores = [t.consensus_score for t in turns if t.consensus_score is not None]
    votes = [t.consensus_pass for t in turns if t.consensus_pass is not None]
    latencies = [t.latency_ms for t in turns if t.latency_ms > 0]

    dim_scores: dict[str, list[float]] = {}
    for turn in turns:
        for dim, score in turn.dimension_scores().items():
            dim_scores.setdefault(dim, []).append(score)

    assertion_stats: dict[str, dict[str, Any]] = {}
    for session in sessions:
        for assertion in session.all_assertions:
            entry = assertion_stats.setdefault(
                assertion.name,
                {"severity": assertion.severity, "total": 0, "failed": 0, "examples": []},
            )
            entry["total"] += 1
            if not assertion.passed:
                entry["failed"] += 1
                if len(entry["examples"]) < 4:
                    entry["examples"].append(assertion.detail[:200])

    defects: list[dict[str, Any]] = []
    for session in sessions:
        for turn in session.turns:
            seen: set[str] = set()
            for verdict in turn.verdicts:
                for defect in verdict.defects:
                    key = (defect["severity"], defect["description"][:80])
                    if key in seen:
                        continue
                    seen.add(key)
                    defects.append(
                        {
                            "persona": session.persona.name,
                            "turn": turn.index,
                            "severity": defect["severity"],
                            "dimension": defect["dimension"],
                            "description": defect["description"],
                            "judge": verdict.model,
                        }
                    )

    judge_health: dict[str, dict[str, Any]] = {}
    for session in sessions:
        for turn in session.turns:
            for verdict in turn.verdicts:
                entry = judge_health.setdefault(
                    verdict.model, {"calls": 0, "parse_ok": 0, "scores": []}
                )
                entry["calls"] += 1
                if verdict.parse_ok:
                    entry["parse_ok"] += 1
                    if verdict.overall_score is not None:
                        entry["scores"].append(verdict.overall_score)
    for entry in judge_health.values():
        entry["parse_rate"] = round(entry["parse_ok"] / entry["calls"], 3) if entry["calls"] else 0.0
        entry["mean_score"] = round(statistics.mean(entry["scores"]), 2) if entry["scores"] else None
        entry.pop("scores")

    cost = round(sum(s.cost_usd for s in sessions), 4)
    total_llm_calls = sum(len(t.llm_calls) for t in turns)

    critical = sum(
        1
        for s in sessions
        for a in s.all_assertions
        if not a.passed and a.severity == "critical"
    ) + sum(1 for d in defects if d["severity"] == "critical")

    return {
        "sessions": len(sessions),
        "turns": len(turns),
        "mean_score": round(statistics.mean(scores), 2) if scores else None,
        "median_score": round(statistics.median(scores), 2) if scores else None,
        "turn_pass_rate": round(sum(votes) / len(votes), 3) if votes else None,
        "critical_count": critical,
        "defects_total": len(defects),
        "defects_by_severity": {
            sev: sum(1 for d in defects if d["severity"] == sev)
            for sev in ("critical", "major", "minor")
        },
        "latency_ms": {
            "mean": round(statistics.mean(latencies)) if latencies else None,
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "max": max(latencies) if latencies else None,
        },
        "dimension_means": {d: round(statistics.mean(v), 2) for d, v in sorted(dim_scores.items())},
        "assertions": assertion_stats,
        "defects": defects,
        "judge_health": judge_health,
        "cost_usd": cost,
        "llm_calls": total_llm_calls,
        "http_errors": sum(s.http_errors for s in sessions),
        "wide_disagreement_turns": [
            {"persona": s.persona.name, "turn": t.index, "spread": t.score_spread,
             "scores": {v.model: v.overall_score for v in t.scored_verdicts}}
            for s in sessions for t in s.turns
            if t.score_spread is not None and t.score_spread >= 3.0
        ],
    }


def gate(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    """Apply the CI quality gates. Returns (passed, reasons_for_failure)."""
    failures: list[str] = []
    score = summary.get("mean_score")
    if score is None:
        failures.append("no turns were scored at all")
    elif score < config.GATE_MIN_MEAN_SCORE:
        failures.append(f"mean score {score} < gate {config.GATE_MIN_MEAN_SCORE}")

    rate = summary.get("turn_pass_rate")
    if rate is not None and rate < config.GATE_MIN_TURN_PASS_RATE:
        failures.append(f"turn pass rate {rate} < gate {config.GATE_MIN_TURN_PASS_RATE}")

    if summary.get("critical_count", 0) > config.GATE_MAX_CRITICAL_DEFECTS:
        failures.append(
            f"{summary['critical_count']} critical findings > gate {config.GATE_MAX_CRITICAL_DEFECTS}"
        )

    p95 = (summary.get("latency_ms") or {}).get("p95")
    if p95 is not None and p95 > config.GATE_MAX_P95_LATENCY_MS:
        failures.append(f"p95 latency {p95:.0f}ms > gate {config.GATE_MAX_P95_LATENCY_MS:.0f}ms")

    return (not failures), failures


def diff_baseline(summary: dict[str, Any]) -> dict[str, Any] | None:
    """Compare this run against the stored baseline, if there is one."""
    if not config.BASELINE_PATH.exists():
        return None
    try:
        baseline = json.loads(config.BASELINE_PATH.read_text())
    except Exception:
        return None

    base_summary = baseline.get("summary", baseline)
    out: dict[str, Any] = {"baseline_generated_at": baseline.get("generated_at"), "changes": {}, "regressions": []}

    def compare(key: str, path: list[str], higher_is_better: bool = True) -> None:
        cur, old = summary, base_summary
        for part in path:
            cur = (cur or {}).get(part) if isinstance(cur, dict) else None
            old = (old or {}).get(part) if isinstance(old, dict) else None
        if not isinstance(cur, (int, float)) or not isinstance(old, (int, float)):
            return
        delta = round(cur - old, 3)
        out["changes"][key] = {"baseline": old, "current": cur, "delta": delta}
        worse = (delta < -config.REGRESSION_SCORE_TOLERANCE) if higher_is_better else (delta > 0)
        if worse:
            out["regressions"].append(f"{key}: {old} -> {cur} ({delta:+})")

    compare("mean_score", ["mean_score"])
    compare("turn_pass_rate", ["turn_pass_rate"])
    compare("critical_count", ["critical_count"], higher_is_better=False)
    compare("p95_latency_ms", ["latency_ms", "p95"], higher_is_better=False)
    for dim in DIMENSIONS:
        compare(f"dim.{dim}", ["dimension_means", dim])
    return out


def to_json(sessions: list[Session], summary: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "meta": meta,
        "summary": summary,
        "sessions": [
            {
                "persona": s.persona.name,
                "headline": s.persona.headline,
                "language": s.persona.language,
                "tags": s.persona.tags,
                "session_id": s.session_id,
                "started_at": s.started_at,
                "ended_at": s.ended_at,
                "end_reason": s.end_reason,
                "mean_score": s.mean_score,
                "turn_pass_rate": s.turn_pass_rate,
                "http_errors": s.http_errors,
                "cost_usd": s.cost_usd,
                "state_probe": {
                    "db_messages": s.probe.db_messages,
                    "db_profile_present": s.probe.db_profile_present,
                    "db_profile": s.probe.db_profile,
                    "db_session_context_keys": s.probe.db_session_context_keys,
                    "redis_key_present": s.probe.redis_key_present,
                    "redis_profile_matches_db": s.probe.redis_profile_matches_db,
                    "notes": s.probe.notes,
                },
                "session_assertions": [
                    {"name": a.name, "passed": a.passed, "severity": a.severity, "detail": a.detail}
                    for a in s.session_assertions
                ],
                "turns": [
                    {
                        "index": t.index,
                        "user_message": t.user_message,
                        "bot_reply": t.bot_reply,
                        "http_status": t.http_status,
                        "error": t.error,
                        "latency_ms": round(t.latency_ms),
                        "executed_nodes": t.executed_nodes,
                        "image_url": t.image_url,
                        "consensus_score": t.consensus_score,
                        "consensus_pass": t.consensus_pass,
                        "score_spread": t.score_spread,
                        "dimension_scores": t.dimension_scores(),
                        "state": t.state,
                        "assertions": [
                            {"name": a.name, "passed": a.passed, "severity": a.severity, "detail": a.detail}
                            for a in t.assertions
                        ],
                        "verdicts": [
                            {
                                "model": v.model,
                                "overall_score": v.overall_score,
                                "overall_pass": v.overall_pass,
                                "parse_ok": v.parse_ok,
                                "error": v.error,
                                "coaching": v.coaching,
                                "defects": v.defects,
                                "dimensions": {
                                    d: {"verdict": ds.verdict, "score": ds.score, "note": ds.note}
                                    for d, ds in v.dimensions.items()
                                },
                            }
                            for v in t.verdicts
                        ],
                    }
                    for t in s.turns
                ],
            }
            for s in sessions
        ],
    }


def to_markdown(sessions: list[Session], summary: dict[str, Any], meta: dict[str, Any],
                baseline_diff: dict[str, Any] | None) -> str:
    lines: list[str] = []
    add = lines.append

    add("# Royal Atelier / Turabees — Chatbot Evaluation Report")
    add("")
    add(f"*Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    add("")
    add("## Run configuration")
    add("")
    add("| Setting | Value |")
    add("| --- | --- |")
    for key, value in meta.items():
        add(f"| {key} | {value} |")
    add("")

    add("## Headline numbers")
    add("")
    lat = summary.get("latency_ms") or {}
    add("| Metric | Value |")
    add("| --- | --- |")
    add(f"| Sessions | {summary['sessions']} |")
    add(f"| Turns | {summary['turns']} |")
    add(f"| Mean turn score | **{_num(summary.get('mean_score'))} / 10** |")
    add(f"| Median turn score | {_num(summary.get('median_score'))} |")
    add(f"| Turn pass rate | **{_pct(summary.get('turn_pass_rate'))}** |")
    add(f"| Critical findings | **{summary.get('critical_count')}** |")
    add(f"| Defects (critical/major/minor) | {summary['defects_by_severity']['critical']} / {summary['defects_by_severity']['major']} / {summary['defects_by_severity']['minor']} |")
    add(f"| HTTP errors from /chat | {summary.get('http_errors')} |")
    add(f"| Latency mean / p50 / p95 / max | {_num(lat.get('mean'), 0)} / {_num(lat.get('p50'), 0)} / {_num(lat.get('p95'), 0)} / {_num(lat.get('max'), 0)} ms |")
    add(f"| Judge LLM calls | {summary.get('llm_calls')} |")
    add(f"| Evaluation cost | ${summary.get('cost_usd')} |")
    add("")

    passed, reasons = gate(summary)
    add(f"**Quality gate: {'PASS' if passed else 'FAIL'}**")
    if reasons:
        add("")
        for reason in reasons:
            add(f"- {reason}")
    add("")

    add("## Rubric dimensions")
    add("")
    add("| Dimension | Mean | Read |")
    add("| --- | --- | --- |")
    for dim, score in sorted(summary["dimension_means"].items(), key=lambda kv: kv[1]):
        verdict = "healthy" if score >= 8 else "acceptable" if score >= 7 else "weak" if score >= 5.5 else "broken"
        add(f"| {dim} | {_num(score)} | {verdict} |")
    add("")

    add("## Deterministic assertions")
    add("")
    add("These are code-level checks with no LLM involved — they are the reproducible part of the suite.")
    add("")
    add("| Check | Severity | Failed / Run | Example |")
    add("| --- | --- | --- | --- |")
    for name, stat in sorted(
        summary["assertions"].items(), key=lambda kv: (-kv[1]["failed"], kv[0])
    ):
        example = (stat["examples"][0] if stat["examples"] else "").replace("|", "\\|")
        flag = " ⚠️" if stat["failed"] else ""
        add(f"| `{name}`{flag} | {stat['severity']} | {stat['failed']} / {stat['total']} | {example} |")
    add("")

    add("## Judge panel health")
    add("")
    add("| Judge model | Calls | Valid JSON | Mean score given |")
    add("| --- | --- | --- | --- |")
    for model, stat in summary["judge_health"].items():
        add(f"| `{model}` | {stat['calls']} | {_pct(stat['parse_rate'])} | {_num(stat['mean_score'])} |")
    add("")
    if summary["wide_disagreement_turns"]:
        add("Turns where the panel disagreed by 3+ points (worth human review):")
        add("")
        for item in summary["wide_disagreement_turns"]:
            add(f"- **{item['persona']}** turn {item['turn']} — spread {item['spread']}: {item['scores']}")
        add("")

    add("## Session scoreboard")
    add("")
    add("| Persona | Tags | Turns | Mean | Pass rate | Crit | Errors | Ended because |")
    add("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for session in sorted(sessions, key=lambda s: (s.mean_score is None, s.mean_score or 0)):
        crit = sum(1 for a in session.all_assertions if not a.passed and a.severity == "critical")
        add(
            f"| {session.persona.name} | {', '.join(session.persona.tags)} | {len(session.turns)} "
            f"| {_num(session.mean_score)} | {_pct(session.turn_pass_rate)} | {crit} "
            f"| {session.http_errors} | {session.end_reason} |"
        )
    add("")

    if summary["defects"]:
        add("## Critical and major defects")
        add("")
        add("| Persona | Turn | Severity | Dimension | Finding |")
        add("| --- | --- | --- | --- | --- |")
        for defect in sorted(
            summary["defects"], key=lambda d: {"critical": 0, "major": 1, "minor": 2}[d["severity"]]
        ):
            if defect["severity"] == "minor":
                continue
            desc = defect["description"].replace("|", "\\|").replace("\n", " ")[:260]
            add(f"| {defect['persona']} | {defect['turn']} | {defect['severity']} | {defect['dimension']} | {desc} |")
        add("")

    if baseline_diff:
        add("## Regression vs baseline")
        add("")
        add(f"Baseline captured {baseline_diff.get('baseline_generated_at')}")
        add("")
        add("| Metric | Baseline | Current | Delta |")
        add("| --- | --- | --- | --- |")
        for key, change in baseline_diff["changes"].items():
            arrow = "▲" if change["delta"] > 0 else ("▼" if change["delta"] < 0 else "=")
            add(f"| {key} | {change['baseline']} | {change['current']} | {arrow} {change['delta']:+} |")
        add("")
        if baseline_diff["regressions"]:
            add("**Regressions detected:**")
            add("")
            for regression in baseline_diff["regressions"]:
                add(f"- {regression}")
        else:
            add("No regressions against baseline.")
        add("")

    add("## Per-session detail")
    add("")
    for session in sessions:
        add(f"### {session.persona.name} — {session.persona.headline}")
        add("")
        add(
            f"`{session.session_id}` · language **{session.persona.language}** · "
            f"{len(session.turns)} turns · mean **{_num(session.mean_score)}** · "
            f"pass {_pct(session.turn_pass_rate)} · ended: {session.end_reason}"
        )
        add("")
        probe = session.probe
        add(
            f"**Persistence probe** — Postgres messages: {probe.db_messages}, "
            f"profile row: {probe.db_profile_present}, Redis key: {probe.redis_key_present}, "
            f"Redis/Postgres agree: {probe.redis_profile_matches_db}"
        )
        if probe.db_profile:
            add("")
            add(f"Stored profile: `{json.dumps(probe.db_profile, default=str)[:600]}`")
        for note in probe.notes:
            add("")
            add(f"> {note}")
        add("")
        failed_session = [a for a in session.session_assertions if not a.passed]
        if failed_session:
            add("**Failed session-level assertions:**")
            add("")
            for assertion in failed_session:
                add(f"- `{assertion.name}` ({assertion.severity}): {assertion.detail}")
            add("")

        for turn in session.turns:
            add(f"#### Turn {turn.index} — score {_num(turn.consensus_score)}"
                + (f" (spread {turn.score_spread})" if turn.score_spread else ""))
            add("")
            add(f"**Customer:** {turn.user_message}")
            add("")
            if turn.error:
                add(f"**BOT FAILED:** `{turn.error}`")
            else:
                add(f"**Bot:** {turn.bot_reply}")
            add("")
            add(f"`nodes: {turn.executed_nodes or '[]'}` · `intent: {turn.state.get('intent')}` · "
                f"`stage: {turn.state.get('sales_stage')}` · `latency: {turn.latency_ms:.0f}ms`"
                + (f" · `products: {len(turn.state.get('products') or [])}`" if turn.state.get("products") else ""))
            add("")
            failed = turn.failed_assertions
            if failed:
                add("Failed assertions: " + ", ".join(f"`{a.name}` ({a.detail[:90]})" for a in failed))
                add("")
            coaching = [v.coaching for v in turn.verdicts if v.coaching]
            if coaching:
                add(f"> Judge coaching: {coaching[0]}")
                add("")
        add("---")
        add("")

    return "\n".join(lines)


def write_reports(sessions: list[Session], meta: dict[str, Any], *, stamp: str | None = None) -> dict[str, Path]:
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary = aggregate(sessions)
    baseline_diff = diff_baseline(summary)

    payload = to_json(sessions, summary, meta)
    if baseline_diff:
        payload["baseline_diff"] = baseline_diff

    json_path = config.RESULTS_DIR / f"eval_{stamp}.json"
    md_path = config.RESULTS_DIR / f"eval_{stamp}.md"
    json_path.write_text(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    md_path.write_text(to_markdown(sessions, summary, meta, baseline_diff))

    # One plain-text transcript per session — the fastest way for a human to read
    # what the bot actually said.
    for session in sessions:
        lines = [f"# {session.persona.name} — {session.persona.headline}",
                 f"session_id: {session.session_id}", ""]
        for turn in session.turns:
            lines.append(f"[turn {turn.index}] CUSTOMER: {turn.user_message}")
            lines.append(f"[turn {turn.index}] BOT ({turn.latency_ms:.0f}ms, {turn.executed_nodes}): "
                         f"{turn.error or turn.bot_reply}")
            lines.append("")
        (config.TRANSCRIPT_DIR / f"{stamp}_{session.persona.name}.txt").write_text("\n".join(lines))

    return {"json": json_path, "markdown": md_path, "summary": summary}
