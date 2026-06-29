import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean, Column, Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base


class PatientInsurance(Base):
    __tablename__ = "patient_insurance"

    insurance_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    insurer_name = Column(String(150), nullable=False)
    policy_number = Column(String(100), nullable=False)
    coverage_limit = Column(Numeric(12, 2))
    expiry_date = Column(Date)
    verification_status = Column(
        Enum("pending", "verified", "rejected", name="verification_status_enum"),
        nullable=False, default="pending",
    )
    verified_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Visit(Base):
    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    visit_number = Column(String(20), unique=True, nullable=False, index=True)
    visit_date = Column(Date, nullable=False, default=date.today)
    visit_type = Column(
        Enum("outpatient", "inpatient", "emergency", name="visit_type_enum"),
        nullable=False,
    )
    payment_type = Column(
        Enum("cash", "insurance", name="payment_type_enum"),
        nullable=False,
    )
    insurance_id = Column(UUID(as_uuid=True), ForeignKey("patient_insurance.insurance_id"), nullable=True)
    verification_flag = Column(Text, nullable=True)
    queue_number = Column(String(10), nullable=True)
    status = Column(
        Enum(
            "registered", "triaged", "in_consultation", "in_lab",
            "in_pharmacy", "completed", "cancelled",
            name="visit_status_enum",
        ),
        nullable=False, default="registered",
    )
    registered_by = Column(UUID(as_uuid=True), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Queue(Base):
    __tablename__ = "queues"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    queue_type = Column(
        Enum(
            "triage", "doctor", "lab", "radiology", "pharmacy", "billing",
            name="queue_type_enum",
        ),
        nullable=False,
    )
    queue_number = Column(String(10), nullable=False)
    priority = Column(
        Enum("emergency", "urgent", "semi_urgent", "non_urgent", name="priority_enum"),
        nullable=False, default="non_urgent",
    )
    status = Column(
        Enum("waiting", "in_progress", "completed", "skipped", name="queue_status_enum"),
        nullable=False, default="waiting",
    )
    called_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class QueueNumberSequence(Base):
    __tablename__ = "queue_number_sequences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    queue_type = Column(String(20), nullable=False)
    date_key = Column(String(8), nullable=False)
    counter = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("queue_type", "date_key", name="uq_queue_type_date_key"),
    )


class VisitNumberSequence(Base):
    __tablename__ = "visit_number_sequences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date_key = Column(String(8), nullable=False)
    counter = Column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("date_key", name="uq_visit_date_key"),
    )
