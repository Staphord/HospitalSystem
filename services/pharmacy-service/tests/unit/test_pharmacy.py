from datetime import date, timedelta, datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.v1.schemas import DispenseRequest, LabelGenerateRequest
from app.core.security import TokenPayload
from app.db.base import Base
from app.exceptions import ConflictError, NotFoundError
from app.models.pharmacy import (
    Patient, Visit, Queue, Prescription, PrescriptionItem, DispensingRecord, DrugInventory
)
from app.services import pharmacy as svc
from app.services.inventory import SEED_INVENTORY_AMOXICILLIN_ID

PHARMACIST = TokenPayload(
    sub="sub",
    preferred_username="amina",
    email=None,
    realm_access={"roles": ["pharmacist"]},
    raw={},
)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        # Seed Patient
        patient = Patient(
            id=svc.STUB_PATIENT_ID,
            hospital_id="default-hospital",
            patient_number="PT-4891",
            full_name="Jane Mwita",
            date_of_birth=date(1985, 4, 12),
            gender="Female",
            allergies="Penicillin",
        )
        session.add(patient)

        # Seed Visit
        visit = Visit(
            visit_id=svc.STUB_VISIT_ID,
            patient_id=svc.STUB_PATIENT_ID,
            visit_number="V-20260315-042",
            visit_date=date.today(),
            visit_type="outpatient",
            payment_type="cash",
            status="in_pharmacy",
            billing_cleared=True,
        )
        session.add(visit)

        # Seed Queue waiting
        queue = Queue(
            queue_id=svc.STUB_QUEUE_ID,
            visit_id=svc.STUB_VISIT_ID,
            patient_id=str(svc.STUB_PATIENT_ID),
            queue_type="pharmacy",
            queue_number="PH-007",
            priority="urgent",
            status="waiting",
            created_at=datetime.now(timezone.utc),
        )
        session.add(queue)

        # Seed Queue completed
        queue_comp = Queue(
            queue_id=svc.STUB_QUEUE_COMPLETED_ID,
            visit_id=svc.STUB_VISIT_ID,
            patient_id=str(svc.STUB_PATIENT_ID),
            queue_type="pharmacy",
            queue_number="PH-008",
            priority="non_urgent",
            status="completed",
            created_at=datetime.now(timezone.utc),
        )
        session.add(queue_comp)

        # Seed Prescription
        prescription = Prescription(
            prescription_id=uuid4(),
            visit_id=svc.STUB_VISIT_ID,
            patient_id=svc.STUB_PATIENT_ID,
            prescribed_by="Dr. Nguyen",
            prescribed_at=datetime.now(timezone.utc),
            status="pending",
        )
        session.add(prescription)
        await session.flush()

        # Seed PrescriptionItems
        rx_item1 = PrescriptionItem(
            prescription_item_id=svc.STUB_PRESCRIPTION_PENDING_ID,
            prescription_id=prescription.prescription_id,
            drug_name="Amoxicillin",
            dose="500mg",
            frequency="Three times daily",
            duration="7 days",
            instructions="Take after meals",
            quantity_prescribed=21,
            status="pending",
        )
        session.add(rx_item1)

        rx_item2 = PrescriptionItem(
            prescription_item_id=svc.STUB_PRESCRIPTION_DISPENSED_ID,
            prescription_id=prescription.prescription_id,
            drug_name="Ibuprofen",
            dose="400mg",
            frequency="As needed",
            duration="5 days",
            instructions=None,
            quantity_prescribed=10,
            status="dispensed",
        )
        session.add(rx_item2)

        # Seed DrugInventory
        inventory = DrugInventory(
            inventory_id=SEED_INVENTORY_AMOXICILLIN_ID,
            drug_name="Amoxicillin",
            brand_name="Amoxil",
            drug_code="AMX-500",
            category="Antibiotic",
            unit="tablets",
            quantity_in_stock=179,
            reorder_level=100,
            unit_cost=50.00,
            unit_price=80.00,
            location="Shelf B-3",
            is_active=True,
        )
        session.add(inventory)

        # Seed DispensingRecord for the dispensed item
        disp_rec = DispensingRecord(
            dispensing_id=svc.STUB_DISPENSING_ID,
            prescription_item_id=svc.STUB_PRESCRIPTION_DISPENSED_ID,
            visit_id=svc.STUB_VISIT_ID,
            inventory_id=SEED_INVENTORY_AMOXICILLIN_ID,
            quantity_dispensed=10,
            unit="tablets",
            batch_number="BATCH-2025-089",
            expiry_date=date(2027, 6, 30),
            dispensed_by="amina",
            dispensed_at=datetime.now(timezone.utc),
        )
        session.add(disp_rec)

        await session.commit()
        yield session

    await engine.dispose()


