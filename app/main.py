from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from app.agent.graph import sales_agent_graph
from app.config import settings
from app.core.logging_config import (
    get_request_id,
    reset_request_id,
    safe_len,
    sanitize_query_params,
    set_request_id,
    setup_logging,
)
from app.routes.image_generation import router as image_generation_router
from app.routes.virtual_tryon import router as virtual_tryon_router
from app.services.chat_store import chat_store
from app.services.cost_tracking import (
    clear_session_cost,
    get_session_cost,
    session_cost_scope,
)
from app.services.quota_service import get_quota_status, require_quota
from app.services.memory_service import memory_service
from app.schemas.profile import CustomerProfileSchema
from app.schemas.negotiation import NegotiationStateSchema
from app.services.backend_api import BackendAPIError, backend_api, resolve_backend_asset_url
from app.services.image_store import image_store

from app.services.db import init_db

setup_logging()
logger = logging.getLogger(__name__)


STATIC_DIR = Path("static")
GENERATED_DIR = STATIC_DIR / "generated"
GENERATED_DIR.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Application startup: initializing database")
        await init_db()
        logger.info("Application startup complete")
    except Exception:
        # Allow API to boot for hard-tests / degraded envs without Postgres.
        # Chat will use in-memory session fallback when DB calls fail.
        logger.exception(
            "Application startup: database init failed — continuing with in-memory fallback"
        )
    yield
    try:
        await backend_api.aclose()
    except Exception:
        logger.exception("Failed to close backend API client")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        token = set_request_id(request_id)
        request.state.request_id = request_id

        start = time.perf_counter()
        query_params = sanitize_query_params(dict(request.query_params))
        logger.info(
            "Request start method=%s path=%s query_params=%s",
            request.method,
            request.url.path,
            query_params,
        )

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.exception(
                "Request failed method=%s path=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                duration_ms,
            )
            raise
        else:
            duration_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "Request end method=%s path=%s status_code=%s duration_ms=%.2f",
                request.method,
                request.url.path,
                response.status_code,
                duration_ms,
            )
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            reset_request_id(token)


