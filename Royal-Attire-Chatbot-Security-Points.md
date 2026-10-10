# Royal Attire — Chatbot Security Points Only

**Source:** `Royal-Attire-Security-Audit-and-Remediation-Plan.md` (2026-10-09)  
**Scope yahan:** sirf `chatbot-backend` + woh cross-service items jahan **chatbot team** ko change karna hai.  
**Out of scope yahan:** website-backend, admin panel, storefront-only issues; **repo visibility** (ops); **virtual try-on quota** (backend team lagayegi).

---

## Short summary (chatbot)

| Priority | Item |
|---|---|
| **P0** | Dev endpoints (`clear-all`, sessions dump, debug, `/test`, docs) **env-controlled** |
| **P0** | CORS exact origins, no `*` with credentials |
| **P0** | `QUOTA_ADMIN_KEY` — quota reset peeche lock |
| **P1** | Prod session = server HttpOnly cookie; dev body fallback |
| **P1** | Plain HTTP → HTTPS (`chat.turabees.com`) — deploy |
| **P1** | docker-compose weak creds / published ports harden |
| **P2** | Memory fallbacks, blocking I/O, input limits, `REDIS_URL`, verbose errors |
| **Coord** | Cookie read, `/limits`, checkout `spec`, gift eligibility backend decide, `?s=` only |
| **Deferred** | Rate limiting (Phase 2) · Negotiation engine change **nahi** · try-on quota (backend) |

---

## 1. Chatbot project issues

### 1.1 Endpoints bina auth (destructive + data leak) — P0

- **Kya:** `GET /api/sessions`, `GET /sessions/{id}/messages`, `POST .../clear`, `POST /api/sessions/clear-all`, `GET /debug/image-generations`, `POST .../quota/reset` (jab key empty).
- **Fix:** `ENABLE_DEV_ENDPOINTS` / `APP_ENV`. Prod = routes off. Reset hamesha `QUOTA_ADMIN_KEY` ke peeche.

### 1.2 Quota per client `session_id` (bypass) — P0/P1

- **Fix:** Prod mein server-issued HttpOnly cookie; body `session_id` sirf **dev** (ya cookie missing pe transitional fallback).

### 1.3 CORS `*` + credentials — P0

- **Fix:** `CORS_ORIGINS` exact list; credentials ke saath `*` na ho.

### 1.4 Fallback in-memory dicts — P2

- **Fix:** Redis + TTL; fallback bounded LRU.

### 1.5 Blocking I/O on event loop — P2

- **Fix:** Async download / `asyncio.to_thread` around sync fal subscribe.

### 1.6 Input size limits — P2

- **Fix:** Pydantic `max_length` + request body cap.

### 1.7 Prompt-injection — architecture

- Price/gift **server/code** decide; output guard; negotiation engine change out of scope.

### 1.8 Plain HTTP — P1 (deploy)

- TLS + `chat.turabees.com`.

### 1.9 Verbose errors — P2

- Generic client message; detail logs.

### 1.10 Config / infra — P1/P2

- docker-compose env-based DB password; bind Postgres to localhost; `REDIS_URL` in Settings; `QUOTA_ADMIN_KEY` required in prod.

### 1.11 `/test` + `/docs` prod mein — P0

- Prod: docs off; `/test` behind `ENABLE_DEV_ENDPOINTS`.

---

## 2. Cross-service (chatbot side)

| # | Cheez | Chatbot |
|---|---|---|
| 1 | Session cookie | Prod: read shared cookie; Dev: body `session_id` |
| 2 | Quota `/limits` | Session identity se |
| 3 | Checkout | Backend verify price |
| 4 | Gift | Accessory bhejo; `isGift` / £0 backend |
| 5 | CORS | Storefront origin allow |
| 6 | Cart link | Sirf `?s=` — no `?cart=` |

**Not chatbot:** virtual try-on daily quota (backend). Repo private (ops).

---

## 3. Priority checklist

**P0**
- [x] `ENABLE_DEV_ENDPOINTS` — prod pe clear-all / sessions / debug / `/test` / docs off
- [x] CORS exact origins
- [x] `QUOTA_ADMIN_KEY` lock on reset (required in prod)
- [x] Checkout URL guard: reject unsigned `?cart=`

**P1**
- [x] Session cookie read path (+ body fallback)
- [ ] HTTPS / `chat.turabees.com` (deploy / DNS — not app code)
- [x] docker-compose harden (env password, localhost bind)

**P2**
- [x] `REDIS_URL` in Settings + bounded memory LRU
- [x] Async-friendly image / try-on I/O
- [x] Input `max_length` + body size limit
- [x] Verbose errors → generic

**Phase 2**
- [ ] Rate limiting
- [ ] Try-on quota — **backend team**
