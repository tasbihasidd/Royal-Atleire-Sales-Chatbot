# Royal Attire — Security Audit & Remediation Plan (v1 Launch)

**Date:** 2026-10-09
**Type:** Security Audit + Remediation Plan (audit only — koi code change nahi hua)
**Scope:** 4 projects
- `website-backend/royal-attire-backend` (Express + Prisma + Postgres API)
- `frontend-adminside/royal-attire-admin` (React admin panel)
- `frontend-clientside/royal-attire-updated` (TanStack Start storefront + chatbot integration)
- `chatbot-backend/Royal-Atleire-Sales-Chatbot` (Python FastAPI AI sales agent)

> **Padhne ka tareeqa:** Har project ki apni heading hai. Har issue ke neeche: **(1)** Kya hai, **(2)** Kyun issue hai + worst case, **(3)** Exact file/line, **(4)** Proposed solution. Aakhir mein **Remediation Plan (naya kaam — §6)**, **Cross-service coordination (§6.6)**, **Dev vs Prod behaviour (§6.5)**, **Worst-case analysis (§7)**, aur **Self-review (§10)** hai.

---

## 0. Short Summary (ek nazar)

- **3 repo private hain** (`hnhsofttechsolutions/*`), **1 repo PUBLIC hai** — `tasbihasidd/Royal-Atleire-Sales-Chatbot` (verify kiya gaya, GitHub API).
- Sabse bade launch blockers: **(a)** committed secrets, **(b)** kai endpoints bina auth (coupons/design-lab/orders/chatbot), **(c)** checkout client ke price pe bharosa, **(d)** chatbot repo public.
- **Out of scope (is plan mein NAHI — Phase 2):**
  1. **Rate limiting (backend + chatbot)** — abhi nahi; baad mein **poori tarah** implement hogi (per-identity keys + Redis, IP-based nahi). ⚠️ Isliye login brute-force ka risk abhi barhta hai — **interim mitigation §4.1**.
  2. Negotiation engine / chatbot negotiation logic — change nahi; sirf backend **double verification** (§6.3).
- **⚠️ Cross-service:** Kuch changes **aadhe** nahi ho sakte (session cookie, checkout payload, gift flag, CORS/env) — **chatbot team ko bhi karna hoga**. Poora contract **§6.6** mein hai.

**Architecture (ek nazar):**

```
   ┌───────────┐      ┌─────────────┐
   │ Customer  │      │ Admin panel │
   │ (browser) │      │ (React)     │
   └─────┬─────┘      └──────┬──────┘
         │                   │
         ▼                   ▼
   ┌────────────────────┐
   │ frontend-clientside │─────────────┐ (1) DIRECT call
   │ (TanStack + Clerk)  │             │  POST /chat, /virtual-try-on
   └─────────┬──────────┘             │  (cookie .turabees.com bhi jati)
             │ (2) HTTPS               ▼
             │  cookie + credentials   ┌────────────────┐
             │                         │  AI Stylist    │
             │                         │  (chatbot)     │
             │                         └───────┬────────┘
             │                                 │ (3) server-to-server
             │                                 │  checkout / limits
             ▼                                 ▼
   ┌──────────────────────────────────────────────────────┐
   │         website-backend  (Express + Prisma)           │
   │  cookie OWNER • price verify • gift • quota • orders  │
   └───────┬───────────────────────────────┬──────────────┘
           ▼                               ▼
      PostgreSQL                        S3 (media)
           + Redis (jobs/queue — §4.19)
```

> **Note (important):** Frontend **chatbot ko DIRECT** call karta hai (chat + try-on) — ye flow **waise hi rahega**, koi change nahi. Backend sirf **cookie/price/gift/quota** ka owner hai; chatbot backend ko **server-to-server** (checkout/limits) call karta hai. (Diagram pehle adhoora tha — direct edge missing thi.)

---

## 1. Executive Summary — Launch Blockers (P0)

Ye cheezein v1 launch se pehle **zaroor** theek honi chahiye:

1. Committed secrets rotate + git history clean (frontend-clientside `.env`).
2. Chatbot repo ko **private** karo.
3. Coupons / design-lab `/admin/*` / intelligence-search pe **auth** lagao.
4. Orders endpoints (`/my-orders`, `/merge`) pe auth + email query param hatao.
5. Checkout pe **server-side price double verification** (neeche Remediation Plan).
6. Chatbot ke dev endpoints (`clear-all`, `sessions`, `debug`, `/test`) **env-controlled** karo (dev = on, prod = off) + CORS fix.
7. Hardcoded JWT fallback secrets hatao.
8. Clerk webhook verification ko mandatory karo (skip nahi).

> **Phase 2 (deferred — filhal NAHI):** **Rate limiting** (backend + chatbot). Interim mitigation §4.1.

---

## 2. Project: `frontend-adminside/royal-attire-admin`

### 2.1 JWT `localStorage` mein rakha hai (XSS se chori ho sakta)

- **Kya hai:** Login ke baad token `localStorage.setItem('token', ...)` se save hota hai.
- **Kyun issue:** `localStorage` ko **koi bhi JavaScript** padh sakta hai. Agar admin panel pe kabhi XSS ho (ya koi malicious npm package), ek line se poora admin token chori ho jata hai.
- **Worst case:** Attacker XSS se token chura ke apne server bhejta hai → **poora admin panel uske paas**.
- **File:** `src/store/auth.ts` (lines 11, 14, 18); request interceptor `src/lib/axios.ts`.
- **Solution:** JWT ko **HttpOnly + Secure cookie** mein le jao (details Remediation Plan §6.1). Admin UI ko token padhne/rakhne ki zaroorat nahi — cookie browser khud bhejta hai (`withCredentials`).

### 2.2 Route guard sirf "token mojood hai" check karta hai (role check nahi)

- **Kya hai:** `AdminLayout.tsx` sirf `if (!token) redirect` karta hai.
- **Kyun issue:** Koi bhi non-empty string daal do to UI khul jata hai. Client-side guard kabhi security boundary nahi hoti.
- **Worst case:** Frontend bypass ho jata hai; agar backend pe kisi route pe `restrictTo('ADMIN')` missing ho to direct access.
- **File:** `src/layouts/AdminLayout.tsx` (line ~19), `src/routes/index.tsx`.
- **Solution:** Backend par har admin route pe `protect + restrictTo('ADMIN','SUPER_ADMIN')` **mandatory** karo; frontend pe sirf UX ke liye role-based redirect rakho.

### 2.3 `.env` git mein committed hai

- **Kya hai:** `frontend-adminside/.env` repo mein track hai (ismein `VITE_API_URL` — production host).
- **Kyun issue:** Production infrastructure detail VCS mein aa jati hai.
- **File:** `.env` (tracked), `.gitignore` mein `.env` nahi.
- **Solution:** `.env` ko `.gitignore` mein daalo, `.env.example` rakho, tracked `.env` remove karo.

### 2.4 `HighlightedText` raw input se regex banata hai (crash / ReDoS)

- **Kya hai:** `new RegExp(\`(${query})\`, 'gi')` — search query seedha regex mein.
- **Kyun issue:** Query mein `(` / `[` → app crash; `(a+)+$` jaisa pattern → browser freeze (ReDoS).
- **File:** `src/components/ui/HighlightedText.tsx` (line 11).
- **Solution:** Query ke special chars escape karo (`RegExp.escape`) ya regex ke bajaye `indexOf`-based highlight.

