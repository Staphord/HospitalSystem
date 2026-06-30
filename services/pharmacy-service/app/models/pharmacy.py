import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID

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
