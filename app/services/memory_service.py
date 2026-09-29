from __future__ import annotations
import json
import logging
from typing import Any
from sqlalchemy import select, delete

from app.config import settings
from app.schemas.profile import CustomerProfileSchema
from app.schemas.negotiation import NegotiationStateSchema
from app.services.db import AsyncSessionLocal, UserProfile

logger = logging.getLogger(__name__)

# In-memory LRU fallback cache if Redis is not connected
_LOCAL_CACHE: dict[str, dict[str, Any]] = {}


class MemoryService:
    def __init__(self) -> None:
        self.redis_client = None
        self._init_redis()

    def _init_redis(self) -> None:
        redis_url = getattr(settings, "REDIS_URL", None) or "redis://localhost:6379/0"
        try:
            import redis.asyncio as aioredis
            self.redis_client = aioredis.from_url(redis_url, decode_responses=True, socket_connect_timeout=1)
            logger.info("Redis client configured url=%s", redis_url)
        except Exception as e:
            logger.warning("Redis client disabled, falling back to PostgreSQL + local cache: %s", e)
            self.redis_client = None

    async def load_session_context(self, session_id: str) -> tuple[CustomerProfileSchema, NegotiationStateSchema]:
        """
        Load Customer Profile and Negotiation State from Redis -> Postgres -> New Defaults.
        Matches exact implementation plan signature.
        """
        cache_key = f"session_profile:{session_id}"

        # 1. Try Redis Cache
        if self.redis_client:
            try:
                cached_data = await self.redis_client.get(cache_key)
                if cached_data:
                    payload = json.loads(cached_data)
                    profile = CustomerProfileSchema(**payload.get("profile", {}))
                    negotiation = NegotiationStateSchema(**payload.get("negotiation", {}))
                    logger.debug("Memory cache HIT (Redis) session_id=%s", session_id)
                    return profile, negotiation
            except Exception as e:
                logger.warning("Redis read error session_id=%s: %s", session_id, e)

        # 2. Try In-Memory Fallback Cache
        if session_id in _LOCAL_CACHE:
            payload = _LOCAL_CACHE[session_id]
            profile = CustomerProfileSchema(**payload.get("profile", {}))
            negotiation = NegotiationStateSchema(**payload.get("negotiation", {}))
            logger.debug("Memory cache HIT (Local) session_id=%s", session_id)
            return profile, negotiation

        # 3. Try PostgreSQL DB (soft-fail → fresh profile)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(UserProfile).where(UserProfile.session_id == session_id))
                user_prof = result.scalar_one_or_none()

                if user_prof and user_prof.profile_json:
                    profile_dict = dict(user_prof.profile_json)
                    profile_dict["session_id"] = session_id
                    profile = CustomerProfileSchema(**profile_dict)
                    negotiation = NegotiationStateSchema(**(user_prof.negotiation_json or {}))
                    logger.info("Memory cache MISS -> Loaded from Postgres session_id=%s", session_id)
                else:
                    profile = CustomerProfileSchema(session_id=session_id)
                    negotiation = NegotiationStateSchema()
                    logger.info("Memory cache MISS -> Created fresh profile session_id=%s", session_id)

                await self._write_to_caches(session_id, profile, negotiation)
                return profile, negotiation
        except Exception as e:
            logger.warning(
                "Postgres load failed session_id=%s — using in-memory profile: %s",
                session_id,
                e,
            )
            profile = CustomerProfileSchema(session_id=session_id)
            negotiation = NegotiationStateSchema()
            await self._write_to_caches(session_id, profile, negotiation)
            return profile, negotiation

    async def save_session_context(
        self,
        session_id: str,
        profile: CustomerProfileSchema,
        negotiation: NegotiationStateSchema,
    ) -> None:
        """
        Persist profile state atomically to PostgreSQL and refresh Redis cache.
        Matches exact implementation plan signature.
        """
        profile_dict = profile.model_dump()
        negotiation_dict = negotiation.model_dump()

        # Update local and Redis cache
        await self._write_to_caches(session_id, profile, negotiation)

        # Update PostgreSQL database (soft-fail — cache already written)
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(select(UserProfile).where(UserProfile.session_id == session_id))
                user_prof = result.scalar_one_or_none()

                if user_prof is None:
                    user_prof = UserProfile(
                        session_id=session_id,
                        profile_json=profile_dict,
                        negotiation_json=negotiation_dict,
                        sales_stage=profile.sales_stage,
                        buying_intent=profile.buying_intent,
                    )
                    db.add(user_prof)
                else:
                    user_prof.profile_json = profile_dict
                    user_prof.negotiation_json = negotiation_dict
                    user_prof.sales_stage = profile.sales_stage
                    user_prof.buying_intent = profile.buying_intent

                await db.commit()
                logger.info("Persisted CustomerProfile to DB session_id=%s stage=%s", session_id, profile.sales_stage)
        except Exception as e:
            logger.warning("Postgres save failed session_id=%s — cache-only: %s", session_id, e)

    async def warm_cache(self, session_id: str) -> None:
        """Pre-warm Redis and in-memory cache for an active session."""
        profile, negotiation = await self.load_session_context(session_id)
        await self._write_to_caches(session_id, profile, negotiation)

    async def clear_session(self, session_id: str) -> None:
        """Clear memory cache, Redis cache, and DB profile for session_id."""
        _LOCAL_CACHE.pop(session_id, None)
        if self.redis_client:
            try:
                await self.redis_client.delete(f"session_profile:{session_id}")
                logger.info("Cleared Redis profile cache session_id=%s", session_id)
            except Exception as e:
                logger.warning("Redis delete error session_id=%s: %s", session_id, e)

        try:
            async with AsyncSessionLocal() as db:
                await db.execute(delete(UserProfile).where(UserProfile.session_id == session_id))
                await db.commit()
                logger.info("Cleared UserProfile from DB session_id=%s", session_id)
        except Exception as e:
            logger.warning("Postgres clear_session failed session_id=%s: %s", session_id, e)

    async def clear_all_sessions(self) -> dict[str, Any]:
        """Clear all session profiles in memory cache, Redis, and DB."""
        _LOCAL_CACHE.clear()
        redis_cleared_count = 0
        if self.redis_client:
            try:
                keys = []
                async for key in self.redis_client.scan_iter("session_profile:*"):
                    keys.append(key)
                if keys:
                    redis_cleared_count = await self.redis_client.delete(*keys)
                logger.info("Cleared all Redis session profiles count=%d", len(keys))
            except Exception as e:
                logger.warning("Redis clear_all error: %s", e)

        async with AsyncSessionLocal() as db:
            result = await db.execute(delete(UserProfile))
            db_deleted_count = result.rowcount or 0
            await db.commit()
            logger.info("Cleared all UserProfile rows count=%s", db_deleted_count)

        return {"redis_keys_deleted": redis_cleared_count, "db_profiles_deleted": db_deleted_count}

    # Aliases for backward compatibility
    get_profile = load_session_context
    save_profile = save_session_context

    async def _write_to_caches(
        self,
        session_id: str,
        profile: CustomerProfileSchema,
        negotiation: NegotiationStateSchema,
    ) -> None:
        payload = {
            "profile": profile.model_dump(),
            "negotiation": negotiation.model_dump(),
        }
        _LOCAL_CACHE[session_id] = payload

        if self.redis_client:
            try:
                cache_key = f"session_profile:{session_id}"
                await self.redis_client.setex(cache_key, 86400, json.dumps(payload))  # 24 hour TTL
            except Exception as e:
                logger.warning("Redis write error session_id=%s: %s", session_id, e)


memory_service = MemoryService()
