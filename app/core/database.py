from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine: Optional[object] = None
_SessionLocal: Optional[sessionmaker] = None

# Cache for tenant database engines
_tenant_engine_cache: dict[str, tuple] = {}


def _ensure_database_exists() -> None:
    """Ensure the master database exists, auto-create if not."""
    import urllib.parse
    from sqlalchemy.exc import OperationalError
    
    try:
        # Try to connect to see if database exists
        test_engine = create_engine(settings.database_url, pool_pre_ping=True)
        with test_engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        test_engine.dispose()
        return  # Database exists, nothing to do
    except OperationalError:
        # Database doesn't exist, create it using admin connection
        pass
    
    # Parse database name from URL
    parsed = urllib.parse.urlparse(settings.database_url)
    db_name = parsed.path.lstrip("/")
    
    if not db_name:
        raise ValueError("Cannot determine database name from DATABASE_URL")
    
    # Create database using admin connection
    admin_engine = create_engine(
        settings.db_admin_url,
        pool_pre_ping=True,
        isolation_level="AUTOCOMMIT",
    )
    
    with admin_engine.connect() as conn:
        # Check if database exists
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": db_name},
        )
        if not result.scalar():
            # Create database
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            print(f"[AUTO-CREATE] Database '{db_name}' created successfully")
    
    admin_engine.dispose()


def _init_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        return
    
    # Ensure database exists before creating engine
    _ensure_database_exists()
    
    _engine = create_engine(settings.database_url, pool_pre_ping=True)
    _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)


def get_session_local() -> sessionmaker:
    _init_engine()
    return _SessionLocal


def _migrate_user_table() -> None:
    from sqlalchemy import inspect, text
    from app.db.base import Base

    inspector = inspect(_engine)
    columns = [c["name"] for c in inspector.get_columns("users")]
    with _engine.connect() as conn:
        if "username" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN username VARCHAR"))
        if "role" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN role VARCHAR"))
        if "full_name" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR"))
        conn.commit()


def _migrate_super_admins_table() -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(_engine)
    tables = inspector.get_table_names()
    if "super_admins" not in tables:
        with _engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE super_admins (
                    super_admin_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    username VARCHAR(50) UNIQUE NOT NULL,
                    email VARCHAR(150) UNIQUE NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    full_name VARCHAR(200) NOT NULL,
                    role VARCHAR(50) NOT NULL DEFAULT 'super_admin',
                    mfa_secret VARCHAR(100) NOT NULL,
                    is_active BOOLEAN NOT NULL DEFAULT true,
                    last_login_at TIMESTAMP,
                    created_at TIMESTAMP NOT NULL DEFAULT now()
                )
            """))
            conn.commit()


def init_db() -> None:
    from app.db.base import Base
    import app.models

    _init_engine()
    Base.metadata.create_all(bind=_engine)
    _migrate_user_table()
    _migrate_super_admins_table()


class DatabaseRouter(ABC):
    @abstractmethod
    def get_session(self, hospital_id: str) -> Session:
        raise NotImplementedError


class DefaultDatabaseRouter(DatabaseRouter):
    def get_session(self, hospital_id: str) -> Session:
        return get_session_local()()


class TenantDatabaseRouter(DatabaseRouter):
    """Routes to tenant-specific databases based on hospital_id.
    
    Looks up the tenant database DSN from the master database and creates
    an isolated connection for that tenant.
    """
    
    def get_session(self, hospital_id: str) -> Session:
        if not hospital_id:
            return get_session_local()()
        
        # Check if we have a cached engine for this tenant
        if hospital_id in _tenant_engine_cache:
            engine, SessionLocal = _tenant_engine_cache[hospital_id]
            return SessionLocal()
        
        # Query master database for tenant DSN
        from cryptography.fernet import Fernet
        
        master_db = get_session_local()()
        try:
            result = master_db.execute(
                text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true"),
                {"tid": hospital_id},
            )
            row = result.scalar()
            if not row:
                # Tenant not found or not active, fall back to master database
                return get_session_local()()
            
            # Decrypt DSN
            cipher = Fernet(settings.tenant_db_encryption_key.encode())
            dsn = cipher.decrypt(row.encode()).decode()
        finally:
            master_db.close()
        
        # Create engine for tenant database
        engine = create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=10)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
        _tenant_engine_cache[hospital_id] = (engine, SessionLocal)
        return SessionLocal()


# Use TenantDatabaseRouter for multi-tenant database isolation
_router = TenantDatabaseRouter()


@dataclass
class HospitalContext:
    hospital_id: str
    db: Session


def get_db() -> Generator[Session, None, None]:
    db = get_session_local()()
    try:
        yield db
    finally:
        db.close()


def get_tenant_db(hospital_id: str) -> Generator[Session, None, None]:
    """Get a database session for a specific tenant.
    
    This routes to the tenant-specific database instead of the master database.
    """
    db = _router.get_session(hospital_id)
    try:
        yield db
    finally:
        db.close()


def get_hospital_context(hospital_id: str) -> HospitalContext:
    db = _router.get_session(hospital_id)
    return HospitalContext(hospital_id=hospital_id, db=db)


def close_hospital_context(context: HospitalContext) -> None:
    context.db.close()
