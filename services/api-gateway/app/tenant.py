from __future__ import annotations

import json
from typing import Any

import httpx
import redis.asyncio as aioredis
from fastapi import HTTPException, Request, status
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

_master_engine = None
_MasterSessionLocal = None
_redis: aioredis.Redis | None = None


def _get_master_engine():
    global _master_engine, _MasterSessionLocal
    if _master_engine is None:
        _master_engine = create_engine(
            settings.master_db_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _MasterSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_master_engine)
    return _master_engine, _MasterSessionLocal


def get_master_db() -> Session:
    _, SessionLocal = _get_master_engine()
    return SessionLocal()


async def _get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=3,
        )
    return _redis


def _get_cipher():
    from cryptography.fernet import Fernet
    return Fernet(settings.tenant_db_encryption_key.encode())


def decrypt_dsn(encrypted: str) -> str:
    return _get_cipher().decrypt(encrypted.encode()).decode()


async def get_tenant_db_url(tenant_id: str) -> str | None:
    """Resolve tenant DB URL from JWT -> Master DB -> Redis cache (TTL 300s)."""
    if not tenant_id:
        return None

    r = await _get_redis()
    cache_key = f"tenant_db_url:{tenant_id}"
    try:
        cached = await r.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    db = get_master_db()
    try:
        row = db.execute(
            text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true"),
            {"tid": tenant_id},
        ).scalar()
        if not row:
            return None
        dsn = decrypt_dsn(str(row))
    finally:
        db.close()

    try:
        await r.set(cache_key, json.dumps(dsn), ex=300)
    except Exception:
        pass

    return dsn


async def is_tenant_suspended(tenant_id: str) -> bool:
    try:
        r = await _get_redis()
        cached = await r.get(f"suspended_tenant:{tenant_id}")
        if cached is not None:
            return cached == "1"
    except Exception:
        pass
    return False
