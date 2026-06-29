"""Tenant data export service for pre-termination data download."""

import logging
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.config import settings

logger = logging.getLogger("master_service.export")


def _serialize_value(val):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, bytes):
        return val.decode()
    if isinstance(val, UUID):
        return str(val)
    return val


def _table_to_dicts(engine, table_name: str) -> list[dict]:
    """Fetch all rows from a table and return as list of dicts."""
    with engine.connect() as conn:
        inspector = inspect(engine)
        columns = [col["name"] for col in inspector.get_columns(table_name)]
        rows = conn.execute(text(f"SELECT * FROM {table_name}")).fetchall()
        result = []
        for row in rows:
            result.append({col: _serialize_value(getattr(row, col)) for col in columns})
        return result


async def export_tenant_data(db: Session, tenant_id: str) -> dict:
    """Export all data from a tenant's database as a JSON-serializable dict."""
    from app.services.tenant_service import decrypt_dsn, get_tenant_db_dsn

    dsn = await get_tenant_db_dsn(db, tenant_id)
    if not dsn:
        raise ValueError(f"Could not resolve database URL for tenant {tenant_id}")

    engine = create_engine(dsn, pool_pre_ping=True)

    tables = [
        "patients",
        "patient_insurance",
        "patient_number_sequences",
        "visits",
        "queues",
        "queue_number_sequences",
        "visit_number_sequences",
        "users",
    ]

    data = {}
    with engine.connect() as conn:
        inspector = inspect(engine)
        existing = set(inspector.get_table_names())
        for table in tables:
            if table in existing:
                try:
                    data[table] = _table_to_dicts(engine, table)
                except Exception as exc:
                    logger.warning("Failed to export table %s: %s", table, exc)
                    data[table] = []
            else:
                data[table] = []

    engine.dispose()
    return data
