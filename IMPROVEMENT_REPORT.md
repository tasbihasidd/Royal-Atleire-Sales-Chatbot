# Turabees / Royal Atelier Chatbot — Improvement Report

**Scope:** Architecture + live behaviour. Application code was **not** modified during the audit; fixes listed here are recommendations.  
**Evidence:** Static analysis of `app/` + real LLM sessions via `tests/eval_suite/` (20 personas, OpenRouter judges).  
**Environment note:** App run via `uv` + host uvicorn; Postgres (docker compose :5434); Redis (standalone :6379). Docker `api` container was crash-looping on wrong `DATABASE_URL` (`localhost` inside container).

---

## Executive verdict

The agent is a **hybrid LLM planner + heavy Python post-processing** sales FSM. Happy-path engagement (greeting, discovery, Roman Urdu tone, basic search, jailbreak deflection) is often **good (≈7.5–9)**. It fails commercially where the business cares most:

1. **Reliability under load** — backend 429s become customer HTTP 500s (no retry).  
2. **Catalogue / currency truth** — synthetic prices + GBP catalogue shown to PKR Roman-Urdu users.  
3. **Budget & negotiation salescraft** — weak downsell; accessory-as-gift exists in code but is fragile / incomplete vs marketing policy.  
4. **Overengineering & dual state** — Redis optional and misconfigured; dead scoring/tools; session context keys drop custom design fields.

---

## P0 — Fix before more “prompt tuning”

### 1. Backend 429 → unhandled → `/chat` 500

**Evidence:** During the 20-session run, API logs showed **hundreds of `429 Too Many Requests`** against `royal-attire-api.devssh.xyz` (products/search, details, fabrics, handover, accessories). Each bubble up as `Chat request failed` → client HTTP 500. Sessions aborted after 3 consecutive errors.

**Root cause:** `app/services/backend_api.py` `_request` has **no retry, no 429 backoff**, new `httpx.AsyncClient` per call, `timeout=20`. Tool nodes re-raise.

**Fix:**
- Retry 429/5xx with exponential backoff + `Retry-After`.  
- Degrade gracefully in nodes: return a structured error to `final_response` instead of crashing the turn.  
- Cache `list_categories` / `list_variations` for the process lifetime (planner fetches every turn).  
- Eval: run at `--concurrency 2`; gate on `http_ok` / `no_server_errors`.

### 2. Synthetic / default prices presented as catalogue truth

**Evidence:** `backend_api._normalize_product` injects category defaults when `price <= 0` (e.g. Sherwani GBP 2400 / PKR 150000). Live search also returned real but absurd GBP figures (e.g. £12,234 and £180,000 on sherwanis) which the bot quoted faithfully.

**Fix:**
- Never invent a sellable price. If missing → `"price_unavailable": true` and prompt the LLM to say it will confirm with a consultant.  
- Surface `_has_db_price` to the final prompt as a hard rule.  
- Separate ticket for **backend data quality** (GBP vs PKR, zero prices, empty `suitable_events`).

### 3. Currency localisation

**Evidence:** Roman-Urdu Barat shoppers quoted in GBP. Assertion `currency_matches_market` / `prices_plausible_for_currency` exist to catch this.

**Fix:** Detect market from language / `BASE` config / user cues; convert or filter currency; never mix without explanation.

### 4. Session context silent drops

**Evidence:** `build_session_context_from_result` writes `custom_image_url`, `custom_design_result`, `custom_instructions`, but `SESSION_CONTEXT_KEYS` **excludes** them → dropped on `save_session_context`. Variation IDs are in the allow-list but inconsistently written. Dual negotiation state: `memory_service` UserProfile vs `session_context` — `main.py` prefers session_context if present (stale override risk).

**Fix:** Unify one write path; include custom design + variation fields in allow-list; single source for negotiation round.

---

## P1 — Sales capability (owner requirements)

### 5. Budget too low → navigate to different options

**Target persona:** `Kamran_Budget_TooLow` (first-run mean ≈ **5.9**).

**Today:** Discovery/search may show out-of-budget pieces or dead-end; weak structured downsell.

**Need:** Explicit ladder — acknowledge budget → show what exists in band → if empty, cheapest adjacent + “increase by X for Y fabric” → optional accessories-only / lighter Mehndi wear → handover. Deterministic planner rule when `budget` set and search empty.

### 6. Price shock → justify cost (not jump to discount)

**Target:** `Faisal_PriceShock`.

**Today:** Negotiation ladder exists (`negotiation_engine` R1 defend value) but only after `selected_product_id` + discount cues. Pure “why so expensive?” often stays in recommendation with generic luxury copy.

**Need:** Objection playbook fed with **concrete** product fields (fabric, embroidery, lead time, bespoke) from `product_details`; forbid empty marketing.

### 7. Discount ask → free accessories as gift (not cash first)

**Code status:** Partially implemented — Round 2 `offer_free_accessory` when price ≥ 100k PKR / £1k (`accessories_service` + `negotiation_engine`). Round 3 ~10% floor without real promo API. Spec wanted tiered 150k/100k/50k and real `POST /discounts/validate`.

