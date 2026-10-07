from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select

from app.services.db import AsyncSessionLocal, GeneratedWeddingImage

logger = logging.getLogger(__name__)

QUOTA_WAIVED_KEY = "_quota_waived"


class ImageStore:
    async def save_generated_image_record(
        self,
        image_url: str,
        image_filename: str,
        prompt: str,
        fabric_analysis: dict,
        preferences: dict,
        session_id: str | None = None,
        user_id: str | None = None,
        metadata: dict | None = None,
    ) -> int:
        logger.info(
            "Generated image record save start filename=%s session_id=%s",
            image_filename,
            session_id,
        )
        async with AsyncSessionLocal() as db:
            record = GeneratedWeddingImage(
                session_id=session_id,
                user_id=user_id,
                image_url=image_url,
                image_filename=image_filename,
                prompt=prompt,
                fabric_analysis=fabric_analysis,
                preferences=preferences,
                metadata_json=metadata or {},
            )

            db.add(record)
            await db.commit()
            await db.refresh(record)

            logger.info(
                "Generated image record saved record_id=%s filename=%s",
                record.id,
                image_filename,
            )
            return record.id

    def _is_quota_waived(self, preferences: dict | None) -> bool:
        if not isinstance(preferences, dict):
            return False
        return bool(preferences.get(QUOTA_WAIVED_KEY))

    async def count_images_between(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
    ) -> int:
        """Count custom/wedding image records for session in [start, end).

        Rows marked preferences._quota_waived=true are excluded (admin reset).
        """
        if not session_id:
            return 0
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(GeneratedWeddingImage.preferences).where(
                        GeneratedWeddingImage.session_id == session_id,
                        GeneratedWeddingImage.created_at >= start,
                        GeneratedWeddingImage.created_at < end,
                    )
                )
                rows = result.scalars().all()
                return sum(1 for prefs in rows if not self._is_quota_waived(prefs))
        except Exception as e:
            logger.warning(
                "count_images_between failed session_id=%s: %s",
                session_id,
                e,
            )
            return 0

    async def waive_images_between(
        self,
        session_id: str,
        start: datetime,
        end: datetime,
    ) -> int:
        """Mark today's (or range) image rows as quota-waived. Returns rows updated."""
        if not session_id:
            return 0
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GeneratedWeddingImage).where(
                    GeneratedWeddingImage.session_id == session_id,
                    GeneratedWeddingImage.created_at >= start,
                    GeneratedWeddingImage.created_at < end,
                )
            )
            rows = list(result.scalars().all())
            updated = 0
            for row in rows:
                prefs = dict(row.preferences or {})
                if prefs.get(QUOTA_WAIVED_KEY):
                    continue
                prefs[QUOTA_WAIVED_KEY] = True
                row.preferences = prefs
                updated += 1
            if updated:
                await db.commit()
            logger.info(
                "waive_images_between session_id=%s updated=%s scanned=%s",
                session_id,
                updated,
                len(rows),
            )
            return updated

    async def list_recent_records(self, limit: int = 20) -> list[dict]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(GeneratedWeddingImage)
                .order_by(GeneratedWeddingImage.created_at.desc())
                .limit(limit)
            )
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "session_id": r.session_id,
                    "image_url": r.image_url,
                    "image_filename": r.image_filename,
                    "prompt": r.prompt,
                    "fabric_analysis": r.fabric_analysis,
                    "preferences": r.preferences,
                    "metadata_json": r.metadata_json,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]


image_store = ImageStore()
