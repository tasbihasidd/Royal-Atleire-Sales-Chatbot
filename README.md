# Royal Atelier Sales Agent — Docker + PostgreSQL

This version includes:

- FastAPI `/chat` endpoint
- LangGraph sales-agent flow
- LangChain tools (products, fabrics, inventory, negotiation, handover)
- PostgreSQL session/chat persistence
- Dockerfile and docker-compose.yml

## Services

```text
api       → FastAPI chatbot on http://localhost:8015
postgres  → PostgreSQL exposed on localhost:5434
```

## Start

```bash
cp .env.example .env
# Add OPENROUTER_API_KEY in .env
docker compose up --build
```

## Test chat

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
