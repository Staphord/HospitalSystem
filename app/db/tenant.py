from typing import Dict, Optional

from cachetools import TTLCache
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.core.tenant import resolve_tenant_db_url
from app.exceptions import TenantNotFoundError

_engine_cache: TTLCache[str, sessionmaker] = TTLCache(
    maxsize=64, ttl=3600
)


def _get_tenant_session_factory(tenant_id: str) -> sessionmaker:
    if tenant_id in _engine_cache:
        return _engine_cache[tenant_id]

    db_url = resolve_tenant_db_url(tenant_id)
    if not db_url:
        raise TenantNotFoundError(f"Tenant '{tenant_id}' not found")

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        pool_recycle=3600,
    )
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _engine_cache[tenant_id] = factory
    return factory


def get_tenant_db(tenant_id: str) -> Session:
    factory = _get_tenant_session_factory(tenant_id)
    db = factory()
    try:
        yield db
    finally:
        db.close()
