from __future__ import annotations

import logging

from app.services.db import AsyncSessionLocal, GeneratedWeddingImage

logger = logging.getLogger(__name__)


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

    async def list_recent_records(self, limit: int = 20) -> list[dict]:
        from sqlalchemy import select
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
