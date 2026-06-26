from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
import redis.asyncio as aioredis
from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings

_redis: Optional[aioredis.Redis] = None
_cipher: Optional[Fernet] = None


def _get_cipher() -> Fernet:
    global _cipher
    if _cipher is None:
        _cipher = Fernet(settings.tenant_db_encryption_key.encode())
    return _cipher


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
        )
    return _redis


def encrypt_dsn(dsn: str) -> str:
    return _get_cipher().encrypt(dsn.encode()).decode()


def decrypt_dsn(encrypted: str) -> str:
    return _get_cipher().decrypt(encrypted.encode()).decode()


async def is_tenant_suspended(tenant_id: str) -> bool:
    try:
        r = await _get_redis()
        blocklist_key = f"suspended_tenant:{tenant_id}"
        cached = await r.get(blocklist_key)
        if cached is not None:
            return cached == "1"
    except Exception:
        # Redis unreachable — fall through to DB check (not cached)
        pass
    return False


async def cache_tenant_suspension(tenant_id: str, ttl: int | None = None) -> None:
    try:
        r = await _get_redis()
        key = f"suspended_tenant:{tenant_id}"
        await r.set(key, "1", ex=ttl or settings.suspended_tenant_blocklist_ttl)
    except Exception:
        pass


async def remove_tenant_suspension_cache(tenant_id: str) -> None:
    try:
        r = await _get_redis()
        await r.delete(f"suspended_tenant:{tenant_id}")
    except Exception:
        pass


async def check_tenant_subscription(db: Session, tenant_id: str) -> str:
    from app.services.subscription_plans import SubscriptionStatus

    row = db.execute(
        text(
            "SELECT status, subscription_end, grace_period_end FROM tenants "
            "WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    ).one_or_none()
    if not row:
        return "not_found"
    status, sub_end, grace_end = row
    if status == "suspended":
        return "suspended"
    now = datetime.now(timezone.utc)
    if sub_end and now > sub_end:
        if grace_end and now <= grace_end:
            return "past_due"
        return "expired"
    return "active"


async def check_and_update_tenant_status(
    db: Session,
    tenant_id: str,
) -> str:
    from app.services.subscription_plans import SubscriptionStatus
    from app.services.subscription_service import _ensure_aware

    row = db.execute(
        text(
            "SELECT status, subscription_end, grace_period_end, id, subscription_status FROM tenants "
            "WHERE tenant_id = :tid"
        ),
        {"tid": tenant_id},
    ).one_or_none()
    if not row:
        return "not_found"
    status, sub_end, grace_end, pk_id, subscription_status = row

    if status == "suspended" or subscription_status == SubscriptionStatus.SUSPENDED.value:
        await cache_tenant_suspension(tenant_id)
        return "suspended"

    now = datetime.now(timezone.utc)
    sub_end = _ensure_aware(sub_end)
    grace_end = _ensure_aware(grace_end)
    if sub_end and now > sub_end:
        if grace_end and now <= grace_end:
            if subscription_status != SubscriptionStatus.PAST_DUE.value:
                db.execute(
                    text(
                        "UPDATE tenants SET subscription_status = :past_due "
                        "WHERE id = :id"
                    ),
                    {"past_due": SubscriptionStatus.PAST_DUE.value, "id": pk_id},
                )
                db.commit()
            return "past_due"

        # Beyond grace period -> auto-suspend
        db.execute(
            text(
                "UPDATE tenants SET status = 'suspended', is_active = false, "
                "subscription_status = :suspended, suspended_at = :now, "
                "suspended_reason = :reason WHERE id = :id"
            ),
            {
                "suspended": SubscriptionStatus.SUSPENDED.value,
                "id": pk_id,
                "now": now,
                "reason": "Subscription expired beyond grace period",
            },
        )
        db.commit()
        await cache_tenant_suspension(tenant_id)
        await _revoke_keycloak_sessions(tenant_id)
        return "suspended"

    return "active"


async def _revoke_keycloak_sessions(tenant_id: str) -> None:
    try:
        token_url = f"{settings.keycloak_url}/realms/master/protocol/openid-connect/token"
        admin_data = {
            "grant_type": "password",
            "client_id": "admin-cli",
            "username": settings.keycloak_admin_username,
            "password": settings.keycloak_admin_password,
        }
        async with httpx.AsyncClient(timeout=10.0) as c:
            tr = await c.post(token_url, data=admin_data)
            tr.raise_for_status()
            admin_token = tr.json()["access_token"]
            headers = {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}

            users_url = f"{settings.keycloak_url}/admin/realms/{settings.keycloak_realm}/users"
            params = {"q": f"tenant_id:{tenant_id}"}
            ur = await c.get(users_url, headers=headers, params=params)
            if ur.is_success:
                for user in ur.json():
                    uid = user.get("id")
                    if uid:
                        await c.put(
                            f"{users_url}/{uid}/logout",
                            headers=headers,
                        )
    except Exception:
        pass


async def get_tenant_db_dsn(db: Session, tenant_id: str) -> str | None:
    row = db.execute(
        text("SELECT db_connection_string FROM tenants WHERE tenant_id = :tid AND is_active = true"),
        {"tid": tenant_id},
    ).scalar()
    if not row:
        return None
    return decrypt_dsn(str(row))
