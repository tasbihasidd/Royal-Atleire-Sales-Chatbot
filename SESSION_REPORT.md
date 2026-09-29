# Session Report — Post-Fix Full Eval (Sep 22–23, 2026)

## Verdict

**20 / 20 personas completed.** Quality gate still **FAIL**, but scores recovered sharply vs the broken overnight baseline (mean **2.29 → 7.45** on the instrumented 16-persona batch).

Main remaining failures: **PKR customers still quoted in GBP / 180000-as-GBP**, slow turns (p95 ~66s), discovery that delays product show, and weak custom / accessories flows (Moiz, Rizwan, Sohail).

---

## How this run was assembled

| Batch | When | Personas | Artifact |
| --- | --- | --- | --- |
| A | 22 Sep ~19:47–21:21 | Asad, James, Bilal, Kamran | log only (`logs/full_e2e_eval.log`) — process died mid-Faisal (API down overnight) |
| B | 23 Sep 14:35–18:54 | remaining 16 | full report: `test_results/eval_20260923_093523.{md,json}` |

Config (batch B): DeepSeek chatbot · 3-judge panel · max 10 turns · concurrency 1 · wall clock **~4.3 h** · eval LLM cost **~$0.14**.

---

## Combined scoreboard (all 20)

| # | Persona | Mean | Turns | Notes |
| ---: | --- | ---: | ---: | --- |
| 1 | James_Walima_Bespoke | **8.69** | 9 | batch A (log) |
| 2 | Haris_Variations | **8.46** | 10 | |
| 3 | Vague_Vicky | **8.32** | 10 | hit max turns |
| 4 | Bilal_Nikah_Mixed | **8.23** | 8 | batch A (log) |
| 5 | Zain_GroomsParty_Bulk | **8.15** | 10 | hit max turns |
| 6 | Troll_Adversarial | **8.07** | 9 | guardrails healthy |
| 7 | Usman_Discount_Hunter | **7.97** | 10 | 1× HTTP 500; cash discount before free gift |
| 8 | Ahmed_Urgent | **7.90** | 7 | |
| 9 | Faisal_PriceShock | **7.78** | 8 | |
| 10 | Asad_Barat_Negotiator | **7.72** | 9 | batch A (log) |
| 11 | Tahir_Stock_Size | **7.67** | 6 | |
| 12 | Adnan_Comparison | **7.66** | 6 | |
| 13 | Typo_Tanveer | **7.48** | 8 | |
| 14 | OneWord_Omar | **7.38** | 10 | hit max turns |
| 15 | Nabeel_Mehndi_Light | **7.25** | 4 | |
| 16 | Sara_ThirdParty | **6.75** | 8 | below gate 7.0 |
| 17 | Kamran_Budget_TooLow | **6.33** | 6 | batch A (log); below gate |
| 18 | Sohail_Fabric_First | **6.06** | 8 | fabric rates not grounded |
| 19 | Rizwan_Accessories_Only | **5.50** | 5 | accessories search / language |
| 20 | Moiz_FullCustom | **5.31** | 8 | worst; bespoke / image grounding |

**Batch B mean (16 instrumented):** **7.45** · median 8.0 · turn pass rate **55%**  
**Approx all-20 mean (incl. log scores):** **~7.48**

Below 7.0: Moiz, Rizwan, Sohail, Kamran, Sara (5 / 20).

---

## Quality gates (batch B)

| Gate | Result | Threshold |
| --- | --- | --- |
| Mean score | **7.45** | ≥ 7.0 ✓ |
| Turn pass rate | **0.548** | ≥ 0.75 ✗ |
| Critical findings | **36** | ≤ 0 ✗ |
| p95 latency | **66404 ms** | ≤ 20000 ms ✗ |

**Gate: FAIL**

Vs broken baseline (`2026-09-22` credit/soft-fail run): mean +5.16, grounding +5.3, routing +7.4, criticals 51 → 36. Latency got worse (p95 +29s) under concurrency 1 + backend retries.

---

## Rubric dimensions (batch B)

