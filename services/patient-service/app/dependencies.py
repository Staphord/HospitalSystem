from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import get_db
from app.core.security import get_current_active_user
from app.config import settings
from app.db.base import Base

_tenant_engine_cache: dict[str, tuple] = {}
_master_engine = None
_MasterSessionLocal = None


def _get_master_engine():
    global _master_engine, _MasterSessionLocal
    if _master_engine is None:
        _master_engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )
        _MasterSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_master_engine)
    return _master_engine, _MasterSessionLocal


def get_master_db() -> Session:
    _, SessionLocal = _get_master_engine()
    return SessionLocal()


def resolve_tenant_db_url(tenant_id: str) -> str | None:
    from cryptography.fernet import Fernet
    db = get_master_db()
    try:
        row = db.execute(
            text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true"),
            {"tid": tenant_id},
        ).scalar()
        if not row:
            return None
        cipher = Fernet(settings.tenant_db_encryption_key.encode())
        return cipher.decrypt(row.encode()).decode()
    finally:
        db.close()


async def get_tenant_id_from_token(
    request: Request,
    payload: dict = Depends(get_current_active_user),
) -> str:
    tenant_id = payload.get("hospital_id") or payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tenant association found in token",
        )
    return tenant_id


async def get_tenant_db_url_from_request(
    x_tenant_db: str | None = Header(None),
    payload: dict = Depends(get_current_active_user),
) -> str | None:
    if x_tenant_db:
        return x_tenant_db
    return None


def get_tenant_session(db_url: str) -> Session:
    if db_url not in _tenant_engine_cache:
        engine = create_engine(db_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
        Base.metadata.create_all(bind=engine)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _tenant_engine_cache[db_url] = (engine, SessionLocal)
    _, SessionLocal = _tenant_engine_cache[db_url]
    return SessionLocal()


def get_tenant_db(
    x_tenant_db: str | None = Depends(get_tenant_db_url_from_request),
    tenant_id: str = Depends(get_tenant_id_from_token),
) -> Session:
    db_url = x_tenant_db or resolve_tenant_db_url(tenant_id)
    if not db_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not resolve tenant database URL. Provide X-Tenant-DB header or ensure tenant_id is valid.",
        )
    db = get_tenant_session(db_url)
    try:
        yield db
    finally:
        db.close()