app = FastAPI(
    title="Royal Atelier Sales Agent",
    description="LangGraph + LangChain sales chatbot with PostgreSQL session storage.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(image_generation_router)
app.include_router(virtual_tryon_router)


class ChatRequest(BaseModel):
    session_id: str = Field(..., examples=["user-session-123"])
    message: str = Field(..., examples=["Mujhe Nikah ke liye cream Sherwani chahiye, size 40 available hai?"])


class ChatResponse(BaseModel):
    reply: str
    imageurl: str = ""
    state: dict[str, Any]
    executed_nodes: list[str] = Field(default_factory=list)


def _resolve_chat_image_url(result: dict[str, Any]) -> str:
    # Do not attach leftover catalog images on greeting / off-topic / general turns, or when gathering customization preferences without a bespoke image.
    steps = result.get("required_steps") or []
    executed = result.get("executed_nodes") or []
    user_msg = str(result.get("user_message") or "").lower()

    # Price / lead-time / pure checkout follow-ups: do NOT re-flash the bespoke card
    # unless we generated a new image this turn.
    price_followup = any(
        w in user_msg
        for w in (
            "price",
            "qeemat",
            "qemat",
            "kitna",
            "kitne",
            "cost",
            "rate",
            "pricing",
            "lead time",
            "kitni",
            "paisa",
            "amount",
        )
    )
    checkout_followup = bool(result.get("checkout_url") or result.get("checkout_hand_off_note"))
    regenerated = "generate_custom_design" in executed
    if (price_followup or checkout_followup) and not regenerated:
        return ""

    if result.get("sales_stage") == "customization" and not result.get("custom_image_url"):
        return ""

    if result.get("intent") in ("general",) and not any(
        step in steps for step in ("search_products", "get_product_details", "search_fabrics")
    ) and not result.get("custom_image_url"):
        return ""
        
    if result.get("custom_image_url"):
        return result.get("custom_image_url")
    if isinstance(result.get("custom_design_result"), dict) and result.get("custom_design_result", {}).get("image_url"):
        return result.get("custom_design_result", {}).get("image_url")


    products = result.get("products") or []
    selected_product_id = result.get("selected_product_id")
    product_details = result.get("product_details") or {}

    image_path: str | None = None

    if selected_product_id and isinstance(product_details, dict):
        if str(product_details.get("product_id")) == str(selected_product_id):
            image_path = product_details.get("image_url")

    if not image_path and selected_product_id:
        for product in products:
            if str(product.get("product_id")) == str(selected_product_id):
                image_path = product.get("image_url")
                break

    if not image_path and products:
        image_path = products[0].get("image_url")

    return resolve_backend_asset_url(image_path)


@app.get("/")
def root():
    return {
        "message": "Royal Atelier API is running",
        "chat_api": "/chat",
        "image_generation_api": "/api/generate-wedding-image",
        "wedding_image_fabrics": "/api/generate-wedding-image/fabrics?category=Sherwani",
        "virtual_tryon_api": "/api/virtual-try-on",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    try:
        logger.info(
            "Chat request start session_id=%s message_length=%s",
            request.session_id,
            safe_len(request.message),
        )

        quota = await require_quota(request.session_id, "ai_message")
        if not quota.allowed:
            logger.info(
                "Chat quota exceeded session_id=%s used=%s limits=%s",
                request.session_id,
                quota.used,
                quota.limits,
            )
            soft = quota.message or (
                "Aaj ki AI chat limit poori ho chuki hai. Kal phir try karein."
            )
            return ChatResponse(
                reply=soft,
                imageurl="",
                executed_nodes=[],
                state={
                    "quota_exceeded": True,
                    "quota": quota.to_dict(),
                },
            )

        await chat_store.ensure_session(request.session_id)
        await chat_store.add_message(
            session_id=request.session_id,
            role="user",
            content=request.message,
        )

        history = await chat_store.get_messages(request.session_id, limit=20)
        logger.info(
            "Chat history loaded session_id=%s message_count=%s",
            request.session_id,
            len(history),
        )

        customer_contact = await chat_store.load_customer_contact(request.session_id, history)
        session_context = await chat_store.load_session_context(request.session_id, history)
        profile, negotiation_state = await memory_service.load_session_context(request.session_id)


        # Keep metadata (esp. products) so get_product_details can rematch
        # previously shown pieces by name after a later empty search turn.
        initial_state = {
            "session_id": request.session_id,
            "user_message": request.message,
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "metadata": m.get("metadata") or {},
                }
                for m in history
            ],
            "products": session_context.get("products") or [],
            "product_details": session_context.get("product_details"),
            "inventory_result": session_context.get("inventory_result"),
            "selected_product_id": session_context.get("selected_product_id"),
            "handover_pending": session_context.get("handover_pending", False),
            "event_type": profile.event_type or session_context.get("event_type"),
            "product_type": session_context.get("product_type"),
            "color": session_context.get("color"),
            "size": profile.jacket_size or session_context.get("size"),
            "budget": profile.budget if profile.budget is not None else session_context.get("budget"),
            "wedding_date": profile.wedding_date or session_context.get("wedding_date"),
            "height": profile.height or session_context.get("height"),
            "chest": profile.chest or session_context.get("chest"),
            "waist": profile.waist or session_context.get("waist"),
            "shoulder": session_context.get("shoulder"),
            "sleeve": session_context.get("sleeve"),
            "jacket_length": session_context.get("jacket_length"),
            "size_chart": session_context.get("size_chart"),
            "body_measurements": session_context.get("body_measurements"),
            "measurement_path": session_context.get("measurement_path"),
            "measurement_prompt": session_context.get("measurement_prompt"),
            "measurement_result": session_context.get("measurement_result"),
            "customization_stage": session_context.get("customization_stage"),
            "cut_style": session_context.get("cut_style"),
            "custom_image_url": session_context.get("custom_image_url"),
            "custom_design_result": session_context.get("custom_design_result"),
            "custom_instructions": session_context.get("custom_instructions"),
            "price_quote": session_context.get("price_quote"),
            "customize_pricing_mode": session_context.get("customize_pricing_mode"),
            "sales_stage": profile.sales_stage or session_context.get("sales_stage"),
            "buying_intent": profile.buying_intent or session_context.get("buying_intent"),
            "customer_profile": profile.model_dump(),
            "selected_variation_id": session_context.get("selected_variation_id")
            or profile.selected_variation_id,
            "selected_variation_name": session_context.get("selected_variation_name")
            or profile.selected_variation_name,
            "selected_product_variation_id": session_context.get("selected_product_variation_id")
            or profile.selected_product_variation_id,
            "selected_product_variation_name": session_context.get("selected_product_variation_name")
            or profile.selected_product_variation_name,
            "product_variations": session_context.get("product_variations") or [],
            "negotiation_state": negotiation_state.model_dump()
            if not session_context.get("negotiation_state")
            else session_context.get("negotiation_state"),
            "selected_fabric_catalog_code": profile.selected_fabric_catalog_code
            or session_context.get("selected_fabric_catalog_code"),
            "fabrics": session_context.get("fabrics") or [],
            "style_context": [],
            "customer_contact": customer_contact,
            "handover_result": None,
            "executed_nodes": [],
            "checkout_url": session_context.get("checkout_url"),
            "checkout_session_id": session_context.get("checkout_session_id"),
            "shown_product_ids": session_context.get("shown_product_ids") or [],
        }


        config = {
            "configurable": {"thread_id": request.session_id},
            "metadata": {
                "session_id": request.session_id,
                "request_id": get_request_id(),
            },
            "tags": [
                "royal-atelier",
                "sales-agent",
                str(settings.LLM_PROVIDER or "fal"),
            ],
        }
        logger.info("LangGraph invoke start session_id=%s", request.session_id)
        with session_cost_scope(request.session_id):
            result = await sales_agent_graph.ainvoke(initial_state, config=config)
        executed_nodes = list(result.get("executed_nodes") or [])
        logger.info(
            "LangGraph invoke end session_id=%s intent=%s required_steps=%s "
            "executed_nodes=%s products_count=%s has_inventory=%s has_negotiation=%s "
            "has_measurement=%s has_handover=%s",
            request.session_id,
            result.get("intent"),
            result.get("required_steps"),
            executed_nodes,
            len(result.get("products") or []),
            result.get("inventory_result") is not None,
            result.get("negotiation_result") is not None,
            result.get("measurement_result") is not None,
            result.get("handover_result") is not None,
        )

        reply = (result.get("final_response") or "").strip()
        if not reply:
            logger.warning(
                "Chat empty final_response fallback session_id=%s",
                request.session_id,
            )
            reply = (
                "I apologise — I could not finish that reply. "
                "Please send your message again, or share WhatsApp for a Style Consultant."
            )

        imageurl = _resolve_chat_image_url(result)

        await chat_store.add_message(
            session_id=request.session_id,
            role="assistant",
            content=reply,
            metadata={
                "intent": result.get("intent"),
                "required_steps": result.get("required_steps"),
                "executed_nodes": executed_nodes,
                "imageurl": imageurl,
                "custom_image_url": result.get("custom_image_url"),
                "custom_design_result": result.get("custom_design_result"),
                "selected_product_id": result.get("selected_product_id"),
                "products": result.get("products", []),
                "product_details": result.get("product_details"),
                "inventory_result": result.get("inventory_result"),
                "negotiation_result": result.get("negotiation_result"),
                "measurement_result": result.get("measurement_result"),
                "handover_result": result.get("handover_result"),
                "handover_pending": result.get("handover_pending"),
                "customer_contact": result.get("customer_contact"),
                "event_type": result.get("event_type"),
                "product_type": result.get("product_type"),
                "color": result.get("color"),
                "size": result.get("size"),
                "budget": result.get("budget"),
                "wedding_date": result.get("wedding_date"),
                "quantity": result.get("quantity"),
                "height": result.get("height"),
                "chest": result.get("chest"),
                "waist": result.get("waist"),
                "sales_stage": result.get("sales_stage"),
                "discovery_next_slot": result.get("discovery_next_slot"),
                "buying_intent": result.get("buying_intent"),
                "negotiation_state": result.get("negotiation_state"),
                "selected_fabric_catalog_code": result.get("selected_fabric_catalog_code"),
                "fabrics": result.get("fabrics"),
                "price_quote": result.get("price_quote"),
                "customize_pricing_mode": result.get("customize_pricing_mode"),
                "catalog_categories": result.get("catalog_categories"),
                "scoring_reasons": result.get("scoring_reasons"),
                "shown_product_ids": result.get("shown_product_ids") or session_context.get("shown_product_ids") or [],
                "checkout_url": result.get("checkout_url"),
                "checkout_session_id": result.get("checkout_session_id"),
            },
        )

        if result.get("customer_contact"):
            await chat_store.update_session_metadata(
                request.session_id,
                {"customer_contact": result.get("customer_contact")},
            )

        updated_context = chat_store.build_session_context_from_result(result, session_context)
        await chat_store.save_session_context(request.session_id, updated_context)

        # Persist customer profile and negotiation state via memory_service
        profile_data = result.get("customer_profile") or profile.model_dump()
        # Merge any top-level extracted fields into profile_data
        for field, key in [
            ("event_type", "event_type"),
            ("budget", "budget"),
            ("wedding_date", "wedding_date"),
            ("size", "jacket_size"),
            ("selected_variation_id", "selected_variation_id"),
            ("selected_variation_name", "selected_variation_name"),
            ("selected_product_variation_id", "selected_product_variation_id"),
            ("selected_product_variation_name", "selected_product_variation_name"),
            ("product_type", "product_type"),
            ("selected_product_id", "selected_product_id"),
        ]:
            if result.get(field) and not profile_data.get(key):
                profile_data[key] = result.get(field)
        if result.get("sales_stage"):
            profile_data["sales_stage"] = result["sales_stage"]

        updated_profile = CustomerProfileSchema(**profile_data)
        updated_negotiation = NegotiationStateSchema(**(result.get("negotiation_state") or negotiation_state.model_dump()))
        await memory_service.save_session_context(request.session_id, updated_profile, updated_negotiation)

        logger.info(
            "Assistant response saved session_id=%s reply_length=%s imageurl_present=%s",
            request.session_id,
            safe_len(reply),
            bool(imageurl),
        )

        suppress_ui = bool(result.get("suppress_product_ui")) or (
            result.get("sales_stage") in ("detail", "closing")
            and "search_products" not in executed_nodes
            and "get_product_details" not in executed_nodes
            and bool(result.get("selected_product_id"))
            and result.get("intent") not in ("product_search", "accessories_search")
        )
        hide_catalog = bool(
            result.get("custom_design_result")
            or result.get("custom_image_url")
            or result.get("sales_stage") == "customization"
            or suppress_ui
        )
        state_payload = {
            "intent": result.get("intent"),
            "sales_stage": result.get("sales_stage"),
            "discovery_next_slot": result.get("discovery_next_slot"),
            "required_steps": result.get("required_steps"),
            "executed_nodes": executed_nodes,
            "buying_intent": result.get("buying_intent"),
            "selected_product_id": result.get("selected_product_id"),
            "products": [] if hide_catalog else result.get("products", []),
            "product_details": None if hide_catalog else result.get("product_details"),
            "suppress_product_ui": hide_catalog,
            "checkout_hand_off_note": result.get("checkout_hand_off_note"),
            "checkout_url": result.get("checkout_url") or session_context.get("checkout_url"),
            "checkout_session_id": result.get("checkout_session_id") or session_context.get("checkout_session_id"),
            "product_interest_note": result.get("product_interest_note"),
            "inventory_result": result.get("inventory_result"),
            "negotiation_result": result.get("negotiation_result"),
            "measurement_result": result.get("measurement_result"),
            "handover_result": result.get("handover_result"),
            "handover_pending": result.get("handover_pending"),
            "customer_contact": result.get("customer_contact"),
            "custom_image_url": result.get("custom_image_url"),
            "custom_design_result": result.get("custom_design_result"),
            "event_type": result.get("event_type"),
            "product_type": result.get("product_type"),
            "selected_variation_id": result.get("selected_variation_id"),
            "selected_variation_name": result.get("selected_variation_name"),
            "selected_product_variation_id": result.get("selected_product_variation_id"),
            "selected_product_variation_name": result.get("selected_product_variation_name"),
            "product_variations": result.get("product_variations") or [],
            "product_variation_note": result.get("product_variation_note"),
            "color": result.get("color"),
            "wedding_date": result.get("wedding_date"),
            "catalog_categories": result.get("catalog_categories") or [],
            "catalog_variations": result.get("catalog_variations") or [],
            "category_variations": result.get("category_variations") or [],
            "catalog_search_note": result.get("catalog_search_note"),
            "shown_product_ids": result.get("shown_product_ids") or session_context.get("shown_product_ids") or [],
            "fabrics": result.get("fabrics") or [],
            "selected_fabric_catalog_code": result.get("selected_fabric_catalog_code"),
            "customization_stage": result.get("customization_stage"),
            "cut_style": result.get("cut_style"),
        }
        if settings.INCLUDE_COST_IN_RESPONSE:
            state_payload["cost"] = get_session_cost(request.session_id)
        return ChatResponse(
            reply=reply,
            imageurl=imageurl,
            executed_nodes=executed_nodes,
            state=state_payload,
        )
    except BackendAPIError as exc:
        logger.exception(
            "Chat backend unavailable session_id=%s status=%s path=%s",
            request.session_id,
            exc.status_code,
            exc.path,
        )
        # Degrade gracefully — never bubble raw 500 from upstream rate limits.
        soft = (
            "I apologise — our catalogue is momentarily busy. "
            "Please try again in a moment, or share your WhatsApp so a Style Consultant can assist you directly."
        )
        await chat_store.add_message(
            session_id=request.session_id,
            role="assistant",
            content=soft,
            metadata={"backend_error": True, "status_code": exc.status_code},
        )
        return ChatResponse(reply=soft, imageurl=None, executed_nodes=[], state={"backend_error": True})
    except Exception:
        logger.exception("Chat request failed session_id=%s", request.session_id)
        soft = (
            "I apologise — something went wrong on my side. "
            "Please try again shortly, or leave your WhatsApp for a Style Consultant."
        )
        try:
            await chat_store.add_message(
                session_id=request.session_id,
                role="assistant",
                content=soft,
                metadata={"internal_error": True},
            )
        except Exception:
            logger.exception("Failed to persist soft-error reply session_id=%s", request.session_id)
        return ChatResponse(reply=soft, imageurl=None, executed_nodes=[], state={"internal_error": True})


