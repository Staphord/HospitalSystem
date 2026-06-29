from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.master import get_master_db
from app.services.tenant_service import decrypt_dsn
from app.config import settings

# Cache for tenant engines
_tenant_engine_cache: dict[str, tuple] = {}


def _get_tenant_engine(tenant_id: str):
    """Create or retrieve a cached engine for a tenant database."""
    if tenant_id in _tenant_engine_cache:
        return _tenant_engine_cache[tenant_id]

    # Query master database for tenant DSN
    db = get_master_db()
    try:
        from sqlalchemy import text
        result = db.execute(
            text("SELECT db_connection_string FROM tenants WHERE tenant_id = :tid AND is_active = true"),
            {"tid": tenant_id},
        )
        row = result.scalar()
        if not row:
            raise ValueError(f"Tenant '{tenant_id}' not found or inactive")
        dsn = decrypt_dsn(str(row))
    finally:
        db.close()

    engine = create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=10)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _tenant_engine_cache[tenant_id] = (engine, SessionLocal)
    return engine, SessionLocal


def get_tenant_db_sync(tenant_id: str) -> Generator[Session, None, None]:
    """Get a database session for a specific tenant."""
    _, SessionLocal = _get_tenant_engine(tenant_id)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