### 2.5 Admin pe rich-text bina sanitize save hota hai (downstream stored-XSS risk)

- **Kya hai:** Quill editor ka HTML seedha backend ko jata hai (koi sanitize nahi); DOMPurify dependency hai hi nahi.
- **Kyun issue:** HTML mein `<script>`/`onerror` paste ho sakta hai. Admin khud render nahi karta, **lekin storefront** usay `dangerouslySetInnerHTML` se render karta hai → **stored XSS**.
- **File:** `src/features/blogs/pages/BlogForm.tsx`; consumer side `frontend-clientside/src/routes/heritage-blog_.$slug.tsx`.
- **Solution:** Input (admin) aur output (storefront) dono taraf sanitize — DOMPurify.

### 2.6 Koi refresh token / server-side revocation nahi — [REVIEW: naya mila]

- **Kya hai:** JWT 7 din ka hai; na refresh flow, na logout pe server-side revoke (sirf localStorage clear hota hai).
- **Worst case:** Chura hua token **7 din tak** valid rehta hai.
- **Solution:** Short-lived access token + refresh flow, ya session versioning (DB mein tokenVersion) — cookie plan (§6.1) ke saath.

---

## 3. Project: `frontend-clientside/royal-attire-updated`

### 3.1 Secret keys git mein committed (SABSE BADA)

- **Kya hai:** `.env` git mein tracked hai. Usme: `GEMINI_API_KEY` (asli), OpenRouter key (comment), aur ek `sk_live_...` Clerk secret key (galat variable mein comment).
- **Kyun issue:** Jo bhi repo dekh le (collaborator/leaked laptop) ye keys le sakta hai.
- **Worst case:** Clerk `sk_live` se **poora auth account control**; Gemini/OpenRouter se **aapke paise se unlimited AI calls**.
- **File:** `.env` (tracked; `.gitignore` mein sirf `*.local`), `src/lib/bespoke.functions.ts` (line ~70, hardcoded OpenRouter fallback).
- **Solution:** Teeno keys **rotate**, `.env` ko `.gitignore` mein, history se purge (BFG/git filter-repo), hardcoded fallback hatao.

### 3.2 Chatbot/Guest session id `localStorage` mein (quota bypass)