@app.get("/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 50):
    messages = await chat_store.get_messages(session_id=session_id, limit=limit)
    logger.info(
        "Session messages fetched session_id=%s limit=%s message_count=%s",
        session_id,
        limit,
        len(messages),
    )
    return {"session_id": session_id, "messages": messages}


@app.get("/sessions/{session_id}/cost")
async def get_session_ai_cost(session_id: str):
    """In-memory estimated AI cost for a session (LLM tokens + fal image/try-on)."""
    cost = get_session_cost(session_id)
    return {
        "session_id": session_id,
        "cost": cost,
        "langsmith_tracing": bool(settings.LANGSMITH_TRACING),
        "langsmith_project": settings.LANGSMITH_PROJECT if settings.LANGSMITH_TRACING else None,
        "note": (
            "Memory-only estimates for this process. "
            "Filter LangSmith by metadata.session_id for durable analytics."
        ),
    }


@app.get("/sessions/{session_id}/quota")
async def get_session_quota(session_id: str):
    """Daily limits (hardcoded or RA) + today's usage from session history."""
    status = await get_quota_status(session_id)
    return status


@app.get("/api/sessions")
async def list_sessions(limit: int = 50):
    """List recent sessions for workbench switcher."""
    sessions = await chat_store.list_recent_sessions(limit=limit)
    return {"sessions": sessions}


@app.post("/api/sessions/{session_id}/clear")
async def clear_session_endpoint(session_id: str):
    """Clear a single session from Redis cache, memory fallback, and PostgreSQL."""
    await memory_service.clear_session(session_id)
    await chat_store.clear_session(session_id)
    clear_session_cost(session_id)
    logger.info("Session cleared session_id=%s", session_id)
    return {
        "status": "ok",
        "message": f"Session '{session_id}' successfully cleared from Redis and PostgreSQL.",
        "session_id": session_id,
    }


@app.post("/api/sessions/clear-all")
async def clear_all_sessions_endpoint():
    """Clear all sessions from Redis, in-memory cache, and PostgreSQL."""
    mem_result = await memory_service.clear_all_sessions()
    chat_result = await chat_store.clear_all_sessions()
    logger.info("All sessions cleared: %s, %s", mem_result, chat_result)
    return {
        "status": "ok",
        "message": "All sessions successfully cleared from Redis and PostgreSQL.",
        "details": {**mem_result, **chat_result},
    }


@app.get("/debug/image-generations")
async def debug_image_generations(limit: int = 20):
    """Debug endpoint to inspect prompts, inputs, and URLs of generated wedding images."""
    records = await image_store.list_recent_records(limit=limit)
    return {"count": len(records), "records": records}


@app.get("/test", response_class=HTMLResponse)
async def test_workbench():
    """Serve the interactive HTML testing workbench."""
    index_path = Path("index.html")
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>Tester UI index.html not found</h1>", status_code=404)