# Royal Atelier Sales Agent — Docker + PostgreSQL

This version includes:

- FastAPI `/chat` endpoint
- LangGraph sales-agent flow
- LangChain tools (products, fabrics, inventory, negotiation, handover)
- PostgreSQL session/chat persistence
- Dockerfile and docker-compose.yml

## Ports

| Mode | API | Postgres |
|------|-----|----------|
| **Local** (uvicorn on host) | `http://localhost:8015` | `localhost:22006` (docker postgres) |
| **Live / Docker** (`docker compose`) | `http://localhost:2006` | container DNS `postgres:5432` |

`.env` is for local host runs. `docker-compose.yml` overrides `PORT`, `BASE_URL`, and `DATABASE_URL` for the API container.

## Local (recommended for development)

```bash
cp .env.example .env
# set FAL_KEY / BACKEND_API_TOKEN as needed

# Postgres only (or full stack — API on 2006 won't block host :8015)
docker compose up -d postgres

python -m uvicorn app.main:app --host 0.0.0.0 --port 8015 --reload
```

```bash
curl -fsS http://localhost:8015/health
```

## Live / Docker (port 2006)

```bash
docker compose up --build -d
curl -fsS http://localhost:2006/health
```

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
