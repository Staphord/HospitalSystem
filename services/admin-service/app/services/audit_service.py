"""Tenant audit trail helpers (FR-56)."""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.admin import AuditLog

logger = logging.getLogger("admin_service.audit")


def log_change(
    db: Session,
    *,
    user_id: str,
    action: str,
    table_name: str,
    record_id: str | None = None,
    old_values: dict[str, Any] | None = None,
    new_values: dict[str, Any] | None = None,
    ip_address: str | None = None,
    session_id: str | None = None,
) -> AuditLog | None:
    try:
        row = AuditLog(
            log_id=str(uuid.uuid4()),
            user_id=user_id or "anonymous",
            action=action,
            table_name=table_name,
            record_id=record_id,
            old_values=old_values,
            new_values=new_values,
            ip_address=ip_address,
            session_id=session_id,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    except Exception:
        logger.exception(
            "Failed to write audit_logs action=%s table=%s record=%s",
            action,
            table_name,
            record_id,
        )
        try:
            db.rollback()
        except Exception:
            pass
        return None


def list_audit_logs(
    db: Session,
    *,
    user_id: str | None = None,
    action: str | None = None,
    table_name: str | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[AuditLog], int]:
    q = db.query(AuditLog)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    if action:
        q = q.filter(AuditLog.action == action)
    if table_name:
        q = q.filter(AuditLog.table_name == table_name)
    if from_dt:
        q = q.filter(AuditLog.created_at >= from_dt)
    if to_dt:
        q = q.filter(AuditLog.created_at <= to_dt)
    total = q.count()
    rows = (
        q.order_by(AuditLog.created_at.desc())
        .offset(offset)
        .limit(min(limit, 200))
        .all()
    )
    return rows, total


def get_audit_log(db: Session, log_id: uuid.UUID) -> AuditLog | None:
    return db.query(AuditLog).filter(AuditLog.log_id == str(log_id)).first()
