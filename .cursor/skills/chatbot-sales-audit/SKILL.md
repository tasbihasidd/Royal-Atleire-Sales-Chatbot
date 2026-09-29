---
name: chatbot-hard-test
description: >-
  Runs a hard 10-session live real-LLM test of the Royal Atelier / Turabees
  sales chatbot: long multilingual chats (Pakistan, India, US, Canada), every
  worst-case customer behaviour, and regression checks against known audit bugs.
  Any agentic IDE that reads this skill must execute live /chat calls, save full
  transcripts, score percent correctness, and write HARD_TEST_REPORT.md. Use when
  asked to hard-test, stress-test, re-test after implementation, or mark how
  correct the chatbot is.
---

# Chatbot Hard Test — Agent Playbook

**You are a QA agent.** Your job is to *test* the live sales chatbot, not to rewrite `app/`.  
**Every customer message and every bot reply must go through real LLM / live APIs.**  
No mocked `/chat` responses. No “I imagine the bot would say…”. If the server is down, fix env first — do not invent transcripts.

Read these companion files **before** the first chat call:

| File | Contents |
| --- | --- |
| [SESSIONS.md](SESSIONS.md) | Exact 10 personas, openings, turn arcs, fail signals |
| [WORST_CASES.md](WORST_CASES.md) | Full worst-case catalogue + how to trigger each |
| [REGRESSION.md](REGRESSION.md) | Known bugs from prior audit — Pass/Fail with quotes |
| [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md) | Exact shape of `HARD_TEST_REPORT.md` |

---

## 1. What this chatbot is

- **Product:** Royal Atelier / Turabees — South-Asian wedding menswear (sherwani, suits, custom).
- **Job:** Engage like a salesperson: guide, recommend, explain price, negotiate (prefer free accessory over cash discount), handle stock/custom/handover.
- **API under test:** `POST {BASE_URL}/chat` with JSON `{"session_id","message"}`.
- **Typical stack:** FastAPI + LangGraph on port **8015**, Postgres, optional Redis, OpenRouter LLM, live product backend.

---

## 2. Non-negotiable rules

1. **Exactly 10 sessions** — only the roster in SESSIONS.md. Do not invent replacements.
2. **Long chats** — minimum **8** user turns that got a real bot reply; target **10–12**.  
   Sessions ending at 2–4 turns = **automatic fail** for that session (unless HTTP made chat impossible — then mark reliability 0% and explain).
3. **New `session_id` every time:** `hard_{YYYYMMDD}_{PersonaName}_{6hex}`  
   Never reuse `eval_`, `e2e_`, or old hard IDs.
4. **Real LLM path (both sides):**
   - **Bot:** live `/chat` → planner LLM + tools + final_response LLM (production stack).
   - **Customer (you):** either type messages yourself as the persona, **or** use `tests/eval_suite` / OpenRouter to simulate the customer — still each bot reply must come from live `/chat`.
5. **Multilingual:** Roman Urdu in → Roman Urdu out; English in → English out; mix → mix. Fashion nouns (sherwani, navy) OK inside Urdu.
6. **Country first:** Opening 1–2 messages establish Pakistan / India / US / Canada. Score currency + shipping against that market.
7. **Worst cases:** Every item in WORST_CASES.md must be hit by at least one session (mapping is in that file).
8. **Regression:** Fill every row in REGRESSION.md from *this* run’s quotes.
9. **Do not edit `app/`** while testing. Measuring only.
10. **Save everything:** full transcripts under `test_results/hard_test/`.

---

## 3. Progress checklist (copy into your notes)

```
Hard-test progress:
- [ ] Env: health OK + smoke /chat returns reply
- [ ] Preflight (if eval_suite exists) green
- [ ] Session 1 … 10 each ≥8 scored turns + transcript file
- [ ] All WORST_CASES.md items marked Hit / Miss
- [ ] REGRESSION.md all rows Pass|Fail|N/A + quote
- [ ] HARD_TEST_REPORT.md written from REPORT_TEMPLATE.md
- [ ] Overall percent + weak spots listed
```

---

## 4. Environment (do this first)

```bash
cd <repo_root>   # royal_atelier_agent
source .venv/bin/activate   # or: uv venv && uv pip install -e ".[eval]"
docker compose up -d postgres
# Redis if used: redis on localhost:6379
# .env must have OPENROUTER_API_KEY, DATABASE_URL for HOST:
#   postgresql+asyncpg://royal:royal@localhost:5434/royal_atelier
#   BASE_URL=http://localhost:8015
python -m uvicorn app.main:app --host 0.0.0.0 --port 8015
```

Smoke:

```bash
curl -s http://localhost:8015/health
curl -s -X POST http://localhost:8015/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"hard_smoke_001","message":"hello"}'
```