| Dimension | Mean | Read |
| --- | ---: | --- |
| guardrails | 9.66 | healthy |
| state_consistency | 8.84 | healthy |
| routing | 8.71 | healthy |
| language_compliance | 8.64 | healthy |
| negotiation_discipline | 8.48 | healthy |
| naturalness | 8.13 | healthy |
| objection_handling | 7.75 | acceptable |
| progression | 7.52 | acceptable |
| grounding | 7.26 | acceptable |
| consultative_quality | 7.01 | borderline |

---

## What improved (fix roadmap landing)

- Soft HTTP degrade mostly worked: only **1** `/chat` HTTP 500 across 127 turns (vs earlier 429/500 storms).
- Negotiation / handover paths fire (Asad, Usman, Ahmed, Troll).
- `free_accessory_within_margin` assertion: **0 / 2 failed** when exercised.
- Guardrails strong (Troll **8.07**).
- No OpenRouter **402** after capping chatbot `max_tokens=2048`.

---

## Top defects still open

### P0 — Currency / price localisation (largest systemic fail)

- `currency_matches_market`: **8 / 8 failed** — Roman Urdu customers priced in **GBP**.
- `prices_plausible_for_currency`: **15 / 18 failed** — e.g. `180000` treated as GBP (outside 100–15,000 band); should be PKR.
- Judge criticals repeatedly flag **£162,000 / 180,000 GBP** invents or wrong currency (Usman, OneWord, Typo, Haris).

→ Market currency localisation from the roadmap is **not fully landing in replies**.

### P0 — Grounding / inventing prices & stock

- `prices_grounded`: **6 / 28 failed**.
- Moiz invents bespoke “already crafted” + refuses published prices.
- Rizwan claims accessory catalogue/pricing “not loaded”.
- Tahir / Haris stock statements contradict context.

### P1 — Accessories-only & fabric-first intents

- Rizwan **5.50**, Sohail **6.06** — worst product-intent failures after Moiz.
- Language register slips to pure English on accessories / one-word openers.

### P1 — Discovery too long

- `discovery_not_endless`: **8 / 16** — many sessions 5–10 turns before any product shown.

### P1 — Negotiation gift-before-cash

- Usman turn 4 critical: **cash discount offered instead of free accessory** (policy miss despite margin assertion passing when gift path ran).

### P2 — Latency & infra

- Mean chat latency ~29s · p95 ~66s · max ~159s (backend 429 retries still bite).
- One internal leak: floor_price / margin mentioned (Usman path).
- Judge health: DeepSeek judge valid JSON **only ~3%** — panel heavily depends on gpt-4o-mini + Gemini; scores still usable but noisy.

---

## Critical defect themes (judge panel, batch B)

| Theme | Example personas |
| --- | --- |
| Wrong / invented GBP prices | Usman, OneWord, Typo, Vague |
| Language register (EN vs Roman Urdu) | Adnan, Rizwan, OneWord |
| Stock / bespoke grounding | Moiz, Tahir, Haris |
| Accessories “not in catalogue” | Rizwan |
| Cash discount before free gift | Usman |

Full defect list: `test_results/eval_20260923_093523.md` § Critical and major defects.

---

## Recommended next fixes (priority)

1. **Force PKR (or market currency) in every customer-facing price string** for PK market / Roman Urdu sessions; stop emitting bare `180000` + `GBP`.
2. **Accessories-only path**: never claim catalogue empty if search returned items; keep Roman Urdu register.
3. **Custom / fabric**: give grounded ranges or honest “consultant quotes”; no invented “already crafted”.
4. **Negotiation R1**: free accessory before cash when margin allows (Usman regression).
5. **Show products earlier** (cut endless discovery).
6. Cap / cache backend calls further — p95 latency gate will keep failing otherwise.
7. Eval hygiene: raise judge `max_tokens` slightly or drop DeepSeek from panel (3% JSON success wastes budget).

---

## Artifacts

| File | Contents |
| --- | --- |
| `test_results/eval_20260923_093523.md` | Full auto report (16 personas) |
| `test_results/eval_20260923_093523.json` | Machine-readable results |
| `test_results/transcripts/20260923_093523_*.txt` | Per-persona transcripts |
| `logs/full_e2e_eval.log` | Batch A (first 4) |
| `logs/full_e2e_eval_resume.log` | Batch B |

---

*Report compiled 23 Sep 2026 after resume batch `Run complete passed=False`.*
