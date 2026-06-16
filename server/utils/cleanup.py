"""
utils/cleanup.py — Background task: delete alerts older than retention period.
Runs once on startup, then every 24 hours.
"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import AsyncSessionLocal
from models.alert import Alert

logger = logging.getLogger(__name__)


async def cleanup_old_alerts():
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(days=settings.alert_retention_days)
            logger.info(f"Running cleanup: deleting alerts older than {cutoff.date()}")

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(Alert.id, Alert.snapshot_path, Alert.clip_path)
                    .where(Alert.timestamp < cutoff)
                )
                old_alerts = result.all()

                if not old_alerts:
                    logger.info("Cleanup: nothing to delete")
                else:
                    deleted_files = 0
                    for _, snapshot_path, clip_path in old_alerts:
                        for rel_path in [snapshot_path, clip_path]:
                            if rel_path:
                                abs_path = Path(settings.storage_root) / rel_path
                                if abs_path.exists():
                                    abs_path.unlink()
                                    deleted_files += 1

                    await db.execute(delete(Alert).where(Alert.timestamp < cutoff))
                    await db.commit()

                    logger.info(
                        f"Cleanup: deleted {len(old_alerts)} alerts, "
                        f"{deleted_files} files"
                    )

        except Exception as e:
            logger.exception(f"Cleanup task error: {e}")

        await asyncio.sleep(24 * 60 * 60)