- **Kya hai:** `royal_chatbot_session_id` aur `royal_guest_session_uuid` client browser mein bante hain; chatbot/backend usay **blindly** use karte hain.
- **Kyun issue:** Quota `session_id` pe lagti hai. User localStorage se id badal/delete kar ke **naya session** bana leta hai → **free messages dobara** (daily limit bypass).
- **Worst case:** Attacker infinite free AI/image generation → aapki cost.
- **File:** `src/lib/session.ts` (lines 5, 6, 27–52); consumption `src/components/site/stylist-chat.tsx`.
- **Solution:** **Server-issued HttpOnly cookie** (details §6.2). Dev mein localStorage (easy development), prod mein cookie (env-controlled).
- 🔗 **Coordination (frontend ↔ chatbot):** frontend `credentials:true` bheje; **chatbot wahi cookie padhe** (§6.6 #1). Warna dono alag session pe chalenge.

### 3.3 Orders IDOR + PII URL mein

- **Kya hai:** `/orders/my-orders?email=...` aur `/orders/merge` **bina auth**; email query string mein.
- **Kyun issue:** Sirf `?email=` badal ke kisi ka bhi order data milta hai; email browser history/logs mein chala jata hai.
- **Worst case:** Saare customers ke orders (naam, phone, address, measurements) scrape → GDPR/PII leak.
- **File:** `src/routes/account.tsx` (lines ~69–79).
- **Solution:** Backend pe `protect` lagao; email/userId **token se** nikalo, URL se nahi.

### 3.4 Checkout frontend ka price bhejta + `?cart=` base64 token

- **Kya hai:** Order payload mein `unit_price: numPrice` frontend bhejta hai (`checkout.index.tsx` 218, 230); `?cart=<base64>` token bina signature decode hota hai (117–126; `cart.tsx` 63–72; `cart-drawer.tsx` 130–139).
- **Kyun issue:** Base64 **encryption nahi** — koi bhi `atob()` se khol ke price badal sakta hai; backend usay maan leta hai.
- **Worst case:** £5000 ka suit £1 ka order.
- **File:** `src/routes/checkout.index.tsx`, `src/routes/cart.tsx`, `src/components/cart/cart-drawer.tsx`.
- **Solution:** Client price bhejna band; sirf `session_id`/spec bhejo. Backend double-verify (§6.3). Unsigned `?cart=` hatao — sirf `?s=shortCode` rakho (secure rasta already maujood hai).

### 3.5 Poora catalog ek request mein download (bandwidth/memory)

- **Kya hai:** `fetch('${baseUrl}/products')` bina page/limit — saare products, phir client-side paginate.
- **Kyun issue:** 5–10k products pe har visitor 5–15 MB download karega.
- **File:** `src/components/shop-collection/shop-collection-content.jsx` (line 17).
- **Solution:** Server-side pagination (`?page=&limit=`) — baaki pages already aisa karte hain.

### 3.6 Stored XSS — blog content bina sanitize render

- **Kya hai:** `<article dangerouslySetInnerHTML={{ __html: blog.content }} />` (sanitize nahi).
- **Kyun issue:** Blog HTML mein JS chhupa ho to **har visitor** ke browser mein chalega.
- **File:** `src/routes/heritage-blog_.$slug.tsx` (line ~120).
- **Solution:** DOMPurify se sanitize (output side), aur server-side bhi.

### 3.7 Chatbot / try-on plain HTTP pe (encryption nahi)

- **Kya hai:** `http://167.114.96.66:2006` (chat + virtual try-on). User ke **chehre ki tasveer** bhi isi pe jati hai.
- **Kyun issue:** MITM se messages/photos intercept ho sakti hain; storefront https hai to browser mixed-content block bhi kar sakta hai.
- **File:** `.env` (`VITE_CHATBOT_API_URL`, `VITE_VIRTUAL_TRYON_API_URL`), `src/lib/utils.ts` (defaults 14–37), `IntelligenceSearchPage.tsx`.
- **Solution:** Server pe TLS (reverse proxy + Let's Encrypt), URLs `https://`, aur behtar ho ke chatbot `chat.turabees.com` pe le aao (§6.2 / §7).

### 3.8 Checkout price link `?cart=` (redundant + insecure)

- **Kya hai:** Sales-agent link secure rasta already deta hai (`?s=shortCode` + DB session + signed JWT + HttpOnly cookie). Saath hi ek **unsigned** `?cart=<base64>` rasta bhi chalu hai.
- **Solution:** Unsigned `?cart=` rasta **hatao**, sirf `?s=` rakho.

---

## 4. Project: `website-backend/royal-attire-backend`

### 4.1 Saare rate limiters OFF hain — [OUT OF SCOPE — Phase 2]

- **Kya hai:** `rateLimiter.middleware.ts` mein sab limiter `noopLimiter` (kuch nahi rokta); `app.ts` ka global limiter comment out.
- **Status:** **Abhi implement NAHI karni** (aap ne out of scope kaha). Baad mein **poori tarah** — per-identity keys (account/session/email) + Redis store; IP-based nahi (shared IP issue).
- **⚠️ Risk (defer karne se):** rate limit na hone se **login brute-force**, checkout carding, AI/ticket spam khule rehte hain.
- **Interim mitigation (jab tak defer):** (a) admin password **strong + unique**; (b) login pe **in-app failure counter / temporary lock** (chhota, rate-limit ke bagair); (c) agar available ho to Cloudflare/Nginx **edge** pe basic limit.
- **File:** `src/middlewares/rateLimiter.middleware.ts`, `src/app.ts` (~83–88).

### 4.2 Coupons CRUD poori public hai

- **Kya hai:** `coupon.routes.ts` mein POST/PUT/PATCH/DELETE sab bina `protect`.
- **Worst case:** Koi bhi 100% coupon bana ke free order; ya saare coupons delete.
- **File:** `src/api/routes/coupon.routes.ts`.
- **Solution:** `protect + restrictTo('ADMIN','SUPER_ADMIN')`. Sirf `/validate` public rakho.

### 4.3 Design-lab `/admin/*` poori public hai

- **Kya hai:** `designLab.routes.ts` mein `protect` import hi nahi.
- **Worst case:** Koi bhi fabrics/embroidery/event-contexts create/delete kare; design requests (PII) padh/delete kare.
- **File:** `src/api/routes/designLab.routes.ts`.
- **Solution:** Har `/admin/*` route pe auth.

### 4.4 Orders IDOR + `/merge` bina auth

- **Kya hai:** `/orders/my-orders` aur `/orders/merge` bina `protect`; email/userId query/body se.
- **Worst case:** Har customer ke orders padho; guest orders kisi bhi userId se attach karo.
- **File:** `src/api/routes/order.routes.ts`, `src/api/controllers/order.controller.ts`.
- **Solution:** `protect` + email/userId token se.

### 4.5 Checkout client ke price pe bharosa (`unit_price`)

- **Kya hai:** `order.controller.createCheckoutSession` aur `salesAgent.controller.createSalesAgentCheckout` client ka `unit_price` seedha use karte hain; DB se sirf tab jab number missing ho.
- **Worst case:** Attacker request mein `unit_price: 1` → sasta order.
- **File:** `src/api/controllers/order.controller.ts`, `src/api/controllers/salesAgent.controller.ts` (line 991).
- **Solution:** Server-side **double verification** — §6.3.

### 4.6 Hardcoded JWT fallback secrets

- **Kya hai:** `env.ts:20` → `'default_dev_secret_please_change_this_to_32_chars'`; `salesAgent.controller.ts:1103,1181` → `'royal_sales_agent_checkout_secret_2026'`.
- **Kyun issue:** Server chupke se jaani-pehchaani secret use kar leta hai agar env missing ho.
- **Worst case:** Attacker usi secret se `{role:'SUPER_ADMIN'}` token forge kare → full admin.
- **File:** `src/config/env.ts`, `src/api/controllers/salesAgent.controller.ts`.
- **Solution:** Fallback hatao — secret na mile to server **boot hi na ho**.

### 4.7 Clerk webhook verification silently skip

- **Kya hai:** `webhook.routes.ts` — agar `CLERK_WEBHOOK_SECRET` unset ho to verification skip + process.
- **Worst case:** Koi bhi fake `user.created/updated` bhej ke users banaye/email badle → takeover.
- **File:** `src/api/routes/webhook.routes.ts` (~15–49).
- **Solution:** Secret na ho to request **reject** (500); compare constant-time.

### 4.8 Anonymous upload + SSRF

- **Kya hai:** `/uploads/public` (bina login file upload), `/uploads/save-ai-image` (server kisi bhi `imageUrl` ko fetch karta hai).
- **Worst case:** S3 storage bhar ke bill, aur SSRF (internal network / cloud metadata hit).
- **File:** `src/api/routes/upload.routes.ts`.
- **Solution:** Auth + file type/size validation; URL ko allowlist karo (SSRF rok).

### 4.9 CORS gaps

- **Kya hai:** Prod list mein fallback `'https://royal-attire-updated.vercel.app/'` (trailing slash → match fail); Dev mein `callback(null, true)` (koi bhi origin) + `credentials:true`; static `/uploads` pe `Access-Control-Allow-Origin: *`.
- **Worst case:** Dev config prod mein chala jaye to arbitrary origin credentialed requests.
- **File:** `src/app.ts` (~26–45, 61–80).
- **Solution:** Exact origins, trailing slash fix, dev branch prod mein na chale.

### 4.10 Unbounded queries / N+1 / in-memory job store

- **Kya hai:** Kai `findMany()` bina `take` (featured products, design-lab, tickets, intelligence-search); `getAllTickets` saare tickets RAM mein laa ke JS filter; `salesAgent` checkout mein per-item DB call (N+1); `importJobs = new Map()` module-scope (job status RAM mein).
- **Worst case:** Data barhne pe RAM/DB bandwidth blow; multi-instance pe job status gayab; restart pe gayab.
- **Solution (current architecture ke hisaab se, ek-ek):**
  1. **Unbounded lists:** har service mein default `take`/`skip` lagao (products 12, tickets 20, etc.); `?limit=all` hatao; `getAllTickets` ka JS filter → DB query.
  2. **N+1:** loop ke andar `findUnique` ki jagah ek batch `findMany({ where: { id: { in: ids } } })`, phir map lookup.
  3. **Import job store:** RAM `Map` → **direct-to-S3 presigned upload + background worker + Redis** (poora flow **§4.19** mein).

### 4.11 Login pe password hash log hota hai

- **Kya hai:** `auth.service.ts:12` `console.log("user --> ", user)` — poora user incl. bcrypt hash.
- **Solution:** Log hatao / mask karo.

### 4.12 Seed hardcoded admin creds

- **Kya hai:** `prisma/seed.ts` → `admin@royal.com` / `admin123`.
- **Worst case:** Prod mein seed chala aur password na badla → public default creds. (Rate limit Phase 2 hai → brute-force ka extra risk §4.1 mein.)
- **Solution:** Random password / env se; pehle login pe change force.

### 4.13 Gift / bundle discount client-side apply hota hai (verify karo) — [REVIEW: refined]

- **Kya hai:** Backend FREE bundle rule deta hai (`salesAgent.controller.ts:907–919`, `max_free_value`), **lekin** chatbot `checkout_service.py:211` accessory ko `isGift`/`unit_price:0` ke **bina** bhejta hai. Aur frontend `cart-drawer.tsx:331–343` mein discount **client-side** apply hota hai — `/coupons/validate` fail ho to **local fallback** se `applyDiscount(offerItem.discountValue)`.
- **Kyun issue:** (a) "free gift" shayad charged ho (bug) ya discount sirf display ho; (b) backend order ke waqt coupon/discount **re-validate nahi karta** — jo client bheje wahi maan leta hai. Attacker koi bhi discount apply kar sakta hai.
- **Solution:** Discount/coupon **server-side** order-time pe validate + apply ho (client calculation kabhi na ho); gift `isGift:true, unitPrice:0` server mark kare (signed session).
- 🔗 **Coordination (chatbot ↔ backend):** chatbot accessory bheje; gift ki eligibility **backend** decide kare (`isGift`) — §6.6 #4.

### 4.14 Verbose error messages client ko

- **Kya hai:** Kai controllers `error.message` seedha response mein bhejte hain.
- **Solution:** Generic message client ko, detail server logs mein.

### 4.15 Dead/duplicate code

- Do `email.service.ts`, unused `clerk.middleware.ts`, unmounted `api/routes/index.ts`, `xss-clean` types bina package.

### 4.16 `/orders/checkout` arbitrary `user_id` accept karta hai — [REVIEW: naya mila]

- **Kya hai:** `createCheckoutSession` `user_id` request body se leta hai (auth ke bina) aur order usi account se attach.
- **Worst case:** Attacker kisi bhi `user_id` pe order daal sakta hai (fake attribution / doosre ke account pe orders).
- **File:** `src/api/controllers/order.controller.ts`.
- **Solution:** `user_id` **token se** lo (Clerk verify), body se nahi.

### 4.17 `intelligence-search` public write — [REVIEW: section add kiya (P0 bullet tha, detail missing thi)]

- **Kya hai:** `POST /intelligence-search` aur `PATCH /intelligence-search/:id/link-product` bina auth; `GET` pe saare generated images.
- **Worst case:** Koi bhi records create/link/unlink kare; saari generated images (prompts ke saath) padhe.
- **File:** `src/api/routes/intelligenceSearch.routes.ts`.
- **Solution:** GET public (ya sanitized), POST/PATCH admin-only.

### 4.18 `express.json({limit:'10kb'})` checkout payload tod sakta hai — [REVIEW: naya mila — functional risk]

- **Kya hai:** Global JSON body limit 10kb (`app.ts`); checkout payload (items + custom_attributes + measurements + image URLs) isse bara ho sakta hai.
- **Worst case:** Legit checkout pe 413 (Payload Too Large) — production bug.
- **Solution:** Checkout/upload routes pe limit barhao (e.g. 1mb), baaki pe tight.

### 4.19 Bulk product import — Direct-to-S3 presigned upload (naya flow) — [senior design]

**Aaj ka flow (problem):**
- Admin `POST /products/import-drive-zip` pe **poora ZIP** (500MB tak) bhejta hai.
- Server ZIP extract karta hai, har image `sharp` se **webp + 3 variants** (thumb/medium/large) banata hai, S3 pe daalta hai, phir DB record. Ye sab request path / background IIFE mein.
- Status `importJobs = new Map()` (RAM) — §4.10.

**Worst cases:**
- 500MB upload + sharp processing server pe → memory/CPU spike, doosre users slow.
- Multi-instance / restart → status gayab, adhoora import.
- `sharp` variants lazmi (warna catalog images bhaari).

**Proposed flow (client-side unzip + presigned direct-to-S3):**
```
 Admin browser (ZIP file select)              Backend                 S3
   │
   │ 1) ZIP browser mein hi streaming read (fflate) — server pe ZIP upload NAHI
   │
   │ 2) har image ke liye:
   │      POST /uploads/presign ───────────▶ size/type validate
   │                                         key = products/{uuid}.jpg
   │      ◀────────── { uploadUrl, key }
   │
   │ 3) PUT uploadUrl (binary) ─────────────────────────────────▶ image seedha S3
   │
   │ 4) manifest banao: [{ key, productCode, color, ... }]
   │      POST /products/import-manifest ───▶ DB rows (batch/transaction)
   │      ◀────────── { jobId }              └─ variants worker (Redis) pe enqueue
   │
   │ 5) progress bar = client pe (x/y uploaded)   ← server load ~0
   ▼
```

**Backend ki zimmedari (halki):**
- `POST /uploads/presign`: auth, size/type/content-type validate, `key` prefix fix (`products/`), **short TTL (5 min)**, per-request max count.
- `POST /products/import-manifest`: manifest validate (keys S3 pe mojood hain? HEAD), **CSV metadata server** parse, products **batch insert**.

**Worst cases jo sochne hain (senior):**
- **Variants kaun banaye?** Abhi `sharp` server pe webp + thumb/medium/large banata hai; direct-to-S3 se ye skip. Options: (a) client canvas resize (quality issue), (b) **background worker / Lambda** (recommended), (c) original accept. → Isliye worker chahiye.
- **Presigned abuse:** key prefix + content-type + size cap + short TTL, warna koi marzi ki file/naam daale.
- **Orphan files:** upload ho gaya magar manifest fail → S3 bekaar files. Fix: S3 lifecycle rule + cleanup.
- **Validation:** client ki parsed metadata pe **bharosa na karo** — CSV server parse karo.
- **Consistency:** DB record tab bane jab S3 key verify (HEAD) ho jaye.

**Redis kahan aayega:** job status + variant-generation queue (BullMQ). Yahi §4.10 #3 ka poora solution hai.

---

## 5. Project: `chatbot-backend/Royal-Atleire-Sales-Chatbot`

### 5.1 Repo PUBLIC hai ⚠️

- **Kya hai:** GitHub API ne confirm kiya — `tasbihasidd/Royal-Atleire-Sales-Chatbot` `"visibility":"public"`, aaj push hua.
- **Kyun issue:** Pura code, endpoints, quota logic public. (Secrets nahi mile — sirf `.env.example`.) Lekin attack surface + internal URLs expose.
- **Solution:** **Repo ko private karo** (P0). Git history mein bhi purane secrets check karo.

### 5.2 Kai endpoints bina auth (destructive + data leak)

- **Kya hai:** `GET /api/sessions` (sab sessions), `GET /sessions/{id}/messages` (kisi ka chat), `POST /api/sessions/{id}/clear` + `/api/sessions/clear-all` (**poora data wipe**), `GET /debug/image-generations` (prompts leak), `POST /sessions/{id}/quota/reset` (khula jab `QUOTA_ADMIN_KEY` empty).
- **Worst case:** Attacker poora DB wipe ya sab conversations padh le.
- **File:** `app/main.py` (605–712).
- **Solution (env-based — "band" nahi, control):** Ek env flag rakho, e.g. `ENABLE_DEV_ENDPOINTS=true|false` (ya `APP_ENV=dev|prod`). **Dev/local mein `true`** — ye endpoints + `/test` khule rahen (easy testing). **Prod mein `false`** — routes register hi na hon (ya 404/403). Aur sensitive wale (`clear-all`, `quota/reset`) ko har haal mein admin key (`QUOTA_ADMIN_KEY`) ke peeche rakho.

### 5.3 Quota per client session id (bypass) → Remediation §6.2

- **Kya hai:** Chatbot `session_id` client body se leta hai; quota us pe count hoti. User id badal ke reset.
- **File:** `app/main.py` (135), `app/services/quota_service.py`.
- **Solution:** Prod mein server-issued cookie session (§6.2) — browser id nahi banata; dev mein body `session_id` (jaise abhi). Quota per-session hi rahega (per-account/per-IP limit **nahi** — shared IP issue).
- **Review note (corrected):** `session_id` **na** bhejne pe endpoint 400 deta hai (`image_generation.py:675-679` require karta hai) — to bypass "id hata ke" nahi, **fresh ids rotate kar ke** hota hai. Cookie fix isi ko band karegi.
- 🔗 **Coordination (chatbot ↔ backend):** limits backend `/limits` se aayen; session **cookie** se identify ho (§6.6 #1, #2).

### 5.4 CORS `*` + credentials

- **Kya hai:** `allow_origins=["*"]` + `allow_credentials=True` + all methods/headers.
- **File:** `app/main.py` (120–126).
- **Solution:** Env-based explicit origins; credentials ke saath `*` na ho.

### 5.5 Fallback in-memory dicts (memory growth)

- **Kya hai:** `_MEM_SESSIONS`, `_MEM_MESSAGES` (`chat_store.py`), `_LOCAL_CACHE` (`memory_service.py`) — unbounded, DB-fail mode mein.
- **Worst case:** DB down hone pe RAM barhta rehta → OOM crash.
- **Solution:** **Redis** with **TTL** + `maxmemory-policy allkeys-lru` + `maxmemory` set; data structure: session → **Hash**, messages → **List with LTRIM** (capped) ya **Sorted Set** (timestamp score). Agar Redis bhi down → bounded **`cachetools.LRUCache(maxsize=N)`**, plain dict **nahi**.
- **Kyun (why):** plain dict kabhi saaf nahi hota (leak); **TTL + LRU eviction** se memory **bounded** rehti hai. Sahi data structure se memory kam + access tez. Warna Redis bhi RAM bhar ke crash kar sakta hai.

### 5.6 Blocking I/O event loop pe

- **Kya hai:** `generate_image_bytes_fal()` (`image_generation.py:768`) aur `run_virtual_tryon()` (`virtual_tryon.py:252`) async handler ke andar synchronously.
- **Worst case:** Ek call poora event loop block → baaki users wait.
- **Solution:** **`httpx.AsyncClient`** (shared instance, connection pooling) — yahi use karni chahiye.
- **Kyun (why):** `asyncio.to_thread` bounded **thread pool** use karta hai — har call ek thread pakadta hai, pool bhar jaye to queue; 1000s users pe scale nahi hota (thread overhead). `AsyncClient` **koi thread nahi** — event loop sirf `await` karta hai, lakhs concurrent I/O ek thread pe handle karta hai + TCP/TLS reuse. `to_thread` sirf **temporary fallback** (jab SDK sync-only ho).

### 5.7 Input size limits nahi

- **Kya hai:** `ChatRequest.message` pe `max_length` nahi; `session_id` unbounded; `prompt` sirf `min_length=1`.
- **Solution:** Pydantic pe `max_length` (message 2000, session_id 255, prompt 1000), body size limit.

### 5.8 Prompt-injection guard sirf 7 English regex

**Kya hai:** `guardrails.py:9-16` mein sirf **7 exact English patterns** (`ignore previous instructions`, `act as DAN`, etc.); enforcement `nodes.py:2123`.

**Kyun issue (worst case):**
- Sirf English + exact words → Roman Urdu ("pichli hidayat bhool jao"), paraphrase, unicode se **bypass**.
- **Indirect injection:** LLM jo *data* padhta hai (product description, tool result, website content) usme bhi chhupa hukum ho sakta hai.
- Prompt filtering **probabilistic** hai — 100% pakadna namumkin. Isliye isay **security boundary** nahi banana.

**Solution (layered — sabse strong pehle) + "kyun":**

| # | Layer | Kya | Kyun (why) |
|---|-------|-----|-----------|
| 1 | **🔒 Architecture (primary)** | Price/discount/gift **server code** decide kare, **LLM kabhi nahi** | Code ke rules **deterministic** — injection ho bhi jaye to paisa/price pe asar nahi |
| 2 | **Tool param validation** | LLM tool call kare, par params **schema-validated** (zod/Pydantic) | LLM `discount:100` bhej bhi de to tool reject kar de |
| 3 | **Output guard** | Reply bhejne se pehle check — fake price/discount/floor leak na ho (`sanitize_agent_output` extend) | LLM ne galat likha ho to bhejne se pehle rok do |
| 4 | **System prompt hardening** | Role lock + "sirf ye kaam" | **Helpful, magar boundary nahi** — bypass hota hai, akela bharosa na karo |
| 5 | **Targeted classifier** | Sirf high-risk step pe (discount/handover) ek sasta check | Har message pe LLM-judge = **mehnga + slow**; isliye selective |

**Defense-in-depth diagram:**
```
   User message
        │
        ▼
 [1] System prompt hardening ──► (helpful, not enough)
        │
        ▼
 [2] cheap regex filter       ──► (first-line, bypassable)
        │
        ▼
 [3] LLM (reply + tool calls)
        │
        ▼
 [4] Tool param validation    ──► bad params reject
        │
        ▼
 [5] CODE decides price/gift  ──► ★ ASLI PROTECTION (LLM powerless)
        │
        ▼
 [6] Output guard             ──► no fake price leak
        │
        ▼
      reply
```

**Ek line:** Prompt injection ko **prompt se nahi, architecture se** rokte hain — LLM bolne de, **paisa code** decide kare. (Negotiation khud out of scope hai — §6.4.)

### 5.9 Plain HTTP (jaise §3.7)

- **Solution:** TLS + `chat.turabees.com` (same registrable domain — §7).

### 5.10 Verbose errors

- **Kya hai:** `image_generation.py:839` `f"...{str(e)}"`, `virtual_tryon.py:211` provider details, etc.
- **Solution:** Generic message, detail logs mein.

### 5.11 Config / infra leaks

- `docker-compose.yml`: DB creds `royal:royal`, Postgres host port publish (22006).
- `QUOTA_ADMIN_KEY` empty → reset endpoint khula.
- `REDIS_URL` `Settings` mein declared nahi → hamesha `redis://localhost:6379/0` default (§5.14).

### 5.12 Virtual try-on pe koi quota nahi (cost abuse) — [REVIEW: naya mila]

- **Kya hai:** `POST /api/virtual-try-on` (`virtual_tryon.py:214`) pe `require_quota` call hi nahi hoti.
- **Worst case:** Attacker loop chala ke unlimited try-on (fal Bria) chala sakta hai → direct cost.
- **Solution:** Quota lagao (image-generation jaisa). (Rate limit Phase 2 — §4.1.)
- 🔗 **Coordination (chatbot team):** try-on pe bhi quota lagayein.

### 5.13 `/test` workbench + `/docs` prod mein public — [REVIEW: naya mila]

- **Kya hai:** `GET /test` interactive testing workbench serve karta hai (`main.py:715`); FastAPI `/docs`, `/redoc`, `/openapi.json` default on.
- **Worst case:** Attacker ko poora API map + live testing tool mil jata hai.
- **Solution (env-based):** `FastAPI(docs_url=None, redoc_url=None)` **sirf** jab `APP_ENV=prod`; dev mein docs on (easy). `/test` workbench bhi isi `ENABLE_DEV_ENDPOINTS` flag ke peeche — dev mein on, prod mein off.

### 5.14 `REDIS_URL` Settings mein declared nahi — [REVIEW: naya mila]

- **Kya hai:** `memory_service` `getattr(settings, "REDIS_URL", None)` use karta hai → hamesha default `redis://localhost:6379/0`.
- **Worst case:** Prod mein ghalat Redis (ya fail) → cache silently memory-fallback pe chala jata hai.
- **Solution:** `REDIS_URL` ko `Settings` mein declare + prod validation.

---

## 6. REMEDIATION PLAN (naya kaam — kya banana/change karna hai)

### 6.1 Auth: JWT → HttpOnly + Secure cookie (admin + root domain)

**Maqsad:** JWT `localStorage` se nikaal ke `HttpOnly; Secure` cookie mein daalo, aur cookie **root-level domain** pe set ho taake subdomains bhi use kar sakein.

**Kya change:**
- **Backend login:** response mein `Set-Cookie: <name>=<jwt>; HttpOnly; Secure; SameSite=Lax; Domain=.turabees.com; Path=/`.
- **Frontend:** `withCredentials: true` (axios) / `credentials:'include'` (fetch). Token padhne/likhne ki zaroorat khatam.
- **CORS:** `credentials:true` + **exact origins** (already list hai — trailing slash fix).

**Env-based behaviour:**

| Setting | Dev | Prod |
|---|---|---|
| `Secure` | false | true |
| `SameSite` | Lax | Lax (chatbot bhi `*.turabees.com` pe hai, to none ki zaroorat nahi) |
| `Domain` | (khali — host-only) | `.turabees.com` |

**✅ Resolved (aapne confirm kiya):** **Prod mein sab kuch `*.turabees.com` ke under hoga** — jaise `api.turabees.com`, `chat.turabees.com`, `admin.turabees.com`, `www.turabees.com`. Isliye server `Domain=.turabees.com` cookie **set kar sakta hai** aur subdomains bhi usay receive karenge. Bas ensure karo ke API/chatbot unhi subdomains pe host hon (abhi dev mein `devssh.xyz` / `167.114.96.66` hain — wo sirf dev ke liye theek hain).

**Cookie flow — DEV vs PROD:**

```
── DEV (localhost) ────────────────────────────────────────────────
   Browser(localhost:5173)             API(localhost:5000)
       │  POST /auth/login                 │
       │ ─────────────────────────────────▶│
       │  Set-Cookie: auth=JWT;            │   (no Domain, Secure=false)
       │ ◀─────────────────────────────────│
       │  credentials:true                 │
       │  ▶ har request pe cookie bhejta   │   → simple, kaam karta hai
────────────────────────────────────────────────────────────────────

── PROD (*.turabees.com) ──────────────────────────────────────────
   Browser              api.turabees.com        chat.turabees.com
       │  POST /auth/login     │                       │
       │ ─────────────────────▶│                       │
       │  Set-Cookie: auth=JWT;│                       │
       │   HttpOnly; Secure;   │                       │
       │   SameSite=Lax;       │                       │
       │   Domain=.turabees.com│                       │
       │ ◀─────────────────────│                       │
       │  (browser khud cookie bhejta HAR *.turabees.com pe)
       │ ─────────────────────▶ api  ✅                 │
       │ ─────────────────────────────────────────────▶ chat ✅
       │                                     (subdomain → cookie milti)
────────────────────────────────────────────────────────────────────
```
**Kyun ye approach:** `HttpOnly` = JS (XSS) token na padh sake; `Domain=.turabees.com` = ek hi cookie sab subdomains (api/chat/admin) ko mile; `Secure` = sirf HTTPS.

### 6.2 Guest + Chatbot session id → prod mein server-issued HttpOnly cookie

**Maqsad (final requirement):** **Prod** mein session id **browser generate na kare** — **server** cookie set kare. **Dev** mein jaisa abhi hai (browser bana ke `session_id` bheje) — aasan development ke liye — aur FastAPI/us chatbot wo **receive** bhi karein. Prod mein frontend `credentials: true` ke saath bhejta hai, chatbot cookie **receive** aur **utilize** karta hai.

**Kya change:**
- **Prod:** pehli request pe server session banata hai → `Set-Cookie: <session>=<signed-id>; HttpOnly; Secure; SameSite=Lax; Domain=.turabees.com; Path=/`. Frontend `credentials:true` bhejta hai; chatbot cookie se session uthata hai. Browser JS na bana sakta na badal sakta.
- **Dev:** jaisa abhi hai — browser localStorage se id banata hai, FastAPI/chatbot `session_id` **receive** karte hain (yahi testing ke liye convenient).
- **Dono modes support:** request mein cookie mile to cookie se, warna body `session_id` (dev fallback).

**Env behaviour:**

| | Dev | Prod |
|---|---|---|
| Session kaun banata | browser (localStorage) — jaise abhi | **server** (cookie) |
| Cookie HttpOnly/Secure/Domain | — | true / true / `.turabees.com` |
| Chatbot/Backend reads | body `session_id` | **cookie** |
| Frontend request | normal (jaise abhi) | `credentials: true` |

**Session flow — DEV vs PROD:**

```
── DEV ────────────────────────────────────────────────────────────
   Browser                        Chatbot(FastAPI)
     │ localStorage se session_id      │
     │ {session_id:"user-123", msg} ──▶│ session_id BODY se use
────────────────────────────────────────────────────────────────────

── PROD ───────────────────────────────────────────────────────────
   Browser              Backend              Chatbot
     │ 1st request         │                     │
     │ ───────────────────▶│ session banao        │
     │ ◀─ Set-Cookie:      │ (signed id,          │
     │    royal_session;   │  Domain .turabees)   │
     │    HttpOnly;Secure  │                     │
     │  credentials:true   │                     │
     │ ▶ har request pe cookie ─────────────────▶ │ cookie se session uthao
     │   (browser JS na bana/padh sake)           │ (body session_id ignore)
────────────────────────────────────────────────────────────────────
```
🔗 **Coordination:** Cookie **backend** issue kare, **chatbot** wahi naam padhe (§6.6 #1).

**Quota (important):** Quota waisa hi **per-session** rahega. **Per-account/per-IP limit nahi lagani** — shared public IP (society/university/NAT) pe bhout devices hoti hain, IP-based limit ghalat blocks karegi. Better sirf itna: prod mein session **server-issued + HttpOnly** ho, to normal user JS se id badal nahi sakega.

### 6.3 Backend double verification (price) — is plan ka core

**Maqsad:** Backend client/chatbot ke bheje price pe **bharosa na kare** — khud verify/compute kare. Negotiation **change nahi** karni (§6.4).

**Kyun ye approach (why):** Client/chatbot ka number **replace ho sakta hai** (public endpoint + DevTools). Server khud `spec` se compute kare to attacker ke paas manipulate karne ki **jagah hi nahi bachti**. Aur ye deterministic hai — same spec → same price.

**Verification flow:**
```
   Client/chatbot ──{ spec, (unit_price optional) }──▶ Backend
                                                        │
                          ┌─────────────────────────────┤
                          │ 1. catalog?  → DB price      │
                          │ 2. custom?   → calculate(spec)│
                          │ 3. floor check               │
                          │ 4. gift      → server decide │
                          └─────────────────────────────┤
                                                        ▼
                             Order (server ka price)  ← client ka number IGNORE
```

**Current problem:** `order.controller.ts` aur `salesAgent.controller.ts` client ka `unit_price` seedha use karte hain.

**Kya change (backend only):**
1. **Catalog/standard item:** `catalog_product_id` se **DB ka price** lo; `product.floorPrice` floor. Client ka number **ignore**.
2. **Custom item:** `spec` (style/fabric/embroidery/measurements/qty) se **backend me calculate** karo (`pricing.service.ts` — chatbot `price_calculator.py` formula port karo):
   ```
   fabric_cost = fabricLength × fabricRate
   subtotal    = style.basePrice + fabric_cost + embroideryCost
   list_price  = subtotal + (subtotal × markup)
   floor       = subtotal            (markup ke bina)
   ```
3. **Floor check:** koi bhi price `floor` se neeche na jaaye.
4. **Session:** `CheckoutSession` (already maujood) mein `spec`/items + (gift `isGift`) rakhо; order ke waqt **session se** items uthao, frontend se nahi.
5. **Gift:** `isGift:true, unitPrice:0` server mark kare; eligibility server check kare (§4.13).
6. **Double verification:** agar client/custom ke paas `spec` na ho aur recompute na ho paaye → **request reject** karo, guess na karo.

**Out of scope:** negotiation engine / chatbot negotiation logic — **chhеdna nahi**.

### 6.4 Out of Scope (is plan mein change NAHI — Phase 2)

- **Rate limiting (backend + chatbot)** — abhi nahi; baad mein per-identity + Redis ke saath **poori** implement hogi. Interim mitigation §4.1.
- Chatbot ka `NegotiationEngine` / negotiation node / discount ladder.
- Coupon ka logic (jaisa hai waisa).
- Sirf backend **double verification** (upar §6.3).

### 6.5 Dev vs Prod summary (env-driven)

| Cheez | Dev | Prod |
|---|---|---|
| JWT location | localStorage (current) | HttpOnly Secure cookie |
| Cookie Domain | host-only | `.turabees.com` |
| Session id | localStorage | server cookie |
| CORS origins | reflect (dev only) | exact list (no `*`) |
| Credentials | true | true |
| Chatbot URL | http (dev) | https `chat.turabees.com` |
| Dev endpoints (`/test`, docs, `clear-all`) | on (`ENABLE_DEV_ENDPOINTS=true`) | off (`false`) |

### 6.6 Cross-service coordination (chatbot ↔ backend ↔ frontends) — [naya, important]

Ye changes **aadhe ek project mein nahi** ho sakte. Neeche contract hai — `*` = wahan **chatbot team ko bhi change** karna hai. Chatbot team is plan ko padh ke apne changes kar sake, isliye ye table lazmi hai.

**Coordination map (kaun kisse coordinate kare):**

```
                    ┌───────────────────────────────────────────┐
   frontend-        │            website-backend                │       frontend-
   clientside ─────▶│   OWNER: cookie, price, gift, quota       │◀───── adminside
   (Clerk)          └───────────────────┬───────────────────────┘
   (credentials:true)                   │
                                        ▼
                              Postgres + Redis + S3

   chatbot-backend ────────────────────▶ website-backend
   (Python/FastAPI)                     (server-to-server)
   🔗 chatbot team KO change karna hai:
       #1 cookie READ (name/domain shared)
       #2 /limits call (session cookie se)
       #3 checkout payload (spec bheje → backend verify)
       #4 gift accessory bheje (backend isGift decide)
       #5 CORS/credentials align
       #6 link me sirf ?s= (na ?cart=)
```

| # | Cheez | Owner (Kaun decide/issue kare) | Chatbot team ko | Frontend team ko | Worst case (coordinate na ho to) |
|---|---|---|---|---|---|
| 1 | **Session cookie** | Backend issue kare | Wahi cookie **read** kare (prod), body `session_id` (dev) | `credentials:true` bheje | Dono apni cookie set karein → ek doosre ko **overwrite** |
| 2 | **Quota limits** | Backend `/limits` | Session id cookie se bheje | — | Limits ghalat session pe lag |
| 3 | **Checkout payload** | Backend verify kare | `spec` + `discount` bheje (ya agreed number) | — | Backend reject / galat price |
| 4 | **Gift/accessory** | Backend decide kare | accessory bheje; `isGift` server lagaye | £0 dikhaye | Gift charged / free-gift exploit |
| 5 | **CORS + credentials** | Dono | `chat.turabees.com` origin allow kare | exact origin | Cookie cross-site na jaye |
| 6 | **`?cart=` link** | Chatbot/Frontend | link `?s=` hi de | `?s=` handle kare | Insecure tampering |
| 7 | **Presign upload (§4.19)** | Backend | — (chatbot scope nahi) | streaming unzip + PUT | Server load |

**Cookie coordination (sabse nazuk — #1):**
- **Ek hi owner** ho cookie ka. Mashwara: **backend** `royal_session` set kare `Domain=.turabees.com` pe.
- Chatbot **wahi naam** padhe; signature **shared secret** se verify kare (ya opaque session key maane).
- **Pehle ye tay karo:** name, domain, TTL, SameSite, Secure, signed-format.
- **Rule:** Ek jagah issue ho — warna ek doosre ki cookie clobber kar dega.
- **Worst case:** Backend `royal_session` set kare aur chatbot bhi apni `session` set kare → dono alag session pe chalenge → quota/history match nahi.

**Checkout coordination (#3, #4):**
- Negotiation **out of scope** (§6.4) — lekin backend verify kar raha hai. To chatbot ko confirm karna hoga: wo `unit_price` bhejega ya `spec`? Agar `unit_price` bhejega, to backend usay **recompute** kar ke override/reject karega — chatbot ko response handle karna hoga.
- Gift: chatbot accessory bhejta hai, **eligibility backend** decide karega (`isGift`) — chatbot ko gift ko "free" maan ke assume nahi karna.

**Direct call (frontend → chatbot) — [REVIEW: clarify]:**
- Frontend **chatbot ko direct** call karta hai (chat + try-on) — ye **change nahi ho raha**, plan mein force nahi kiya ke band karo.
- Bas itna chahiye: chatbot ko bhi **wahi session identity** mile (cookie `.turabees.com`) → isliye chatbot ke CORS mein storefront origin (`www.turabees.com`) allow + `credentials:true` ho (§5.4).
- Backend sirf **cookie/price/gift/quota** ka owner hai — chatbot ka chat flow usse alag (direct) hai.

**ENV coordination (dev/prod):**
- Dono services **same env convention** use karein (`APP_ENV=dev|prod` / `ENABLE_DEV_ENDPOINTS`), taake local pe sab khula aur prod pe sab band ho — ek jagah ka flag doosre ko bhi samajh aaye.

---

## 7. Worst-case Analysis (cheezein jo toot saktin hain — "ek theek, doosri kharab na ho")

1. **Cookie domain ✅ resolved:** Prod mein sab `*.turabees.com` pe hoga (aapne confirm kiya) → `Domain=.turabees.com` cookie set ho jayegi aur subdomains use karenge. Bas API/chatbot ko `api.` / `chat.` subdomains pe host karna hai. (Dev mein alag domains — wahan `Secure/Domain` off rahenge, koi issue nahi.)
2. **CSRF trade-off:** localStorage (Bearer) mein CSRF nahi tha (browser auto-send nahi karta). Cookie mein **auto-send** hota hai → **CSRF risk**. Isliye `SameSite` + CSRF token/origin check zaroori. "XSS fix karte hue CSRF na bana do."
3. **Chatbot same domain:** `chat.turabees.com` (same registrable domain) hone ki wajah se normal `SameSite=Lax` cookie chalti hai; `SameSite=None` ki zaroorat nahi.
4. **Quota reset (acceptable):** HttpOnly cookie JS se edit nahi hoti. User cookie delete/incognito se naya session le sakta hai — lekin ye **acceptable** hai. **Per-account/per-IP limit nahi lagani** (shared public IP pe many devices). Design: quota per-session hi rahega.
5. **Clerk coexistence:** Client-side Clerk apni cookies/storage use karta hai. Apni cookie ka naam **alag** rakhna; Clerk ke saath conflict na ho. Client-side pe humara JWT cookie waise bhi zaroori nahi (Clerk handles auth) — ye mainly **admin** ke liye hai.
6. **Localhost dev:** `Secure`/`Domain` dev mein kaam nahi karte → dev mein `Secure=false`, host-only cookie (ya localStorage).
7. **Gift double-charge/mark:** §4.13 — check karna zaroori, warna customer charge ho ya free-gift exploit.
8. **Public repo:** History mein purane secrets ho sakte hain — sirf HEAD check kaafi nahi.

---

## 8. Priority Roadmap

**P0 (launch blocker):**
- Secrets rotate + git clean; chatbot repo private.
- Auth: coupons, design-lab `/admin/*`, intelligence-search (§4.17), orders (`my-orders`/`merge`).
- `/orders/checkout` pe `user_id` token se (§4.16).
- Backend price double verification (§6.3) + gift/coupon server-side (§4.13).
- Chatbot dev endpoints **env-controlled** (`ENABLE_DEV_ENDPOINTS`) + CORS fix (§5.2, §5.13, §5.4); virtual try-on pe quota (§5.12).
- JWT fallback secrets hatao (§4.6); Clerk webhook mandatory (§4.7).
- **Coordination (chatbot team):** cookie ownership, checkout payload, gift flag, CORS/environment — §6.6 contract pehle tay karo.

**P1:**
- JWT → HttpOnly cookie + root domain (§6.1) — prod sab `*.turabees.com` pe (api./chat./admin.).
- Session id → prod mein server cookie (§6.2); dev mein body `session_id`.
- Storefront XSS sanitize (§3.6), admin Quill (§2.5).
- Plain HTTP → HTTPS (§3.7, §5.9).
- PII URL se hatao (§3.3); full catalog paginate (§3.5).
- `/test` + `/docs` env-controlled (§5.13) — dev on, prod off.

**P2:**
- Unbounded queries/pagination, N+1 (§4.10).
- **Bulk import → direct-to-S3 presigned upload + variant worker (§4.19).**
- Chatbot memory growth (§5.5), blocking I/O (§5.6), input limits (§5.7), `REDIS_URL` (§5.14).
- `express.json` 10kb route-wise adjust (§4.18).
- Verbose errors (§4.14, §5.10), dead code (§4.15), seed creds (§4.12), console.log password (§4.11).

**Phase 2 (deferred — baad mein poori):**
- **Rate limiting** (backend + chatbot) — per-identity keys + Redis store; interim mitigation §4.1.

---

## 9. Naya kya lagega / kabhi nahi

- **Naya table:** 0 (sab `CheckoutSession`, `Product.floorPrice`, `Calculation*`, `Coupon` existing pe).
- **Coupon change:** 0.
- **Negotiation change:** 0 (out of scope).
- **Naya code:** `pricing.service.ts` (server calculator), auth/cookie middleware, session-cookie flow, auth guards. (Rate limiters = **Phase 2**.)
- **Naya infra (recommended):** **Redis** — job status + rate-limit store + variant-generation queue; **presign** endpoints + background worker (§4.19).
- **Cross-service:** chatbot team ke changes **§6.6** mein (cookie read, checkout payload, gift, CORS/env).

---

## 10. Self-Review (senior-engineer pass) — kya verify hua, kya mila

**Claims jo code se dobara verify kiye (sahi nikle):**
- Rate limiters `noopLimiter` (`rateLimiter.middleware.ts`) ✅
- Coupons CRUD bina auth (`coupon.routes.ts`) ✅
- Design-lab `/admin/*` bina auth (`designLab.routes.ts`) ✅
- Orders `/my-orders` + `/merge` bina auth, email query se (`order.routes.ts`, `order.controller.ts`) ✅
- Checkout client `unit_price` trust (`order.controller.ts`, `salesAgent.controller.ts:991`) ✅
- JWT fallback secrets (`env.ts:20`, `salesAgent.controller.ts:1103/1181`) ✅
- **Clerk webhook secret unset pe verification skip** (`webhook.routes.ts` — poora padha) ✅
- Chatbot open endpoints (`main.py:605–712` poora padha) ✅
- CORS `*` + credentials (`main.py:120–126`) ✅
- Quota client `session_id` pe (`quota_service.py`) ✅ — ek correction neeche
- Frontend `.env` git-tracked (`git ls-files`) ✅; OpenRouter key hardcoded (`bespoke.functions.ts`) ✅
- Admin JWT localStorage (`store/auth.ts`) ✅
- `HighlightedText` regex raw input (`HighlightedText.tsx`) ✅
- Blog `dangerouslySetInnerHTML` (`heritage-blog_.$slug.tsx:120`) ✅
- Shop-collection full-catalog fetch (`shop-collection-content.jsx:17`) ✅
- 3 repos private, chatbot repo **public** (GitHub API) ✅

**Corrections (report pehle ghalat/imprecise tha):**
1. **§5.3:** `session_id` hata ke bypass **nahi** hota (endpoint 400 deta hai) — bypass **fresh ids rotate** kar ke hota hai. Cookie fix isi ko band karti hai.
2. **§4.13:** gift sirf "free mark nahi hota" nahi — **discount client-side apply hota hai** aur `/coupons/validate` fail hone pe **local fallback** se lagta hai (`cart-drawer.tsx:331–343`); backend order-time pe coupon **re-validate nahi** karta. Ye §6.3 ke scope mein add hua.

**Scope change (aapke kehne pe):** **Rate limiting → Phase 2** (abhi out of scope). §0, §1, §4.1, §4.12, §5.12, §6.4, §8, §9 update hue. Risk + interim mitigation §4.1 mein.

**Naye issues jo review mein mile (pehle miss the):**
- §4.16 `/orders/checkout` arbitrary `user_id` (unauthenticated attribution).
- §4.17 intelligence-search public write (P0 bullet tha, detail section missing tha).
- §4.18 `express.json 10kb` — legit checkout 413 ho sakta hai (functional).
- §5.12 virtual try-on pe koi quota nahi (cost abuse).
- §5.13 `/test` + `/docs` prod mein public.
- §5.14 `REDIS_URL` Settings mein declared nahi.
- §2.6 admin: no refresh token / revocation (7-din token).

**Jo cheez maine verify ki aur sahi nikli (galat alarm nahi):**
- Stripe webhook signature **theek** hai (`constructEvent`).
- Image-generation endpoint `session_id` **require** karta hai (400) — quota khali chhod ke bypass nahi.
- Virtual try-on mein `_is_private_host` check hai (URL-fetch SSRF ka aadi protection).
- SQL injection ka risk low (raw `$queryRaw` use nahi hota).

**Bacha hua risk jo "known-unknown" hai (code se 100% confirm nahi, runtime pe verify karna hoga):**
- Gift/bundle discount **asal mein charge hota hai ya sirf display** — iske liye ek live checkout test chahiye.
- `sk_live_...` Clerk key **real hai ya dummy** — Clerk dashboard se confirm karo.
- `express.json 10kb` pe asli checkout payload kitna bara banta hai — ek test order se measure karo.

---

*Report end. Ye audit-only hai — koi code change nahi kiya gaya.*
