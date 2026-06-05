from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from app.core.config import settings
from app.db.master import get_master_db
from app.services.tenant_service import check_and_update_tenant_status

logger = logging.getLogger("hospital.suspension")


async def run_suspension_check() -> int:
    checked = 0
    suspended = 0
    db = get_master_db()
    try:
        from sqlalchemy import text
        rows = db.execute(
            text(
                "SELECT tenant_id FROM tenants "
                "WHERE is_active = true AND status != 'suspended'"
            )
        ).fetchall()
        for row in rows:
            checked += 1
            result = await check_and_update_tenant_status(db, row[0])
            if result == "suspended":
                suspended += 1
        db.commit()
    finally:
        db.close()

    if suspended:
        logger.warning("Suspension check: %d tenants suspended out of %d checked", suspended, checked)
    else:
        logger.info("Suspension check: %d tenants checked, none suspended", checked)
    return suspended


async def suspension_loop() -> None:
    while True:
        try:
            await run_suspension_check()
        except Exception as e:
            logger.error("Suspension check failed: %s", e)
        await asyncio.sleep(settings.suspension_check_interval)