@pytest.mark.asyncio
async def test_get_pharmacy_queue_returns_seeded_item(db_session: AsyncSession):
    result = await svc.get_pharmacy_queue(db_session, date.today(), "waiting")
    assert result.date == date.today()
    assert len(result.queue) == 1
    assert result.queue[0].queue_number == "PH-007"
    assert result.queue[0].patient_name == "Jane Mwita"


@pytest.mark.asyncio
async def test_call_queue_unknown_id_raises_404(db_session: AsyncSession):
    with pytest.raises(NotFoundError):
        await svc.call_queue_patient(db_session, uuid4(), PHARMACIST)


@pytest.mark.asyncio
async def test_call_completed_queue_raises_409(db_session: AsyncSession):
    with pytest.raises(ConflictError):
        await svc.call_queue_patient(db_session, svc.STUB_QUEUE_COMPLETED_ID, PHARMACIST)


@pytest.mark.asyncio
async def test_dispense_requires_acknowledgment_when_alerts_exist(db_session: AsyncSession):
    body = DispenseRequest(
        prescription_id=svc.STUB_PRESCRIPTION_PENDING_ID,
        visit_id=svc.STUB_VISIT_ID,
        drug_name="Amoxicillin",
        batch_number="BATCH-2025-089",
        expiry_date=date.today() + timedelta(days=365),
        quantity_dispensed=21,
        unit="tablets",
        interaction_alert_acknowledged=False,
    )
    with pytest.raises(ConflictError) as exc:
        await svc.dispense_prescription(db_session, body, PHARMACIST)
    assert "INTERACTION_ALERT_NOT_ACKNOWLEDGED" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_dispense_success_with_acknowledgment(db_session: AsyncSession):
    body = DispenseRequest(
        prescription_id=svc.STUB_PRESCRIPTION_PENDING_ID,
        visit_id=svc.STUB_VISIT_ID,
        drug_name="Amoxicillin",
        batch_number="BATCH-2025-089",
        expiry_date=date.today() + timedelta(days=365),
        quantity_dispensed=21,
        unit="tablets",
        interaction_alert_acknowledged=True,
    )
    result = await svc.dispense_prescription(db_session, body, PHARMACIST)
    assert result.quantity_dispensed == 21
    assert result.dispensed_by == "amina"
    assert result.remaining_stock == 158  # 179 - 21


@pytest.mark.asyncio
async def test_generate_label(db_session: AsyncSession):
    body = LabelGenerateRequest(
        dispensing_id=svc.STUB_DISPENSING_ID,
    )
    result = await svc.generate_label(db_session, body, PHARMACIST)
    assert result.label.patient_name == "Jane Mwita"
    assert result.label.drug_name == "Ibuprofen"
    assert result.label.batch_number == "BATCH-2025-089"


@pytest.mark.asyncio
async def test_dispense_fails_when_billing_not_cleared(db_session: AsyncSession):
    # Set visit billing_cleared to False
    visit = await db_session.get(Visit, svc.STUB_VISIT_ID)
    visit.billing_cleared = False
    await db_session.commit()

    body = DispenseRequest(
        prescription_id=svc.STUB_PRESCRIPTION_PENDING_ID,
        visit_id=svc.STUB_VISIT_ID,
        drug_name="Amoxicillin",
        batch_number="BATCH-2025-089",
        expiry_date=date.today() + timedelta(days=365),
        quantity_dispensed=21,
        unit="tablets",
        interaction_alert_acknowledged=True,
    )
    with pytest.raises(ConflictError) as exc:
        await svc.dispense_prescription(db_session, body, PHARMACIST)
    assert "BILLING_NOT_CLEARED" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_generate_label_preview_by_prescription_item(db_session: AsyncSession):
    body = LabelGenerateRequest(
        prescription_item_id=svc.STUB_PRESCRIPTION_PENDING_ID,
    )
    result = await svc.generate_label(db_session, body, PHARMACIST)
    assert result.label.patient_name == "Jane Mwita"
    assert result.label.drug_name == "Amoxicillin"
    assert result.label.batch_number == "PREVIEW"