**Gaps:**
- Thresholds simpler than `ACCESSORIES_API_SPEC.md`.  
- No live promo code (`backend_promo_code` always None).  
- Accessories APIs may 429 / return empty → engine correctly says don’t invent, but UX feels like refusal.  
- User requirement “discount → gift add-ons” is **negotiation-only**, not offered early.

**Need:** Wire recommend accessories on discount intent; prefer gift before cash; document when free gift is unavailable.

### 8. Variations / colours of a selected product

**Target:** `Haris_Variations`. Remember.txt already noted category-level suggestions wrongly.

**Need:** Force `get_product_details` + product `variations[]`; never answer from category variations alone when `selected_product_id` set.

### 9. Stock / size honesty & urgency

**Targets:** `Tahir_Stock_Size`, `Ahmed_Urgent`.

**Need:** Call `check_inventory` when size+colour present; never invent lead times; if backend `lead_time_days=28` and user has 8 days, say so and offer ready-made / pickup / handover.

### 10. Accessories-only shoppers

**Target:** `Rizwan_Accessories_Only` (weak scores).

**Need:** Intent path that calls accessories search/recommend without stuffing sherwani search.

---

## P2 — Architecture & overengineering

### 11. Redis: needed?

| Fact | Implication |
| --- | --- |
| Not in `docker-compose.yml` | Not part of official stack |
| Not in original `requirements.txt` | Import failed → silent in-memory dict |
| `REDIS_URL` not on `Settings` | `getattr` always falls back to localhost |
| Postgres already stores messages + profiles | Redis is cache-only |

**Verdict:** For current traffic, **Postgres alone is enough**. Redis is optional cache. Either: (a) add Redis properly to compose + settings + requirements and treat it as real, or (b) delete Redis path to reduce complexity. Do not keep a half-wired third store.

### 12. Dead / duplicate modules

| Item | Action |
| --- | --- |
| `SALES_AGENT_TOOLS` never imported | Delete or wire |
| `scoring_engine.py` unused (Phase 3 “done” in docs) | Delete or re-enable deliberately |
| `negotiation_tools.calculate_negotiation_offer` unused (engine used instead) | Delete tool wrapper |
| `schemas/reservation.py`, discount validate schemas | Keep only when backend ships |
| Prompt “top 6 products” vs `MAX_PRODUCTS_TO_SHOW=3` | Align prompts to 3 |

### 13. God-function planner

`planner_node` ≈ 700+ lines of LLM + discovery gates + negotiation guards + customisation. Split into parse → discovery gate → commerce gate → product resolve. Easier to test and less double-LLM latency risk.

### 14. Latency

Solo turn ≈ 25s; under concurrency 2–5 often **60–180s**. Causes: 2 LLM calls/turn + multi API + no connection pool + backend 429 waits.

**Fix order:** 429 handling → cache categories → shorter prompts → consider streaming later (roadmap Phase 9). Measure p95 at `--concurrency 1`.

### 15. Guardrails honesty

Docstring claims 5 layers; grounding/faithfulness not implemented in `sanitize_agent_output`. Jailbreak sets `final_response` in planner but graph still runs `final_response_node` (risk of overwrite). Fix short-circuit.

### 16. Sync-in-async

`validate_measurements.invoke` sync; image generation sync OpenAI in async routes. Wrap in `asyncio.to_thread`.

---

## P3 — Product / backend (not chatbot-only)

From plans + live API:

- Real `floor_price` on SKUs (stop 92% guess).  
- `POST /discounts/validate` + real promo.  
- `POST /reserve` for closing.  
- Populate `suitable_events`, sizes, non-zero prices, consistent currency.  
- Accessories APIs stable under load.  
- Stop applying mock event filter in live mode (`filter_products_by_stated_event` on live results).

---

## Prioritised roadmap (effort × impact)

| Priority | Item | Effort | Impact |
| --- | --- | --- | --- |
| P0 | 429 retry + degrade | S | Reliability |
| P0 | No synthetic prices | S | Trust / legal |
| P0 | Currency localisation | M | PK market fit |
| P0 | Session context keys | S | Custom/bespoke memory |
| P1 | Budget-too-low ladder | M | Conversion |
| P1 | Discount → free accessory UX | M | Brand policy |
| P1 | Variation grounding | S | Product UX |
| P1 | Inventory + urgency honesty | M | Trust |
| P2 | Kill dead code / clarify Redis | S | Maintainability |
| P2 | Split planner + trim prompts | L | Latency / clarity |
| P3 | Backend floor/reserve/promo | L (backend) | Closing |

---

## How to verify each fix

See **`TEST_FRAMEWORK.md`** §6 mapping. Do not claim a fix is done without:

1. Targeted persona run green, and  
2. Full or `--tags critical` suite without regression vs `baseline.json`.

---

## What is already good (do not break)

- Roman Urdu warmth and event-aware discovery openings.  
- Jailbreak / off-topic deflection (Troll scores high when HTTP works).  
- Deterministic negotiation ladder design (R1–R4) — finish wiring, don’t rewrite from scratch.  
- `shown_product_ids` dedup concept.  
- HTML workbench `/test` with executed nodes (useful for manual QA).
