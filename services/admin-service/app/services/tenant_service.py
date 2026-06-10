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
    row = db.execute(
        text(
            "SELECT status, subscription_end FROM tenants "
            "WHERE tenant_id = :tid AND is_active = true"
        ),
        {"tid": tenant_id},
    ).one_or_none()
    if not row:
        return "not_found"
    status, sub_end = row
    if status == "suspended":
        return "suspended"
    if sub_end and sub_end < datetime.now(timezone.utc):
        return "expired"
    return "active"


async def check_and_update_tenant_status(
    db: Session,
    tenant_id: str,
) -> str:
    row = db.execute(
        text(
            "SELECT status, subscription_end, id FROM tenants "
            "WHERE tenant_id = :tid AND is_active = true"
        ),
        {"tid": tenant_id},
    ).one_or_none()
    if not row:
        return "not_found"
    status, sub_end, pk_id = row

    if status == "suspended":
        await cache_tenant_suspension(tenant_id)
        return "suspended"

    if sub_end and sub_end < datetime.now(timezone.utc):
        days_overdue = (datetime.now(timezone.utc) - sub_end).days
        if days_overdue >= 30:
            db.execute(
                text("UPDATE tenants SET status = 'suspended', is_active = false WHERE id = :id"),
                {"id": pk_id},
            )
            db.commit()
            await cache_tenant_suspension(tenant_id)
            await _revoke_keycloak_sessions(tenant_id)
            return "suspended"
        return "expired"

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
        text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true"),
        {"tid": tenant_id},
    ).scalar()
    if not row:
        return None
    return decrypt_dsn(str(row))
