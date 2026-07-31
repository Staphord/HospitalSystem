from __future__ import annotations

from typing import AsyncGenerator

from cachetools import TTLCache
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.db.master import get_master_session
from app.exceptions import TenantNotFoundError
from app.services.tenant_service import get_tenant_db_dsn

_async_engine_cache: TTLCache[str, async_sessionmaker] = TTLCache(maxsize=64, ttl=3600)
_async_engine_instances: dict[str, object] = {}


def _migrate_consultation_columns(conn) -> None:
    from sqlalchemy import inspect, text
    inspector = inspect(conn)
    tables = inspector.get_table_names()
    if "consultations" in tables:
        columns = [c["name"] for c in inspector.get_columns("consultations")]
        if "admission_reason" not in columns:
            conn.execute(text("ALTER TABLE consultations ADD COLUMN admission_reason TEXT"))
        if "discharge_instructions" not in columns:
            conn.execute(text("ALTER TABLE consultations ADD COLUMN discharge_instructions TEXT"))
        if "follow_up_date" not in columns:
            conn.execute(text("ALTER TABLE consultations ADD COLUMN follow_up_date DATE"))
        if "return_date" not in columns:
            conn.execute(text("ALTER TABLE consultations ADD COLUMN return_date DATE"))
        if "return_reason" not in columns:
            conn.execute(text("ALTER TABLE consultations ADD COLUMN return_reason TEXT"))
    if "bill_items" in tables:
        bill_columns = [c["name"] for c in inspector.get_columns("bill_items")]
        if "bill_item_id" not in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ADD COLUMN bill_item_id UUID DEFAULT gen_random_uuid()"))
        if "total_price" not in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ADD COLUMN total_price FLOAT DEFAULT 0.0"))
        if "reference_id" not in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ADD COLUMN reference_id UUID"))
        if "item_id" in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ALTER COLUMN item_id SET DEFAULT gen_random_uuid()"))
        if "item_code" in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ALTER COLUMN item_code SET DEFAULT 'CONSULT'"))
            conn.execute(text("ALTER TABLE bill_items ALTER COLUMN item_code DROP NOT NULL"))
        if "line_total" in bill_columns:
            conn.execute(text("ALTER TABLE bill_items ALTER COLUMN line_total DROP NOT NULL"))


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
    # Automatically migrate dynamic tenant database tables on first access
    async with engine.begin() as conn:
        from app.db.base import Base
        import app.models  # noqa
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_consultation_columns)

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
