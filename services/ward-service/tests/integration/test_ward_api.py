"""Schema / constant checks for ward API."""

from app.api.v1.schemas import (
    AdmissionCreate,
    ConditionUpdate,
    DischargeRequest,
    OrderCreate,
    NursingNoteCreate,
)
from uuid import uuid4


def test_admission_create_schema():
    body = AdmissionCreate(
        visit_id=uuid4(),
        bed_id=uuid4(),
        admitting_diagnosis="Pneumonia",
    )
    assert body.admitting_diagnosis == "Pneumonia"


def test_discharge_schema():
    body = DischargeRequest(discharge_diagnosis="Resolved", discharge_instructions="Rest")
    assert body.discharge_instructions == "Rest"


def test_order_and_note_schemas():
    o = OrderCreate(order_type="medication", order_detail="Paracetamol 1g TDS")
    n = NursingNoteCreate(note_type="observation", note_text="Stable")
    assert o.order_type == "medication"
    assert n.note_type == "observation"


def test_nursing_note_schema_accepts_resp_rate():
    n = NursingNoteCreate(note_type="observation", note_text="Stable", vitals_resp_rate=16)
    assert n.vitals_resp_rate == 16


def test_condition_update_schema():
    body = ConditionUpdate(condition="critical")
    assert body.condition == "critical"
