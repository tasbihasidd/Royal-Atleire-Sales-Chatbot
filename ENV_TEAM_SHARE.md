# Env / contract — kya FE & Backend ko share karna hai

Chatbot ke **server-only secrets** (`FAL_KEY`, `QUOTA_ADMIN_KEY`, `DATABASE_URL`, LangSmith)  
→ public git mein mat daalo.

**`CHATBOT_API_KEY` Frontend ko dena hoga** (har AI call pe header) — warna chat/image/try-on 401.

---

## Frontend (storefront / stylist widget) ko do

| Cheez | Value / note |
|--------|----------------|
| Chatbot base URL | Local: `http://localhost:8015` · Prod: `https://ai.turabees.com` |
| **X-Api-Key** | Same value as chatbot `CHATBOT_API_KEY` — har request pe header |
| APIs | `POST /chat`, `POST /api/generate-wedding-image`, `POST /api/virtual-try-on` |
| CORS | Unka origin chatbot `CORS_ORIGINS` mein hona chahiye |
| Credentials | Prod mein `credentials: 'include'` (cookie ke liye) |
| Session cookie name | `royal_session` (same as chatbot `SESSION_COOKIE_NAME`) |
| Cookie domain (prod) | `.turabees.com` — **backend issue** karega; FE sirf bheje |
| Session body (dev) | Abhi bhi `session_id` body mein chal sakta hai |
| Checkout link | Sirf `https://turabees.com/cart?s=...` — **`?cart=` mat use** |

FE env example:

```env
VITE_CHATBOT_API_URL=https://ai.turabees.com
VITE_CHATBOT_API_KEY=meH0ixRyCoHvLkEY534dGVdEOhvMHNg1eRKn-BleqYo
```

Har fetch/axios call:

```js
headers: { "X-Api-Key": import.meta.env.VITE_CHATBOT_API_KEY }
```

Note: browser se key dikhegi (FE direct call). Phir bhi random curl/bots rokegi.

---

## Website backend (RA / Turabees API) ko do

| Cheez | Note |
|--------|------|
| Chatbot calls in | `BACKEND_API_BASE_URL` → sales-agent (`/checkout`, `/limits`, products, accessories…) |
| Session cookie | Backend **set** kare `royal_session` + `Domain=.turabees.com`; chatbot **read** kare |
| `/limits` | Quota numbers yahan se aate hain |
| Checkout | Chatbot items bhejta hai; **price / isGift / £0** backend verify/mark kare |
| Complimentary gift | ACCESSORY catalogue price ignore; free gift CUSTOM@0 ya backend `isGift` |
| Try-on quota | **Backend team** decide/enforce (chatbot pe alag limit nahi) |

Backend ko **mat** bhejo: `FAL_KEY`, chatbot `QUOTA_ADMIN_KEY`, chatbot DB password.

---

## Production chatbot server pe (sirf ops / chatbot)

```env
APP_ENV=prod
ENABLE_DEV_ENDPOINTS=false
QUOTA_ADMIN_KEY=<long-random-secret>
CHATBOT_API_KEY=<same-key-you-give-frontend>
CORS_ORIGINS=https://www.turabees.com,https://turabees.com
REDIS_URL=redis://...
POSTGRES_PASSWORD=<strong>   # sirf jab docker-compose se DB chalao
```

---

## Short answer

| Team | Kya share |
|------|-----------|
| **Frontend** | Chatbot URL + **`CHATBOT_API_KEY` / X-Api-Key** + cookie name + CORS + `?s=` |
| **Backend** | Cookie contract + `/limits` + checkout/gift price ownership |
| **Kisi ko nahi (public git)** | `FAL_KEY`, `QUOTA_ADMIN_KEY`, DB URLs/passwords, LangSmith keys |
