#!/usr/bin/env python
"""
Entry point for the Royal Atelier chatbot evaluation suite.

    uv run python -m tests.eval_suite.run_eval                 # all 20 personas
    uv run python -m tests.eval_suite.run_eval --fast          # 4 personas, 1 judge
    uv run python -m tests.eval_suite.run_eval --tags negotiation budget_objection
    uv run python -m tests.eval_suite.run_eval --personas Asad_Barat_Negotiator
    uv run python -m tests.eval_suite.run_eval --save-baseline  # record today as the bar
    uv run python -m tests.eval_suite.run_eval --preflight-only

Exit codes: 0 gate passed · 1 gate failed · 2 preflight failed · 3 harness error.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tests.eval_suite import config, personas, probes, reporting, runner  # noqa: E402


def _setup_logging(verbose: bool) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    handlers: list[logging.Handler] = [
        logging.FileHandler(config.LOG_DIR / f"eval_{stamp}.log", encoding="utf-8")
    ]
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO if verbose else logging.WARNING)
    handlers.append(console)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Royal Atelier chatbot evaluation suite")
    parser.add_argument("--personas", nargs="*", help="Run only these persona names")
    parser.add_argument("--tags", nargs="*", help="Run only personas carrying any of these tags")
    parser.add_argument("--fast", action="store_true",
                        help="Smoke mode: 4 representative personas, single judge, 6 turns")
    parser.add_argument("--max-turns", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--single-judge", action="store_true", help="Use one judge instead of the panel")
    parser.add_argument("--save-baseline", action="store_true",
                        help="Store this run's summary as the regression baseline")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--skip-preflight", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args()


async def main() -> int:
    args = _parse_args()
    _setup_logging(not args.quiet)
    log = logging.getLogger("eval")

    problems = config.validate()
    if problems:
        for problem in problems:
            print(f"CONFIG ERROR: {problem}", file=sys.stderr)
        return 2

    if not args.skip_preflight:
        print("Running preflight checks...")
        report = await probes.preflight()
        for name, check in report["checks"].items():
            mark = "ok  " if check["ok"] else "FAIL"
            print(f"  [{mark}] {name}: {json.dumps(check['detail'], default=str)[:150]}")
        if not report["ok"]:
            print("\nPreflight failed. Fix the above before spending LLM budget.", file=sys.stderr)
            return 2
        if args.preflight_only:
            return 0
        print()

    roster = personas.select(names=args.personas, tags=args.tags)
    panel = config.JUDGE_PANEL

    if args.fast:
        roster = personas.select(names=[
            "Asad_Barat_Negotiator",
            "Kamran_Budget_TooLow",
            "Haris_Variations",
            "Troll_Adversarial",
        ])
        panel = (config.FAST_JUDGE,)
        config.MAX_TURNS = 6
    if args.single_judge:
        panel = (config.FAST_JUDGE,)
    if args.max_turns:
        config.MAX_TURNS = args.max_turns

    if not roster:
        print("No personas matched the filter.", file=sys.stderr)
        return 3

    concurrency = args.concurrency or config.SESSION_CONCURRENCY
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print(f"Running {len(roster)} session(s), up to {config.MAX_TURNS} turns each, "
          f"concurrency {concurrency}")
    print(f"Judge panel: {', '.join(panel)}")
    print(f"Customer simulator: {config.USER_SIM_MODEL}")
    print()

    started = time.perf_counter()
    sessions = await runner.run_suite(
        roster, concurrency=concurrency, panel=panel, run_tag=stamp[-6:]
    )
    elapsed = time.perf_counter() - started

    meta = {
        "chatbot_url": config.CHATBOT_BASE_URL,
        "chatbot_model_under_test": __import__("os").getenv("OPENAI_MODEL", "unknown"),
        "user_simulator": config.USER_SIM_MODEL,
        "judge_panel": ", ".join(panel),
        "personas_run": len(roster),
        "max_turns": config.MAX_TURNS,
        "concurrency": concurrency,
        "wall_clock_seconds": round(elapsed, 1),
    }

    artifacts = reporting.write_reports(sessions, meta, stamp=stamp)
    summary = artifacts["summary"]
    passed, reasons = reporting.gate(summary)

    print()
    print("=" * 72)
    print(f"Sessions {summary['sessions']} · turns {summary['turns']} · "
          f"mean score {summary['mean_score']} · pass rate {summary['turn_pass_rate']}")
    print(f"Critical findings {summary['critical_count']} · HTTP errors {summary['http_errors']} · "
          f"p95 latency {summary['latency_ms']['p95']}ms")
    print(f"Cost ${summary['cost_usd']} across {summary['llm_calls']} LLM calls · "
          f"wall clock {elapsed / 60:.1f} min")
    print(f"Gate: {'PASS' if passed else 'FAIL'}")
    for reason in reasons:
        print(f"  - {reason}")
    print(f"\nMarkdown: {artifacts['markdown']}\nJSON:     {artifacts['json']}")
    print("=" * 72)

    if args.save_baseline:
        config.BASELINE_PATH.write_text(
            json.dumps(
                {"generated_at": datetime.now(timezone.utc).isoformat(), "meta": meta, "summary": summary},
                indent=2, default=str,
            )
        )
        print(f"Baseline saved to {config.BASELINE_PATH}")

    log.info("Run complete passed=%s", passed)
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(3)
