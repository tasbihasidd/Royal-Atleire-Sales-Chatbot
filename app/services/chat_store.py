from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, delete, func

from app.core.logging_config import safe_len
from app.services.db import AsyncSessionLocal, ChatMessage, ChatSession

logger = logging.getLogger(__name__)

# In-memory fallback when Postgres is unavailable (hard-test / degraded env).
_MEM_SESSIONS: dict[str, dict[str, Any]] = {}
_MEM_MESSAGES: dict[str, list[dict[str, Any]]] = {}


SESSION_CONTEXT_KEYS = (
    "selected_product_id",
    "products",
    "product_details",
    "inventory_result",
    "handover_pending",
    "event_type",
    "product_type",
    "selected_variation_id",
    "selected_variation_name",
    "selected_product_variation_id",
    "selected_product_variation_name",
    "product_variations",
    "color",
    "size",
    "budget",
    "wedding_date",
    "quantity",
    "height",
    "chest",
    "waist",
    "shoulder",
    "sleeve",
    "jacket_length",
    "size_chart",
    "body_measurements",
    "measurement_path",
    "measurement_prompt",
    "measurement_result",
    "customization_stage",
    "cut_style",
    "sales_stage",
    "buying_intent",
    "negotiation_state",
    "selected_fabric_catalog_code",
    "fabrics",
    "declined_slots",
    "catalog_variations",
    "category_variations",
    "shown_product_ids",
    "custom_image_url",
    "custom_design_result",
    "custom_instructions",
    "price_quote",
    "customize_pricing_mode",
    "market_currency",
    "checkout_url",
    "checkout_session_id",
)


