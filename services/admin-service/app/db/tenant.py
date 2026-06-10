from __future__ import annotations

from typing import AsyncGenerator

from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.master import get_master_session
from app.exceptions import TenantNotFoundError
from app.services.tenant_service import get_tenant_db_dsn

_async_engine_cache: TTLCache[str, async_sessionmaker] = TTLCache(maxsize=64, ttl=3600)
_async_engine_instances: dict[str, object] = {}


async def _get_async_session_factory(tenant_id: str) -> async_sessionmaker:
    if tenant_id in _async_engine_cache:
        return _async_engine_cache[tenant_id]

    from app.db.master import get_master_db
    db = get_master_db()
    try:
        dsn = await get_tenant_db_dsn(db, tenant_id)
    finally:
        db.close()

    if not dsn:
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found or inactive")

    async_dsn = dsn.replace("postgresql://", "postgresql+asyncpg://")
    engine = create_async_engine(
        async_dsn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
        echo=settings.environment == "dev",
    )
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    _async_engine_cache[tenant_id] = factory
    _async_engine_instances[tenant_id] = engine
    return factory


async def get_tenant_session(tenant_id: str) -> AsyncGenerator[AsyncSession, None]:
    factory = await _get_async_session_factory(tenant_id)
    async with factory() as session:
        try:
            yield session
        finally:
            await session.close()
