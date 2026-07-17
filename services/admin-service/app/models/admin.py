"""Tenant-scoped administration ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.db.base import Base


class Department(Base):
    __tablename__ = "departments"

    # Business key as ORM PK (String works for UUID or legacy varchar).
    # Legacy tables may also have an unused integer `id` column — do not map it.
    department_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    department_name = Column(String(100), nullable=False)
    department_type = Column(String(50), nullable=False)
    head_user_sub = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class FeeSchedule(Base):
    __tablename__ = "fee_schedules"

    fee_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    item_name = Column(String(200), nullable=False)
    item_code = Column(String(50), nullable=False, unique=True)
    item_type = Column(String(50), nullable=False)
    standard_price = Column(Numeric(10, 2), nullable=False)
    insurance_price = Column(Numeric(10, 2), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(Date, nullable=False)
    effective_to = Column(Date, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class InsuranceProvider(Base):
    __tablename__ = "insurance_providers"

    provider_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), nullable=False, unique=True)
    contact_email = Column(String(150), nullable=True)
    contact_phone = Column(String(20), nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class Bed(Base):
    __tablename__ = "beds"

    bed_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ward_name = Column(String(100), nullable=False)
    bed_number = Column(String(20), nullable=False)
    bed_type = Column(String(50), nullable=False, default="general")
    is_available = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (UniqueConstraint("ward_name", "bed_number", name="uq_beds_ward_number"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    # log_id as ORM PK (String for UUID or legacy varchar). Apply tenant migration
    # 0015 so legacy DBs gain user_id / session_id (legacy may keep unused user_sub).
    log_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(255), nullable=False, index=True)
    action = Column(String(100), nullable=False, index=True)
    table_name = Column(String(100), nullable=False, index=True)
    record_id = Column(String(255), nullable=True)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    ip_address = Column(String(45), nullable=True)
    session_id = Column(String(100), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )


class BackupJob(Base):
    __tablename__ = "backup_jobs"

    backup_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String(64), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="pending")
    file_path = Column(Text, nullable=True)
    size_bytes = Column(BigInteger, nullable=True)
    triggered_by = Column(String(32), nullable=False, default="user")
    triggered_by_sub = Column(String(255), nullable=True)
    error = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    finished_at = Column(DateTime(timezone=True), nullable=True)


class RolePermission(Base):
    __tablename__ = "role_permissions"

    permission_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    role_name = Column(String(50), nullable=False, unique=True)
    modules = Column(JSONB, nullable=False, default=list)
    actions = Column(JSONB, nullable=False, default=list)
    updated_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
