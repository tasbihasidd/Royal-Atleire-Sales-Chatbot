# HARD_TEST_REPORT.md — required output template

Write this file at the **repository root** as `HARD_TEST_REPORT.md`.  
Replace placeholders. Keep quotes exact.

```markdown
# Hard Test Report — Royal Atelier / Turabees Chatbot

| Field | Value |
| --- | --- |
| Date (UTC) | |
| Agent / IDE | |
| Chatbot URL | e.g. http://localhost:8015 |
| Model under test | from .env OPENAI_MODEL |
| Customer simulation | human agent typing / OpenRouter persona LLM |
| Sessions completed | 10 / 10 |
| Min turns rule | ≥8 successful replies each |
| **Overall score** | **XX%** |
| Worst-case coverage | YY% (Hit/35) |
| Verdict | Strong / Acceptable / Weak / Fail |

## Executive summary
3–5 sentences: what is fixed, what still breaks, whether ready for users in PK/IN/US/CA.

## Scoreboard

| # | Persona | Country | Lang | Turns | A | B | C | D | E | F | Session % | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Confused_Farhan | PK | Roman Urdu | | | | | | | | | |
| 2 | Asma_Lahore_Mix | PK | Mix | | | | | | | | | |
| 3 | Rohan_Mumbai | IN | Hinglish | | | | | | | | | |
| 4 | James_NewJersey | US | English | | | | | | | | | |
| 5 | Priya_Toronto | CA | English | | | | | | | | | |
| 6 | Tahir_Urgent_Stock | PK | Roman Urdu | | | | | | | | | |
| 7 | Rizwan_Accessories | PK | Mix | | | | | | | | | |
| 8 | Usman_Troll | PK | Mix | | | | | | | | | |
| 9 | Haris_Variations | PK | Mix | | | | | | | | | |
| 10 | Zain_Canada_Group | CA | Mix | | | | | | | | | |

Weights reminder: A15 B15 C20 D25 E15 F10.

## Language & countries
- What worked (quote).
- Failures (quote + session).

## Worst-case coverage
Paste filled hit map from WORST_CASES.md (at least IDs + Hit/Miss).
Missed IDs listed here again.

## Known issues (regression)
Paste filled REGRESSION.md table.

## Session details

### Session 1 — Confused_Farhan
- session_id:
- turns / HTTP errors:
- A–F scores + session %:
- 6–10 quoted load-bearing turns (USER / BOT):
- What worked:
- What broke (bot | data | harness):
- Related W# / R#:

(Repeat ### Session 2 … Session 10 the same way.)

## Weak spots (required if overall < 85%)
1. …
2. …

## Transcripts
All files under `test_results/hard_test/`:
- list filenames

## Method note
Confirm: every bot reply came from live `POST /chat` (real LLM + tools). No mocked chatbot answers.
```
