"""Tenant-scoped ward / inpatient ORM models."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Bed(Base):
    """Same table as admin bed catalog; ward updates availability only."""

    __tablename__ = "beds"

    bed_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ward_name = Column(String(100), nullable=False)
    bed_number = Column(String(20), nullable=False)
    bed_type = Column(String(50), nullable=False, default="general")
    is_available = Column(Boolean, nullable=False, default=True)
    is_active = Column(Boolean, nullable=False, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)

    __table_args__ = (UniqueConstraint("ward_name", "bed_number", name="uq_beds_ward_number"),)


class Admission(Base):
    __tablename__ = "admissions"

    admission_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    bed_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    admitting_doctor_id = Column(String(255), nullable=False)
    admitting_diagnosis = Column(Text, nullable=False)
    admission_date = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    discharge_date = Column(DateTime(timezone=True), nullable=True)
    length_of_stay_days = Column(Numeric(6, 1), nullable=True)
    discharge_diagnosis = Column(Text, nullable=True)
    discharge_instructions = Column(Text, nullable=True)
    discharge_order_by = Column(String(255), nullable=True)
    status = Column(String(32), nullable=False, default="active", index=True)
    ward_name = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False)


class InpatientOrder(Base):
    __tablename__ = "inpatient_orders"

    order_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admission_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    order_type = Column(String(50), nullable=False)
    order_detail = Column(Text, nullable=False)
    frequency = Column(String(50), nullable=True)
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    ordered_by = Column(String(255), nullable=False)
    status = Column(String(32), nullable=False, default="active")
    ordered_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


class NursingNote(Base):
    __tablename__ = "nursing_notes"

    note_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    admission_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    note_type = Column(String(50), nullable=False)
    note_text = Column(Text, nullable=False)
    vitals_bp = Column(String(20), nullable=True)
    vitals_temp = Column(Numeric(5, 2), nullable=True)
    vitals_pulse = Column(Integer, nullable=True)
    vitals_spo2 = Column(Numeric(5, 2), nullable=True)
    authored_by = Column(String(255), nullable=False)
    authored_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)


# Lightweight visit row for FK-less lookups / status soft-update
class Visit(Base):
    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    visit_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=True)


class Consultation(Base):
    __tablename__ = "consultations"

    id = Column(UUID(as_uuid=True), primary_key=True)
    visit_id = Column(UUID(as_uuid=True), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    disposition = Column(String(50), nullable=True)
