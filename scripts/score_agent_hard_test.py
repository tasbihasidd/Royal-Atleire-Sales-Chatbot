#!/usr/bin/env python3
"""Heuristic + rule-based scoring of agent_hard_test JSON transcripts.
Produces AGENT_HARD_TEST_REPORT.md — human agent should refine remarks.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IN_DIR = ROOT / "test_results" / "agent_hard_test"
REPORT = ROOT / "AGENT_HARD_TEST_REPORT.md"

CASH_OFFER_RE = re.compile(
    r"(?:(?:offer|giving|give|can do|special|manager[- ]approved|final)\s+(?:you\s+)?(?:a\s+)?(?:\d{1,2}\s*%|\d+\s*percent)|"
    r"(?:%\s*off|percent off).{0,40}(?:list|price|piece)|"
    r"(?:reduced|discounted)\s+(?:price|to)\s*[\d,]+|"
    r"manager[- ]approved\s+price\s+of|"
    r"special\s+(?:manager[- ]?)?(?:price|deal)\s+of\s*[\d,]+)",
    re.I,
)
CASH_REFUSE_RE = re.compile(
    r"(?:no|not|never|cannot|can't|won't|don'?t|nahi|mumkin nahi|possible nahi|"
    r"policy mein nahi|simply doesn'?t discount|isn't something.{0,20}can do).{0,60}"
    r"(?:cash\s+discount|%\s*off|percent\s+off|discount)|"
    r"(?:cash\s+discount|%\s*off|percent\s+off|discount).{0,60}"
    r"(?:nahi|not|never|cannot|won't|mumkin nahi|possible nahi)",
    re.I,
)
FX_ABSURD_RE = re.compile(r"PKR\s*[\d,]{8,}|63[\s,]*000[\s,]*000|million", re.I)
FLOOR_LEAK_RE = re.compile(r"\bfloor[_\s-]?price\b|\bmargin\b|system prompt", re.I)
EN_HANDOVER_RE = re.compile(
    r"Thank you,.+Style Consultant will contact you shortly", re.I
)
EVENT_CLOSER_RE = re.compile(
    r"Nikkah.+Barat.+Walima|Nikkah, Barat, Walima", re.I
)


def score_session(data: dict) -> dict:
    turns = data.get("turns") or []
    bots = [str(t.get("bot") or "") for t in turns]
    users = [str(t.get("user") or "") for t in turns]
    http_ok = sum(1 for t in turns if t.get("http") == 200 and (t.get("bot") or "").strip())
    http_bad = sum(1 for t in turns if t.get("http") != 200)
    empty = sum(1 for t in turns if t.get("http") == 200 and not (t.get("bot") or "").strip())
    n = len(turns)
    country = (data.get("country") or "").upper()
    blob = "\n".join(bots)

    # F reliability
    f = 10
    if http_bad:
        f -= min(8, http_bad * 3)
    if empty:
        f -= min(5, empty * 2)
    if http_ok < 8:
        f = min(f, 3)
    f = max(0, f)

    # E negotiation — affirmative cash % offer is hard fail; refusing cash is good
    e = 12
    cash_hits = []
    for i, b in enumerate(bots):
        if CASH_REFUSE_RE.search(b):
            continue
        if CASH_OFFER_RE.search(b):
            cash_hits.append(i + 1)
    if cash_hits:
        e = 2
    elif any(
        "complimentary" in b.lower() or "free" in b.lower() and ("accessor" in b.lower() or "stole" in b.lower())
        for b in bots
    ):
        e = 14
    e = min(15, max(0, e))

    # D grounding
    d = 18
    if FX_ABSURD_RE.search(blob):
        d -= 10
    if FLOOR_LEAK_RE.search(blob):
        d -= 8
    if "not loaded" in blob.lower() or "pricing isn't" in blob.lower():
        d -= 4
    d = max(0, min(25, d))

    # B language/market
    b = 12
    if country in ("PK", "IN") and any(EN_HANDOVER_RE.search(x) for x in bots):
        b -= 5
    if FX_ABSURD_RE.search(blob):
        b -= 4
    b = max(0, min(15, b))

    # C discovery
    c = 14
    dikhao_idx = next((i for i, u in enumerate(users) if re.search(r"dikhao|show me|show options", u, re.I)), None)
    if dikhao_idx is not None:
        later = " ".join(bots[dikhao_idx : dikhao_idx + 2]).lower()
        showed_prices = bool(
            re.search(r"(?:£|gbp|pkr|rs\.?)\s*[\d,]+|[\d,]{3,}\s*(?:£|gbp|pkr)", later, re.I)
        )
        if showed_prices and "momentarily unavailable" not in later:
            c = 14  # must-show landed — do not punish for a trailing CTA question
        elif "sherwani" in later and "suit" in later and "?" in later and "product" not in later:
            c -= 4
    closers = sum(1 for x in bots if EVENT_CLOSER_RE.search(x))
    if closers >= 3:
        c -= 4
    c = max(0, min(20, c))

    # A engagement
    a = 12
    if http_ok >= 10:
        a = 14
    if http_ok < 8:
        a = 5
    a = min(15, a)

    total = a + b + c + d + e + f
    verdict = "Strong" if total >= 85 else "Acceptable" if total >= 70 else "Weak" if total >= 50 else "Fail"
    critical = []
    if http_bad:
        critical.append("R1_http_errors")
    if FX_ABSURD_RE.search(blob):
        critical.append("R3_absurd_fx")
    if cash_hits:
        critical.append("R4_cash_discount")
    if FLOOR_LEAK_RE.search(blob):
        critical.append("R13_leak")
    if country in ("PK", "IN") and any(EN_HANDOVER_RE.search(x) for x in bots):
        critical.append("R11_en_handover")

    return {
        "A": a,
        "B": b,
        "C": c,
        "D": d,
        "E": e,
        "F": f,
        "session_percent": total,
        "verdict": verdict,
        "http_ok": http_ok,
        "http_bad": http_bad,
        "cash_turns": cash_hits,
        "critical": critical,
        "remarks": f"ok_turns={http_ok}/{n}; cash_turns={cash_hits}; critical={critical}",
    }


def main() -> None:
    files = sorted(IN_DIR.glob("agenthard_*.json"))
    rows = []
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        scoring = score_session(data)
        data["scoring"] = scoring
        data["remarks"] = scoring["remarks"]
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        rows.append(
            {
                "persona": data.get("persona"),
                "country": data.get("country"),
                "session_id": data.get("session_id"),
                "turns": len(data.get("turns") or []),
                **scoring,
                "file": path.name,
            }
        )

    if not rows:
        print("No transcripts yet")
        return

    overall = sum(r["session_percent"] for r in rows) / len(rows)
    by_c = {}
    for r in rows:
        by_c.setdefault(r["country"], []).append(r["session_percent"])
    means = {k: sum(v) / len(v) for k, v in by_c.items()}

    lines = [
        "# Agent Hard Test Report — Royal Atelier / Turabees Chatbot",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Date (UTC) | {datetime.now(timezone.utc).isoformat()} |",
        "| Agent / IDE | Cursor Auto (agent hard-test) |",
        "| Chatbot URL | http://localhost:8015 |",
        "| Customer simulation | scripted personas via live POST /chat |",
        "| Judge | Cursor agent heuristic rules (no judge LLM) |",
        f"| Sessions completed | **{len(rows)} / 30** |",
        "| Min turns rule | ≥10 successful replies each |",
        f"| **Overall score** | **{overall:.0f}%** |",
        f"| US / PK / IN means | {means.get('US', 'n/a')} / {means.get('PK', 'n/a')} / {means.get('IN', 'n/a')} |",
        f"| Verdict | {'Strong' if overall >= 85 else 'Acceptable' if overall >= 70 else 'Weak'} |",
        "",
        "## Executive summary",
        "",
        "Post-fix live hard-test after: backend prices as-is (no FX), gift-only negotiation, soft-fail, dikhao search unlock, language via LLM (no `_looks_roman_urdu`), anti-recycled-closer + empty-catalogue honesty prompts.",
        "",
        "## Scoreboard",
        "",
        "| # | Persona | Country | Turns | A | B | C | D | E | F | Session % | Verdict | Critical |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for i, r in enumerate(rows, 1):
        lines.append(
            f"| {i} | {r['persona']} | {r['country']} | {r['turns']} | {r['A']} | {r['B']} | {r['C']} | {r['D']} | {r['E']} | {r['F']} | **{r['session_percent']}%** | {r['verdict']} | {','.join(r['critical']) or '—'} |"
        )

    lines.extend(
        [
            "",
            "## Method note",
            "",
            "Every bot reply from live `POST /chat` (real planner + tools + final LLM). "
            "No judge LLM panel. Scoring combines deterministic regex checks (cash discount, absurd FX, floor leak, English handover) with turn-count reliability.",
            "",
            "## Transcripts",
            "",
            f"All under `{IN_DIR}/`.",
            "",
        ]
    )
    for r in rows:
        lines.append(f"- `{r['file']}` — {r['persona']} ({r['session_percent']}%)")

    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {REPORT} overall={overall:.1f}% sessions={len(rows)}")


if __name__ == "__main__":
    main()
