from __future__ import annotations

import logging
import subprocess
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_session_local
from app.models.master import Tenant
from app.services.tenant_service import encrypt_dsn

logger = logging.getLogger(__name__)

# Cache for tenant engines
_tenant_engine_cache: dict[str, tuple] = {}


def _get_admin_engine():
    """Create a SQLAlchemy engine using the admin connection (with CREATEDB privilege)."""
    return create_engine(settings.db_admin_url, pool_pre_ping=True, isolation_level="AUTOCOMMIT")


def _build_tenant_dsn(tenant_id: str) -> str:
    """Build the connection string for a tenant database using the template."""
    return settings.tenant_db_template.format(tenant_id=tenant_id)


def _create_database(tenant_id: str) -> str:
    """Create a new PostgreSQL database for the tenant."""
    db_name = f"tenant_{tenant_id}"
    dsn = _build_tenant_dsn(tenant_id)
    admin_engine = _get_admin_engine()

    logger.info("Creating tenant database '%s' for tenant %s", db_name, tenant_id)

    with admin_engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
            {"db_name": db_name},
        )
        if result.scalar():
            logger.warning("Database '%s' already exists — skipping creation", db_name)
        else:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            logger.info("Database '%s' created successfully", db_name)

    admin_engine.dispose()
    return dsn


def _run_alembic_migrations(tenant_id: str, db_name: str) -> None:
    """Run tenant migrations on the newly created database."""
    import os
    import sys
    logger.info("Running tenant migrations for database '%s'...", db_name)
    try:
        dsn = _build_tenant_dsn(tenant_id)
        python_executable = sys.executable
        result = subprocess.run(
            [
                python_executable, "-m", "alembic",
                "-c", "migrations/tenant/alembic.ini",
                "upgrade", "head",
            ],
            env={
                **dict(os.environ),
                "TENANT_DB_URL": dsn,
            },
            capture_output=True,
            text=True,
            check=True,
        )
        logger.info("Migrations completed for '%s'", db_name)
        if result.stdout:
            logger.debug("Alembic output: %s", result.stdout.strip())
    except subprocess.CalledProcessError as exc:
        logger.error("Migration failed for '%s': %s\n%s", db_name, exc.stderr, exc.stdout)
        raise RuntimeError(f"Tenant migration failed for {db_name}: {exc.stderr}") from exc


def _update_tenant_record(tenant_id: str, dsn: str) -> None:
    """Encrypt the tenant DSN and update the master DB tenants record."""
    db: Session = get_session_local()()
    try:
        tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
        if not tenant:
            logger.error("Tenant record %s not found in master DB — cannot update DSN", tenant_id)
            return

        tenant.db_dsn_encrypted = encrypt_dsn(dsn)
        tenant.status = "active"
        tenant.updated_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Updated tenant %s with encrypted DSN", tenant_id)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def provision_tenant_database_sync(tenant_id: str, name: str) -> str:
    """Provision a new isolated PostgreSQL database for a tenant (synchronous version).
    
    Steps:
        1. Create the database
        2. Run tenant migrations
        3. Update the tenant record with the encrypted DSN
        4. Return the DSN
    """
    db_name = f"tenant_{tenant_id}"
    try:
        dsn = _create_database(tenant_id)
        _run_alembic_migrations(tenant_id, db_name)
        _update_tenant_record(tenant_id, dsn)
        logger.info("[OK] Tenant '%s' database provisioned: %s", tenant_id, db_name)
        return dsn
    except Exception as exc:
        logger.error("[FAIL] Tenant provisioning failed for '%s': %s", tenant_id, exc)
        raise


def get_tenant_db_session(tenant_id: str) -> Session:
    """Get a database session for a specific tenant."""
    if tenant_id in _tenant_engine_cache:
        engine, SessionLocal = _tenant_engine_cache[tenant_id]
        return SessionLocal()

    # Query master database for tenant DSN
    master_db = get_session_local()()
    try:
        result = master_db.execute(
            text("SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true"),
            {"tid": tenant_id},
        )
        row = result.scalar()
        if not row:
            raise ValueError(f"Tenant '{tenant_id}' not found or inactive")
        
        from app.services.tenant_service import decrypt_dsn
        dsn = decrypt_dsn(str(row))
    finally:
        master_db.close()

    engine = create_engine(dsn, pool_pre_ping=True, pool_size=5, max_overflow=10)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    _tenant_engine_cache[tenant_id] = (engine, SessionLocal)
    return SessionLocal()
