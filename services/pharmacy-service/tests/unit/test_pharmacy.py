from datetime import date, timedelta
from uuid import uuid4

import pytest

from app.api.v1.schemas import DispenseRequest
from app.core.security import TokenPayload
from app.exceptions import ConflictError, NotFoundError
from app.services import pharmacy as svc

PHARMACIST = TokenPayload(
    sub="sub",
    preferred_username="amina",
    email=None,
    realm_access={"roles": ["pharmacist"]},
    raw={},
)


def test_get_pharmacy_queue_returns_stub_item():
    result = svc.get_pharmacy_queue(date.today(), "waiting")
    assert result.date == date.today()
    assert len(result.queue) == 1
    assert result.queue[0].queue_number == "PH-007"


def test_call_queue_unknown_id_raises_404():
    with pytest.raises(NotFoundError):
        svc.call_queue_patient(uuid4(), PHARMACIST)


def test_call_completed_queue_raises_409():
    with pytest.raises(ConflictError):
        svc.call_queue_patient(svc.STUB_QUEUE_COMPLETED_ID, PHARMACIST)


def test_dispense_requires_acknowledgment_when_alerts_exist():
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
        svc.dispense_prescription(body, PHARMACIST)
    assert "INTERACTION_ALERT_NOT_ACKNOWLEDGED" in str(exc.value.detail)


def test_dispense_success_with_acknowledgment():
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
    result = svc.dispense_prescription(body, PHARMACIST)
    assert result.quantity_dispensed == 21
    assert result.dispensed_by == "amina"
