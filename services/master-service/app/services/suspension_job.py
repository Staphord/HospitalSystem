"""Background job that expires subscriptions and auto-suspends tenants.

Runs on the interval configured by SUSPENSION_CHECK_INTERVAL (default 24h).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db.master import get_master_db
from app.services.subscription_plans import SubscriptionStatus
from app.services.tenant_service import (
    cache_tenant_suspension,
    check_and_update_tenant_status,
)

logger = logging.getLogger("hospital.suspension")


async def run_suspension_check() -> int:
    checked = 0
    suspended = 0
    db = get_master_db()
    try:
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


async def run_trial_expiry_check() -> int:
    """Move tenants whose trial has ended into past_due/suspended unless renewed."""
    from app.services.subscription_service import _ensure_aware

    expired = 0
    db = get_master_db()
    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(
            text(
                "SELECT id, tenant_id, trial_end, grace_period_end "
                "FROM tenants "
                "WHERE subscription_status = :trial"
            ),
            {"trial": SubscriptionStatus.TRIAL.value},
        ).fetchall()

        for pk_id, tenant_id, trial_end, grace_end in rows:
            trial_end = _ensure_aware(trial_end)
            grace_end = _ensure_aware(grace_end)
            if trial_end and now > trial_end:
                if grace_end and now <= grace_end:
                    db.execute(
                        text(
                            "UPDATE tenants SET subscription_status = :past_due, status = 'active' "
                            "WHERE id = :id"
                        ),
                        {"past_due": SubscriptionStatus.PAST_DUE.value, "id": pk_id},
                    )
                    logger.info("Tenant %s trial ended, moved to past_due", tenant_id)
                else:
                    db.execute(
                        text(
                            "UPDATE tenants SET subscription_status = :suspended, status = 'suspended', "
                            "is_active = false, suspended_at = :now, suspended_reason = :reason "
                            "WHERE id = :id"
                        ),
                        {
                            "suspended": SubscriptionStatus.SUSPENDED.value,
                            "id": pk_id,
                            "now": now,
                            "reason": "Free trial expired",
                        },
                    )
                    await cache_tenant_suspension(tenant_id)
                    expired += 1
                    logger.warning("Tenant %s trial expired beyond grace period, suspended", tenant_id)

        db.commit()
    finally:
        db.close()

    return expired


async def suspension_loop() -> None:
    while True:
        try:
            await run_suspension_check()
            await run_trial_expiry_check()
        except Exception as e:
            logger.error("Suspension check failed: %s", e)
        await asyncio.sleep(settings.suspension_check_interval)
