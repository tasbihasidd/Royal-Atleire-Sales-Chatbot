# Royal Atelier Sales Agent — Docker + PostgreSQL

This version includes:

- FastAPI `/chat` endpoint
- LangGraph sales-agent flow
- LangChain tools (products, fabrics, inventory, negotiation, handover)
- PostgreSQL session/chat persistence
- LangSmith observability + per-session AI cost estimates
- Dockerfile and docker-compose.yml

## Ports

| Mode | API | Postgres |
|------|-----|----------|
| **Local** (uvicorn on host) | `http://localhost:8015` | not reachable from host (compose keeps DB internal) — use full `docker compose` or temporarily publish `5432` |
| **Live / Docker** (`docker compose`) | `http://localhost:2006` | container DNS `postgres:5432` (no host port) |

`.env` is for local and live config. `docker-compose.yml` overrides only `DATABASE_URL` (container DNS). **`PORT` and `BASE_URL` come from `.env`** — compose publishes `${PORT}:${PORT}` (default 2006 if unset). On live set e.g. `PORT=2006` and `BASE_URL=https://your-api-domain.com`.

## Local (recommended for development)

```bash
cp .env.example .env
# set FAL_KEY / BACKEND_API_TOKEN as needed

# Postgres only — DB has no host port; for local uvicorn you need a temp port publish
# or run the full stack: docker compose up --build -d
docker compose up -d postgres

python -m uvicorn app.main:app --host 0.0.0.0 --port 8015 --reload
```

```bash
curl -fsS http://localhost:8015/health
```

## Live / Docker

Set `PORT` and `BASE_URL` in `.env` (compose does not hardcode them):

```bash
PORT=2006
BASE_URL=http://localhost:2006
# live server:
# BASE_URL=https://your-api-domain.com
```

```bash
docker compose up --build -d
curl -fsS "http://localhost:${PORT:-2006}/health"
```

`docker-compose` loads `.env` via `env_file`, so `PORT`, `BASE_URL`, `LANGSMITH_*`, and cost rate vars are passed through automatically.

## LangSmith + per-user cost

1. Create a project at [smith.langchain.com](https://smith.langchain.com) and copy an API key.
2. In `.env`:

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=royal-atelier-sales-agent
```

3. Chat with a stable `session_id`, then in LangSmith filter runs by metadata `session_id` to see that user’s graph + LLM spans and costs.
4. Local quick totals (in-memory for this process):

```bash
curl "http://localhost:8015/sessions/s1/cost"
```

Optional:

- `INCLUDE_COST_IN_RESPONSE=true` — attach `state.cost` on `/chat`
- `LANGCHAIN_HIDE_INPUTS=true` / `LANGCHAIN_HIDE_OUTPUTS=true` — redact bodies in prod
- Per-call cost comes from Fal Platform APIs (`/v1/models/pricing`, `/v1/models/billing-events`) using `FAL_KEY`
- `LLM_COST_*` / `FAL_IMAGE_COST_USD` — offline fallbacks only

## Test chat (local)

```bash
curl -X POST http://localhost:8015/chat \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","message":"Mujhe Nikah ke liye cream Sherwani chahiye, size 40 available hai? Discount bhi chahiye. Meri height 6'\''4 hai."}'
```

Response includes `reply`, `imageurl` (absolute product image when search returns results), and `state`.

## Get saved session messages

```bash
curl "http://localhost:8015/sessions/s1/messages"
```

## Chatbot daily quotas

Limits (messages / custom images per day) are **hardcoded in env** until Royal Attire ships `GET /chatbot/quotas`. Usage is counted from **today’s** chat + image history (`Asia/Karachi`). Virtual try-on is **not** under this quota.

```bash
CHATBOT_QUOTA_ENFORCE=true
CHATBOT_QUOTA_USE_BACKEND=false   # set true when RA GET is live
CHATBOT_AI_MESSAGES_PER_DAY=10
CHATBOT_CUSTOM_IMAGES_PER_DAY=2
```

```bash
curl "http://localhost:8015/sessions/s1/quota"
```

When backend is ready: set `CHATBOT_QUOTA_USE_BACKEND=true` — chatbot calls `GET …/sales-agent/chatbot/quotas` for limits; counting stays local.

## Where real-time backend APIs connect

Backend base URL: `BACKEND_API_BASE_URL` (e.g. `https://royal-attire-api.devssh.xyz/api/v1`).

These tool actions fetch real-time data from the Royal Attire sales-agent API:

```text
POST /sales-agent/products/search
GET  /sales-agent/products/{product_id}
POST /sales-agent/fabrics/search
GET  /sales-agent/fabrics/{catalog_code}
POST /sales-agent/inventory/check
POST /sales-agent/handover/create
```

## Important

This MVP creates PostgreSQL tables automatically on startup using SQLAlchemy. For production, replace `init_db()` table creation with Alembic migrations.
