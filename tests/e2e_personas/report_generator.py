"""
Report generator — produces Markdown + JSON diagnostic reports.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from tests.e2e_personas.config import RESULTS_DIR
from tests.e2e_personas.conversation_runner import ConversationResult, TurnRecord

logger = logging.getLogger(__name__)


def _dimension_table_row(dim_name: str, dim_data: dict) -> str:
    """Build a single row for the dimension score table."""
    if not isinstance(dim_data, dict):
        return f"| {dim_name} | — | — | — |"
    score = dim_data.get("score")
    passed = dim_data.get("pass")
    note = dim_data.get("note", "")
    pass_icon = "✅" if passed else ("❌" if passed is False else "—")
    score_str = f"{score}/10" if score is not None else "—"
    return f"| {dim_name} | {pass_icon} | {score_str} | {note} |"


def _format_turn(turn: TurnRecord) -> str:
    """Format a single turn for the Markdown report."""
    v = turn.verdict
    v_pass = v.get("overall_pass")
    pass_icon = "✅" if v_pass else ("❌" if v_pass is False else "⚠️")
    score = v.get("score", 0)

    lines = [
        f"### Turn {turn.turn_number} {pass_icon} (Score: {score}/10, Latency: {turn.latency_ms}ms)",
        "",
        f"**👤 User:** {turn.user_message}",
        "",
        f"**🤖 Bot:** {turn.bot_reply}",
        "",
        f"**Nodes:** `{', '.join(turn.executed_nodes)}`",
        "",
    ]

    if turn.image_url:
        lines.append(f"**Image:** {turn.image_url}")
        lines.append("")

    # Dimension scores table
    dims = v.get("dimensions", {})
    if dims:
        lines.append("| Dimension | Pass | Score | Note |")
        lines.append("|-----------|------|-------|------|")
        for dim_name, dim_data in dims.items():
            lines.append(_dimension_table_row(dim_name, dim_data))
        lines.append("")

    # Defects
    defects = v.get("defects", [])
    if defects:
        lines.append("**🐛 Defects:**")
        for d in defects:
            lines.append(f"- {d}")
        lines.append("")

    # Suggestions
    suggestions = v.get("suggestions", [])
    if suggestions:
        lines.append("**💡 Suggestions:**")
        for s in suggestions:
            lines.append(f"- {s}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


def generate_report(results: list[ConversationResult]) -> tuple[Path, Path]:
    """
    Generate Markdown and JSON reports from conversation results.
    Returns (markdown_path, json_path).
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = RESULTS_DIR / f"report_{timestamp}.md"
    json_path = RESULTS_DIR / f"report_{timestamp}.json"

    # ── Aggregate stats ──────────────────────────────────────────────
    total_turns = sum(len(r.turns) for r in results)
    total_defects = sum(len(r.defects) for r in results)
    all_scores = [
        t.verdict.get("score", 0) for r in results for t in r.turns if t.verdict.get("score")
    ]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    all_passes = [
        t.verdict.get("overall_pass") for r in results for t in r.turns
    ]
    passed_count = sum(1 for p in all_passes if p is True)
    overall_pass_rate = (passed_count / len(all_passes) * 100) if all_passes else 0

    # Dimension-level aggregate
    dim_scores: dict[str, list[int]] = {}
    for r in results:
        for t in r.turns:
            for dim_name, dim_data in t.verdict.get("dimensions", {}).items():
                if isinstance(dim_data, dict) and dim_data.get("score") is not None:
                    dim_scores.setdefault(dim_name, []).append(dim_data["score"])

    # ── Markdown Report ──────────────────────────────────────────────
    md_lines = [
        "# 🧪 Royal Atelier E2E Test Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## Executive Summary",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Total Personas | {len(results)} |",
        f"| Total Turns | {total_turns} |",
        f"| Average Score | {avg_score:.1f}/10 |",
        f"| Overall Pass Rate | {overall_pass_rate:.0f}% |",
        f"| Total Defects | {total_defects} |",
        "",
    ]

    # Session IDs table
    md_lines.append("## Session IDs")
    md_lines.append("")
    md_lines.append("| Persona | Session ID |")
    md_lines.append("|---------|------------|")
    for r in results:
        md_lines.append(f"| {r.persona_name} | `{r.session_id}` |")
    md_lines.append("")

    # Dimension Heatmap
    if dim_scores:
        md_lines.append("## Dimension Score Heatmap (Averages)")
        md_lines.append("")
        md_lines.append("| Dimension | Avg Score | Min | Max |")
        md_lines.append("|-----------|-----------|-----|-----|")
        for dim_name, scores in sorted(dim_scores.items()):
            avg = sum(scores) / len(scores)
            icon = "🟢" if avg >= 8 else ("🟡" if avg >= 6 else "🔴")
            md_lines.append(
                f"| {icon} {dim_name} | {avg:.1f}/10 | {min(scores)} | {max(scores)} |"
            )
        md_lines.append("")

    # All Defects Table
    all_defects = []
    for r in results:
        all_defects.extend(r.defects)

    if all_defects:
        md_lines.append("## 🐛 All Defects")
        md_lines.append("")
        md_lines.append("| # | Persona | Turn | Defect |")
        md_lines.append("|---|---------|------|--------|")
        for i, d in enumerate(all_defects, 1):
            md_lines.append(f"| {i} | {d['persona']} | {d['turn']} | {d['defect']} |")
        md_lines.append("")

    # Per-Persona Sections
    for r in results:
        md_lines.append(f"## Persona: {r.persona_name}")
        md_lines.append("")
        md_lines.append(f"**Description:** {r.persona_description}")
        md_lines.append(f"**Session ID:** `{r.session_id}`")
        md_lines.append(f"**Turns:** {len(r.turns)}")
        md_lines.append(f"**Average Score:** {r.total_score:.1f}/10")
        md_lines.append(f"**Pass Rate:** {r.pass_rate:.0f}%")
        md_lines.append(f"**Defects:** {len(r.defects)}")
        md_lines.append("")

        for turn in r.turns:
            md_lines.append(_format_turn(turn))

    md_content = "\n".join(md_lines)
    md_path.write_text(md_content, encoding="utf-8")

    # ── JSON Report ──────────────────────────────────────────────────
    json_data = {
        "generated_at": datetime.now().isoformat(),
        "summary": {
            "total_personas": len(results),
            "total_turns": total_turns,
            "average_score": round(avg_score, 2),
            "overall_pass_rate": round(overall_pass_rate, 2),
            "total_defects": total_defects,
        },
        "session_ids": {r.persona_name: r.session_id for r in results},
        "dimension_averages": {
            dim: round(sum(scores) / len(scores), 2)
            for dim, scores in dim_scores.items()
        },
        "all_defects": all_defects,
        "personas": [
            {
                "name": r.persona_name,
                "description": r.persona_description,
                "session_id": r.session_id,
                "total_score": round(r.total_score, 2),
                "pass_rate": round(r.pass_rate, 2),
                "defect_count": len(r.defects),
                "turns": [
                    {
                        "turn_number": t.turn_number,
                        "user_message": t.user_message,
                        "bot_reply": t.bot_reply,
                        "executed_nodes": t.executed_nodes,
                        "image_url": t.image_url,
                        "latency_ms": t.latency_ms,
                        "verdict": t.verdict,
                    }
                    for t in r.turns
                ],
            }
            for r in results
        ],
    }
    json_path.write_text(json.dumps(json_data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    logger.info("Reports written: %s, %s", md_path, json_path)
    return md_path, json_path