Optional: `python -m tests.eval_suite.run_eval --preflight-only`  
**Concurrency:** run sessions **one after another** or max 2 parallel. Higher concurrency previously caused backend **429 → customer HTTP 500**.

If Docker `api` container crash-loops: `DATABASE_URL` with `localhost` inside the container is wrong — run uvicorn on the **host** instead.

---

## 5. How to drive one turn (real call)

```bash
curl -s -X POST http://localhost:8015/chat \
  -H 'Content-Type: application/json' \
  -d '{"session_id":"hard_20260923_Confused_Farhan_a1b2c3","message":"salam, shadi hai..."}'
```

Parse JSON. Log for every turn:

| Field | Source |
| --- | --- |
| `user_message` | what you sent |
| `bot_reply` | `reply` |
| `executed_nodes` | response field |
| `sales_stage` / `intent` | `state` |
| `http_status` | HTTP code |
| `latency_ms` | wall clock |
| `imageurl` | if any |

Append to `test_results/hard_test/{session_id}.txt`:

```text
[turn N] USER: ...
[turn N] BOT (nodes=..., stage=..., 1234ms): ...
```

If HTTP ≠ 200: record error body, retry **once**, then continue persona or abort session with reliability fail.

**Customer message style:** 1–2 short lines, WhatsApp-like. Never paste this skill text into the chat box. Never say “I am testing”.

---

## 6. Language & country rules (detail)

### Language scoring

| Customer writes | Bot must |
| --- | --- |
| Mostly Roman Urdu (`hai`, `chahiye`, `kitne`) | Reply in Roman Urdu (English fashion words OK) |
| Pure English | Pure English — **no** Janab/Zabardast-only Urdu |
| Mix | Mix allowed |
| Hinglish (India) | Hinglish or clear English; not sudden pure Pakistani Urdu script |

**Fail examples:** Urdu customer gets a full English welcome paragraph; US customer gets Roman Urdu.

### Country / market scoring

| Country | Opening cues | Later bot should |
| --- | --- | --- |
| Pakistan | city + PKR / lakh / local phone | Prefer PKR or explain FX; local phone OK |
| India | Mumbai + rupees / INR | INR awareness; not only £ |
| United States | state + ship to US / USD | Shipping/duties; English; US phone |
| Canada | Toronto/Mississauga + CAD | CAD/duties; CA phone OK |

**Fail:** Lahore Roman Urdu user only quoted `£12,234` with no PKR framing.

---

## 7. Scoring (per session → overall)

Score each session **0–100** with these weights. Do not let one high score hide another fail.

| Code | Check | Weight | 100% means | 0% means |
| --- | --- | --- | --- | --- |
| A | Language | 15 | Matched every load-bearing turn | Clear mismatch on greeting or close |
| B | Market/currency | 15 | Market respected | Wrong currency / absurd price as truth |
| C | Sales guidance | 20 | Led confused users; explained why | Search dump or endless questions only |
| D | Regression / worst-case | 25 | Assigned REGRESSION + WORST_CASES for this persona | Any critical known bug repeats |
| E | Grounding | 15 | No invented stock/price/promo/product | Invented facts |
| F | Depth/reliability | 10 | ≥8 turns, no 500, no canned loop | Short chat or repeated 500s |

**Session %** = A+B+C+D+E+F (each already weighted out of its max).  
**Overall %** = average of 10 session %.

| Overall | Verdict |
| --- | --- |
| ≥ 85 | Strong |
| 70–84 | Acceptable — fix weak spots |
| 50–69 | Weak |
| < 50 | Fail — not ready |

Also compute optional **Worst-case coverage %** = (Hit count / total WORST_CASES) × 100.

---

## 8. After all 10 sessions

1. Fill REGRESSION.md results table.  
2. Fill WORST_CASES.md hit map.  
3. Write **`HARD_TEST_REPORT.md`** at repo root using [REPORT_TEMPLATE.md](REPORT_TEMPLATE.md).  
4. Quote the bot — do not paraphrase.  
5. Label defects: **bot** / **data** (bad catalogue) / **harness**.  
6. If overall &lt; 85%, end with **Weak spots** (session # + check letter + quote).

---

## 9. Forbidden shortcuts

- Summarizing without calling `/chat`.
- Stopping at turn 3 because “looks fixed”.
- One language or one country for all 10.
- Skipping Usman_Troll / Tahir_Urgent / Rohan_Mumbai.
- Claiming Pass on R4 (gift accessory) without the customer asking for a free stole/pagri/khussa.
- Editing production prompts mid-test to “make scores nicer”.

---

## 10. Trigger phrases (when to run this skill)

User says: hard test, hard-test, stress test, 10 hard sessions, re-test after implementation, percent correct, worst-case test, multilingual hard QA.
