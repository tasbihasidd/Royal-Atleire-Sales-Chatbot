---
name: agent-hard-test-30
description: >-
  Runs the Royal Atelier 30-persona live real-LLM hard test against POST /chat
  (scripts/run_agent_hard_test_30.py), scores with score_agent_hard_test.py, and
  updates AGENT_HARD_TEST_REPORT.md. Use when asked to full hard-test, re-test
  after chatbot fixes, measure overall accuracy, or re-run weak personas only.
---

# Agent Hard Test 30 — Live Playbook

**You are a QA agent.** Measure the live sales chatbot. Prefer **not** editing `app/` while a measurement run is in progress unless the user asked to fix then re-test.

**Every bot reply must come from live `POST /chat`.** No invented transcripts.

Canonical narrative + last run scores/conversations: [`AGENT_HARD_TEST_REPORT.md`](../../../AGENT_HARD_TEST_REPORT.md).

---

## 1. Stack under test

| Piece | Detail |
| --- | --- |
| App | FastAPI + LangGraph (`app/main.py`, `app/agent/graph.py`) |
| Port | **8015** |
| Chat | `POST {BASE}/chat` JSON `{"session_id","message"}` |
| LLM | OpenRouter via `.env` (`OPENAI_MODEL`, etc.) |
| Catalogue | `BACKEND_API_BASE_URL` — must return **200** on `/products/search` (known good: `https://royal-attire-api.devssh.xyz/api/v2/sales-agent`) |
| Driver | [`scripts/run_agent_hard_test_30.py`](../../../scripts/run_agent_hard_test_30.py) |
| Scorer | [`scripts/score_agent_hard_test.py`](../../../scripts/score_agent_hard_test.py) |
| Artifacts | `test_results/agent_hard_test/agenthard_*.json|.txt` |

Start server with explicit backend if shell env may be stale:

```bash
BACKEND_API_BASE_URL=https://royal-attire-api.devssh.xyz/api/v2/sales-agent \
  .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8015
```

Confirm: `curl -s http://127.0.0.1:8015/health` → `{"status":"ok"}`.

---

## 2. Non-negotiable rules

1. **30 personas** in the driver (15 US / 8 PK / 7 IN). Do not invent replacements.
2. Each persona has **≥10 turns**; require mostly HTTP 200 replies.
3. New `session_id` every run (driver generates `agenthard_YYYYMMDD_Persona_hex`).
4. **Real LLM** on the bot path. Customer lines are scripted in the driver.
5. Before a **full** score of overall %, archive or isolate prior `agenthard_*.json` so the scorer does not mix old+new duplicates.
6. Do not commit secrets from `.env`.
7. After scoring, update **`AGENT_HARD_TEST_REPORT.md`**: keep section structure (issues, architecture, coverage, 3 weaknesses + solutions, how to run) and **preserve or regenerate full conversations** for the new run.

---

## 3. Commands

### Unit smoke

```bash
.venv/bin/python -m pytest tests/test_margin_and_prices.py -q
```

### Archive old transcripts (recommended before full-30)

```bash
ARCH=test_results/agent_hard_test/archive_$(date +%Y%m%d_%H%M%S)
mkdir -p "$ARCH"
mv test_results/agent_hard_test/agenthard_*.json "$ARCH/" 2>/dev/null || true
mv test_results/agent_hard_test/agenthard_*.txt "$ARCH/" 2>/dev/null || true
mv test_results/agent_hard_test/summary_index.json "$ARCH/" 2>/dev/null || true
```

### Weak / targeted retest (`--only` comma-separated)

```bash
.venv/bin/python scripts/run_agent_hard_test_30.py --concurrency 2 --only \
  "Confused_Farhan_Dikhao,US_Vague_Then_Dikhao,IN_Vague_Dikhao,Asad_Barat_GiftOnly,Usman_Discount_Hunter_PK,US_Discount_Hunter,James_Walima_Bespoke_NJ,Rizwan_Accessories_Only"
```

### Full 30 (proper overall accuracy)

```bash
.venv/bin/python scripts/run_agent_hard_test_30.py --concurrency 2 --batch all \
  2>&1 | tee test_results/agent_hard_test/full30_run_log.txt
```

Batches: `--batch us|pk|in|all`.

### Score

```bash
.venv/bin/python scripts/score_agent_hard_test.py
```

Then refresh `AGENT_HARD_TEST_REPORT.md` narrative (architecture, weaknesses, run instructions) **and** include each session’s turn-by-turn conversation from the new JSON files.

---

## 4. Pass / fail bar

| Check | Bar |
| --- | --- |
| Overall mean (full 30) | Prefer **≥ 85%** |
| Cash-offer critical | **0** |
| HTTP 500 / hard reliability fail | **0** preferred |
| R13 floor/margin/system-prompt leak | **0** |
| Dikhao / vague | After dikhao, products named when catalogue returns rows; no event-menu spam |
| Gift path | Named complimentary accessory **or** one honest empty+consultant line — never invent, never cash % |

---

## 5. Regression guards (do not break)

When the user asked for fixes then re-test, keep:

- Empty-only search widen (drop filters **only** on 0 hits)
- Dikhao must-show via **prompt** (no new hardcoded category map)
- Gift-only negotiation (no cash %)
- Accessories gift retry without `event_type` still margin-capped
- FX / display passthrough (no invent convert)

---

## 6. Progress checklist

```
[ ] health 200 on :8015
[ ] backend /products/search not 404
[ ] unit smoke green
[ ] archived old agenthard_* if measuring overall
[ ] run weak-only OR full-30
[ ] score_agent_hard_test.py
[ ] AGENT_HARD_TEST_REPORT.md updated (narrative + conversations)
[ ] call out remaining weaknesses with quotes
```

---

## 7. Related skill

Older 10-session playbook (different roster): [`.cursor/skills/chatbot-sales-audit/SKILL.md`](../chatbot-sales-audit/SKILL.md). Prefer **this** skill for the 30-persona driver and `AGENT_HARD_TEST_REPORT.md`.