def _merge_contact_dict(existing: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key, value in patch.items():
        if value:
            merged[key] = value
    if merged.get("phone") and not merged.get("whatsapp"):
        merged["whatsapp"] = merged["phone"]
    elif merged.get("whatsapp") and not merged.get("phone"):
        merged["phone"] = merged["whatsapp"]
    return merged


def _patch_session_context(existing: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    merged = dict(existing)
    for key in SESSION_CONTEXT_KEYS:
        value = metadata.get(key)
        if key == "products":
            # Never wipe last recommended products with an empty list — detail
            # matching and "light colors" re-asks need the prior catalogue rows.
            if isinstance(value, list) and value:
                merged[key] = value
                cur_shown = set(merged.get("shown_product_ids") or [])
                for p in value:
                    if isinstance(p, dict):
                        pid = str(p.get("product_id") or p.get("id") or "")
                        if pid:
                            cur_shown.add(pid)
                merged["shown_product_ids"] = list(cur_shown)
            elif value is not None and key not in merged:
                merged[key] = value
        elif key == "shown_product_ids":
            cur_shown = set(merged.get("shown_product_ids") or [])
            if isinstance(value, list):
                for item in value:
                    if item:
                        cur_shown.add(str(item))
            merged["shown_product_ids"] = list(cur_shown)
        elif key == "product_details":
            if isinstance(value, dict) and value and not value.get("error"):
                merged[key] = value
        elif key == "inventory_result":
            if value is not None:
                merged[key] = value
        elif key == "handover_pending":
            if value is not None:
                merged[key] = bool(value)
        elif key in ("budget", "quantity"):
            if value is not None:
                merged[key] = value
        elif key in ("negotiation_state",):
            if value is not None:
                merged[key] = value
        elif value:
            merged[key] = value

    handover = metadata.get("handover_result")
    if isinstance(handover, dict):
        if handover.get("handover_created"):
            merged["handover_pending"] = False
        elif handover.get("status") == "pending_contact":
            merged["handover_pending"] = True

    return merged


class ChatStore:
    async def ensure_session(self, session_id: str, user_id: str | None = None) -> None:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
                session = result.scalar_one_or_none()

                if session is None:
                    db.add(ChatSession(session_id=session_id, user_id=user_id))
                    await db.commit()
                    logger.info("Chat session created session_id=%s", session_id)
                else:
                    logger.info("Chat session exists session_id=%s", session_id)
                return
        except Exception as e:
            logger.warning("ensure_session DB failed session_id=%s — memory: %s", session_id, e)
            _MEM_SESSIONS.setdefault(session_id, {"user_id": user_id, "metadata_json": {}})
            _MEM_MESSAGES.setdefault(session_id, [])

    async def get_session_metadata(self, session_id: str) -> dict[str, Any]:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
                session = result.scalar_one_or_none()
                if session is None:
                    return dict((_MEM_SESSIONS.get(session_id) or {}).get("metadata_json") or {})
                return dict(session.metadata_json or {})
        except Exception as e:
            logger.warning("get_session_metadata DB failed session_id=%s: %s", session_id, e)
            return dict((_MEM_SESSIONS.get(session_id) or {}).get("metadata_json") or {})

    async def update_session_metadata(self, session_id: str, patch: dict[str, Any]) -> None:
        await self.ensure_session(session_id)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
                session = result.scalar_one_or_none()
                if session is None:
                    raise RuntimeError("session missing after ensure")
                current = dict(session.metadata_json or {})
                current.update(patch)
                session.metadata_json = current
                await db.commit()
                logger.info(
                    "Chat session metadata updated session_id=%s keys=%s",
                    session_id,
                    list(patch.keys()),
                )
                return
        except Exception as e:
            logger.warning("update_session_metadata DB failed session_id=%s: %s", session_id, e)
            slot = _MEM_SESSIONS.setdefault(session_id, {"metadata_json": {}})
            current = dict(slot.get("metadata_json") or {})
            current.update(patch)
            slot["metadata_json"] = current

    async def load_customer_contact(self, session_id: str, history: list[dict[str, Any]]) -> dict[str, Any]:
        session_meta = await self.get_session_metadata(session_id)
        contact = dict(session_meta.get("customer_contact") or {})

        for message in history:
            if message.get("role") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            message_contact = metadata.get("customer_contact")
            if isinstance(message_contact, dict):
                contact = _merge_contact_dict(contact, message_contact)

        logger.info(
            "Customer contact loaded session_id=%s name_present=%s phone_present=%s",
            session_id,
            bool(contact.get("name")),
            bool(contact.get("phone") or contact.get("whatsapp")),
        )
        return contact

    async def load_session_context(
        self,
        session_id: str,
        history: list[dict[str, Any]],
    ) -> dict[str, Any]:
        session_meta = dict(await self.get_session_metadata(session_id))
        stored = dict(session_meta.get("session_context") or {})
        context = _patch_session_context({}, stored)

        for message in history:
            if message.get("role") != "assistant":
                continue
            metadata = message.get("metadata") or {}
            context = _patch_session_context(context, metadata)

        logger.info(
            "Session context loaded session_id=%s selected_product_id=%s product_count=%s handover_pending=%s",
            session_id,
            context.get("selected_product_id"),
            len(context.get("products") or []),
            context.get("handover_pending", False),
        )
        return context

    async def save_session_context(self, session_id: str, context: dict[str, Any]) -> None:
        patch = {
            "session_context": {
                key: context[key]
                for key in SESSION_CONTEXT_KEYS
                if key in context
            }
        }
        await self.update_session_metadata(session_id, patch)

    def build_session_context_from_result(
        self,
        result: dict[str, Any],
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        context = dict(previous)

        if result.get("selected_product_id"):
            context["selected_product_id"] = result["selected_product_id"]
        # Persist non-empty product lists only — empty search turns must not
        # erase the last carousel so name→id matching still works.
        if isinstance(result.get("products"), list) and result["products"]:
            context["products"] = result["products"]
        product_details = result.get("product_details")
        if isinstance(product_details, dict) and product_details and not product_details.get("error"):
            context["product_details"] = product_details
        elif isinstance(product_details, dict) and product_details.get("error"):
            # Do not stash error stubs as product_details
            pass
        if result.get("inventory_result") is not None:
            context["inventory_result"] = result["inventory_result"]

        for key in (
            "event_type",
            "product_type",
            "color",
            "size",
            "budget",
            "wedding_date",
            "quantity",
            "height",
            "chest",
            "waist",
            "sales_stage",
            "buying_intent",
            "selected_fabric_catalog_code",
            "customization_stage",
            "cut_style",
            "selected_variation_id",
            "selected_variation_name",
            "selected_product_variation_id",
            "selected_product_variation_name",
            "market_currency",
            "checkout_url",
            "checkout_session_id",
        ):
            if result.get(key) is not None and result.get(key) != "":
                context[key] = result[key]

        if result.get("product_variations") is not None:
            context["product_variations"] = result["product_variations"]
        if result.get("negotiation_state") is not None:
            context["negotiation_state"] = result["negotiation_state"]
        if result.get("fabrics"):
            context["fabrics"] = result["fabrics"]
        if result.get("custom_image_url"):
            context["custom_image_url"] = result["custom_image_url"]
        if result.get("custom_design_result"):
            context["custom_design_result"] = result["custom_design_result"]
        if result.get("custom_instructions"):
            context["custom_instructions"] = result["custom_instructions"]
        if isinstance(result.get("price_quote"), dict):
            context["price_quote"] = result["price_quote"]
        if result.get("customize_pricing_mode") is not None:
            context["customize_pricing_mode"] = result["customize_pricing_mode"]
        profile = result.get("customer_profile") or {}
        if isinstance(profile, dict) and profile.get("declined_slots") is not None:
            context["declined_slots"] = profile.get("declined_slots")

        handover = result.get("handover_result")
        if isinstance(handover, dict):
            if handover.get("handover_created"):
                context["handover_pending"] = False
            elif handover.get("status") == "pending_contact":
                context["handover_pending"] = True
        elif "handover_pending" in result:
            context["handover_pending"] = bool(result["handover_pending"])

        # Accumulate shown product IDs to prevent duplicates on "show more"
        shown = set(context.get("shown_product_ids") or [])
        if result.get("shown_product_ids"):
            for pid in result["shown_product_ids"]:
                if pid:
                    shown.add(str(pid))
        if result.get("products"):
            for p in result["products"]:
                pid = str(p.get("id") or p.get("product_id") or "")
                if pid:
                    shown.add(pid)
        context["shown_product_ids"] = list(shown)

        return context

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        await self.ensure_session(session_id)

        try:
            async with AsyncSessionLocal() as db:
                db.add(ChatMessage(
                    session_id=session_id,
                    role=role,
                    content=content,
                    metadata_json=metadata or {},
                ))
                await db.commit()
                logger.info(
                    "Chat message saved session_id=%s role=%s content_length=%s",
                    session_id,
                    role,
                    safe_len(content),
                )
                return
        except Exception as e:
            logger.warning("add_message DB failed session_id=%s — memory: %s", session_id, e)
            _MEM_MESSAGES.setdefault(session_id, []).append(
                {
                    "role": role,
                    "content": content,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "metadata": metadata or {},
                }
            )

    async def get_messages(self, session_id: str, limit: int = 20) -> list[dict]:
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc())
                    .limit(limit)
                )
                rows = list(result.scalars().all())
                rows.reverse()

                messages = [
                    {
                        "role": row.role,
                        "content": row.content,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                        "metadata": row.metadata_json or {},
                    }
                    for row in rows
                ]
                logger.info(
                    "Chat messages fetched session_id=%s limit=%s message_count=%s",
                    session_id,
                    limit,
                    len(messages),
                )
                return messages
        except Exception as e:
            logger.warning("get_messages DB failed session_id=%s — memory: %s", session_id, e)
            msgs = list(_MEM_MESSAGES.get(session_id) or [])
            return msgs[-limit:]

    async def count_user_messages_between(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
    ) -> int:
        """Count role=user messages in [start, end) for daily quota (Option A)."""
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(func.count())
                    .select_from(ChatMessage)
                    .where(
                        ChatMessage.session_id == session_id,
                        ChatMessage.role == "user",
                        ChatMessage.created_at >= start,
                        ChatMessage.created_at < end,
                    )
                )
                return int(result.scalar_one() or 0)
        except Exception as e:
            logger.warning(
                "count_user_messages_between DB failed session_id=%s — memory: %s",
                session_id,
                e,
            )
            count = 0
            for msg in _MEM_MESSAGES.get(session_id) or []:
                if msg.get("role") != "user":
                    continue
                raw = msg.get("created_at")
                if not raw:
                    count += 1
                    continue
                try:
                    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if start <= ts < end:
                        count += 1
                except ValueError:
                    count += 1
            return count

    async def clear_session(self, session_id: str) -> None:
        """Delete all chat messages and chat session record for session_id."""
        async with AsyncSessionLocal() as db:
            await db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
            await db.execute(delete(ChatSession).where(ChatSession.session_id == session_id))
            await db.commit()
            logger.info("Cleared chat history and session record session_id=%s", session_id)

    async def clear_all_sessions(self) -> dict[str, int]:
        """Delete all chat messages and chat session records from DB."""
        async with AsyncSessionLocal() as db:
            res_msg = await db.execute(delete(ChatMessage))
            res_sess = await db.execute(delete(ChatSession))
            await db.commit()
            logger.info("Cleared all chat messages and sessions from DB")
            return {
                "messages_deleted": res_msg.rowcount or 0,
                "sessions_deleted": res_sess.rowcount or 0,
            }

    async def list_recent_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List active/recent sessions with timestamp and message count."""
        async with AsyncSessionLocal() as db:
            query = (
                select(
                    ChatSession.session_id,
                    ChatSession.created_at,
                    ChatSession.updated_at,
                    func.count(ChatMessage.id).label("message_count"),
                )
                .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.session_id)
                .group_by(ChatSession.id, ChatSession.session_id, ChatSession.created_at, ChatSession.updated_at)
                .order_by(ChatSession.updated_at.desc())
                .limit(limit)
            )
            result = await db.execute(query)
            rows = result.all()
            return [
                {
                    "session_id": row.session_id,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "message_count": int(row.message_count or 0),
                }
                for row in rows
            ]


chat_store = ChatStore()
