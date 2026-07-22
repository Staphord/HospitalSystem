import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.db.base import Base


class DrugInventory(Base):
    __tablename__ = "drug_inventory"

    inventory_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    drug_name = Column(String(200), nullable=False, index=True)
    brand_name = Column(String(200), nullable=True)
    drug_code = Column(String(50), nullable=True)
    category = Column(String(100), nullable=True, index=True)
    unit = Column(String(50), nullable=False, default="tablets")
    quantity_in_stock = Column(Integer, nullable=False, default=0)
    reorder_level = Column(Integer, nullable=False, default=0)
    unit_cost = Column(Numeric(12, 2), nullable=False, default=0)
    unit_price = Column(Numeric(12, 2), nullable=False, default=0)
    location = Column(String(100), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    last_restocked_at = Column(DateTime(timezone=True), nullable=True)
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


class DrugInventoryTransaction(Base):
    __tablename__ = "drug_inventory_transactions"

    transaction_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inventory_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    transaction_type = Column(String(30), nullable=False)
    quantity_change = Column(Integer, nullable=False)
    quantity_before = Column(Integer, nullable=False)
    quantity_after = Column(Integer, nullable=False)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    unit_cost = Column(Numeric(12, 2), nullable=True)
    notes = Column(Text, nullable=True)
    reference_id = Column(UUID(as_uuid=True), nullable=True)
    performed_by = Column(String(255), nullable=False)
    performed_by_name = Column(String(255), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )


class Patient(Base):
    __tablename__ = "patients"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=True)
    patient_number = Column(String(30), nullable=False)
    full_name = Column(String(200), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    gender = Column(String(20), nullable=False)
    allergies = Column(Text, nullable=True)


class Visit(Base):
    __tablename__ = "visits"

    visit_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    patient_id = Column(UUID(as_uuid=True), nullable=False)
    visit_number = Column(String(20), nullable=False)
    visit_date = Column(Date, nullable=False)
    visit_type = Column(String(50), nullable=False)
    payment_type = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    billing_cleared = Column(Boolean, nullable=False, default=False)


class Consultation(Base):
    __tablename__ = "consultations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False, unique=True)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    history_of_presenting_illness = Column(Text, nullable=True)
    examination_findings = Column(Text, nullable=True)
    clinical_impression = Column(Text, nullable=True)


class Diagnosis(Base):
    __tablename__ = "diagnoses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    consultation_id = Column(UUID(as_uuid=True), ForeignKey("consultations.id"), nullable=False)
    diagnosis_type = Column(String(50), nullable=False)
    code = Column(String(20), nullable=True)
    description = Column(Text, nullable=False)



class Queue(Base):
    __tablename__ = "queues"

    queue_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(String(36), nullable=False)
    queue_type = Column(String(50), nullable=False)
    queue_number = Column(String(10), nullable=False)
    priority = Column(String(50), nullable=False)
    status = Column(String(50), nullable=False)
    called_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class Prescription(Base):
    __tablename__ = "prescriptions"

    prescription_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    patient_id = Column(UUID(as_uuid=True), ForeignKey("patients.id"), nullable=False)
    prescribed_by = Column(String(255), nullable=True)
    prescribed_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    status = Column(String(50), nullable=False, default="pending")

    visit = relationship("Visit", lazy="selectin")
    patient = relationship("Patient", lazy="selectin")
    items = relationship("PrescriptionItem", back_populates="prescription", cascade="all, delete-orphan", lazy="selectin")


class PrescriptionItem(Base):
    __tablename__ = "prescription_items"

    prescription_item_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_id = Column(UUID(as_uuid=True), ForeignKey("prescriptions.prescription_id"), nullable=False)
    drug_name = Column(String(200), nullable=False)
    dose = Column(String(100), nullable=True)
    frequency = Column(String(100), nullable=True)
    duration = Column(String(100), nullable=True)
    instructions = Column(Text, nullable=True)
    quantity_prescribed = Column(Integer, nullable=True)
    status = Column(String(50), nullable=False, default="pending")

    prescription = relationship("Prescription", back_populates="items")
    dispensing_record = relationship("DispensingRecord", back_populates="prescription_item", uselist=False, lazy="selectin")


class DispensingRecord(Base):
    __tablename__ = "dispensing_records"

    dispensing_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    prescription_item_id = Column(UUID(as_uuid=True), ForeignKey("prescription_items.prescription_item_id"), nullable=False)
    visit_id = Column(UUID(as_uuid=True), ForeignKey("visits.visit_id"), nullable=False)
    inventory_id = Column(UUID(as_uuid=True), ForeignKey("drug_inventory.inventory_id"), nullable=True)
    quantity_dispensed = Column(Integer, nullable=False)
    unit = Column(String(50), nullable=True)
    batch_number = Column(String(100), nullable=True)
    expiry_date = Column(Date, nullable=True)
    dispensed_by = Column(String(255), nullable=True)
    dispensed_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    prescription_item = relationship("PrescriptionItem", back_populates="dispensing_record")

