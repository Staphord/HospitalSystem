"""Minimal billing ORM for ward LOS charge lines."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Bill(Base):
    __tablename__ = "bills"

    bill_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    admission_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    status = Column(String(32), nullable=False, default="open")
    total_amount = Column(Numeric(12, 2), nullable=False, default=0)
    currency = Column(String(5), nullable=False, default="USD")
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class BillItem(Base):
    __tablename__ = "bill_items"

    item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bill_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    item_code = Column(String(50), nullable=False)
    item_type = Column(String(50), nullable=False)
    description = Column(String(255), nullable=False)
    quantity = Column(Numeric(10, 2), nullable=False, default=1)
    unit_price = Column(Numeric(12, 2), nullable=False)
    line_total = Column(Numeric(12, 2), nullable=False)
    source_ref = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("bill_id", "item_code", "source_ref", name="uq_bill_items_idempotent"),
    )


class FeeSchedule(Base):
    __tablename__ = "fee_schedules"

    fee_id = Column(UUID(as_uuid=True), primary_key=True)
    item_name = Column(String(200), nullable=False)
    item_code = Column(String(50), nullable=False, unique=True)
    item_type = Column(String(50), nullable=False)
    standard_price = Column(Numeric(10, 2), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
