from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1 import router
from app.api.v1.schemas import DischargeRequest, InpatientOrderCreate, OrderStatusUpdate, ReferralCreateRequest


def _result(first=None, all_items=None, scalar=None):
    r = MagicMock(); r.scalars.return_value.first.return_value = first; r.scalars.return_value.all.return_value = all_items or []
    r.scalar.return_value = scalar
    return r


def _patient(pid):
    return SimpleNamespace(id=pid, full_name="Jane Doe", patient_number="P-9", gender="female", date_of_birth=date(1990, 1, 1))


def _admission(aid, pid, vid):
    return SimpleNamespace(id=aid, patient_id=pid, visit_id=vid, ward="Ward A", bed="B1", status="admitted",
        condition="stable", admitting_diagnosis="Fever", admission_date=datetime.utcnow()-timedelta(days=2),
        discharge_diagnosis=None, care_summary=None, discharge_instructions=None, follow_up_date=None,
        discharged_at=None, updated_at=None)


@pytest.mark.asyncio
async def test_inpatient_admission_list_and_details():
    aid, pid, vid = uuid4(), uuid4(), uuid4(); adm = _admission(aid, pid, vid); patient = _patient(pid)
    visit = SimpleNamespace(visit_id=vid, patient_id=pid, status="admitted")
    db = AsyncMock(); db.add = MagicMock()
    db.execute = AsyncMock(side_effect=[_result(all_items=[visit]), _result(), _result(), _result(all_items=[adm]), _result(first=patient)])
    listed = await router.get_inpatient_admissions(db, MagicMock())
    assert listed[0].name == "Jane Doe"; db.commit.assert_awaited()
    db.execute = AsyncMock(side_effect=[_result(first=adm), _result(first=patient), _result(), _result()])
    details = await router.get_admission_details(aid, db, MagicMock())
    assert details.patient.ward == "Ward A"
    db.execute = AsyncMock(side_effect=[_result(first=adm), _result(first=None)])
    with pytest.raises(Exception): await router.get_admission_details(aid, db, MagicMock())


@pytest.mark.asyncio
async def test_inpatient_order_lifecycle_and_discharge():
    aid, oid, vid = uuid4(), uuid4(), uuid4(); pid = uuid4(); adm = _admission(aid, pid, vid)
    order = SimpleNamespace(id=oid, admission_id=aid, order_type="medication", description="Drug", sub_description=None,
        issued_at=datetime.utcnow(), due_label="Today", status="pending", completed_by=None, completed_at=None)
    db = AsyncMock(); db.add = MagicMock()
    db.execute = AsyncMock(return_value=_result(all_items=[order]))
    assert (await router.get_inpatient_orders_route(aid, db, MagicMock()))[0].description == "Drug"
    db.execute = AsyncMock(side_effect=[_result(first=adm), _result()])
    current = SimpleNamespace(sub="doctor", username="doctor")
    created = await router.create_inpatient_order_route(aid, InpatientOrderCreate(order_type="diet", description="Diet"), db, current)
    assert created.status == "pending"
    db.execute = AsyncMock(return_value=_result(first=order))
    updated = await router.update_inpatient_order_status(oid, OrderStatusUpdate(status="done"), db, current)
    assert updated.status == "done" and order.completed_by == "doctor"
    db.execute = AsyncMock(side_effect=[_result(first=adm), _result(first=SimpleNamespace(status="admitted", updated_at=None))])
    response = await router.discharge_inpatient_patient(aid, DischargeRequest(discharge_diagnosis="D", condition="stable", care_summary="S", instructions="I", follow_up_date="bad"), db, current)
    assert response["status"] == "success"
    order.status = "pending"; db.execute = AsyncMock(return_value=_result(first=order))
    assert (await router.update_inpatient_order_status(oid, OrderStatusUpdate(status="discontinued"), db, current)).status == "discontinued"
    db.execute = AsyncMock(side_effect=[_result(first=adm), _result(first=None)])
    assert (await router.discharge_inpatient_patient(aid, DischargeRequest(discharge_diagnosis="D", condition="stable", care_summary="S", instructions="I"), db, current))["status"] == "success"


@pytest.mark.asyncio
async def test_patient_search_and_recent_results():
    pid = uuid4(); patient = _patient(pid)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[_result(scalar=1), _result(all_items=[patient])])
    result = await router.search_patients_endpoint("Jane", 1, 10, db, MagicMock())
    assert result.total == 1 and result.patients[0].full_name == "Jane Doe"
    db.execute = AsyncMock(return_value=_result(all_items=[patient]))
    assert (await router.get_recent_patients(3, db, MagicMock()))[0].full_name == "Jane Doe"
    db.execute = AsyncMock(side_effect=[_result(scalar=0), _result(all_items=[])])
    empty = await router.search_patients_endpoint("", 2, 5, db, MagicMock())
    assert empty.total == 0 and empty.patients == []


@pytest.mark.asyncio
async def test_investigation_dashboard_and_referrals():
    pid, vid, iid = uuid4(), uuid4(), uuid4(); patient = _patient(pid); now = datetime.utcnow()
    inv = SimpleNamespace(id=iid, patient_id=pid, visit_id=vid, test_name="CBC", request_type="lab", urgency="urgent",
        status="completed", requested_at=now, created_at=now)
    lab = SimpleNamespace(result_value="12", unit="g/dL", reference_range="10-15", result_notes="ok", is_critical=True, resulted_at=now)
    db = AsyncMock(); first = _result(all_items=[inv]); second = _result(first=patient); third = _result(first=lab)
    db.execute = AsyncMock(side_effect=[first, second, third])
    results = await router.get_all_investigation_results(db, MagicMock())
    assert results[0].status == "critical" and results[0].result_values == "CBC: 12 g/dL"
    ref_patient = patient
    ref = SimpleNamespace(id=uuid4(), patient=ref_patient, visit_id=vid, referred_to="Hospital", type="external",
        reason="specialist", status="pending", urgency="routine", category="specialist", department=None,
        preferred_doctor=None, hospital_name=None, external_doctor=None, contact_number=None, decline_reason=None,
        referred_at=now, responded_at=None)
    db.execute = AsyncMock(return_value=_result(all_items=[ref]))
    assert (await router.get_all_referrals(db, MagicMock()))[0].patient.full_name == "Jane Doe"
    body = ReferralCreateRequest(patient_id=pid, type="external", referred_to="Hospital", reason="specialist", urgency="routine", category="specialist")
    db.execute = AsyncMock(return_value=_result(first=patient)); db.add = MagicMock()
    created = await router.create_referral(body, db, MagicMock())
    assert created.status == "pending"


@pytest.mark.asyncio
async def test_dashboard_empty_statistics():
    db = AsyncMock(); zero = _result(scalar=0); rows = _result(all_items=[])
    db.execute = AsyncMock(side_effect=[zero, zero, zero, zero, rows, rows, zero, zero, zero, zero])
    stats = await router.get_doctor_dashboard_stats(db, MagicMock())
    assert stats["stats"]["waiting_patients"] == 0
    assert stats["summary"]["referrals_made"] == 0


@pytest.mark.asyncio
async def test_dashboard_populates_priority_queue_and_critical_lab_alert():
    pid, vid, iid = uuid4(), uuid4(), uuid4(); now = datetime.utcnow()
    queue = SimpleNamespace(queue_id=uuid4(), status="waiting", priority="emergency", created_at=now-timedelta(minutes=8))
    visit = SimpleNamespace(visit_id=vid, status="triaged")
    patient = _patient(pid)
    triage = SimpleNamespace(chief_complaint="Chest pain")
    inv = SimpleNamespace(id=iid, patient_id=pid, test_name="Troponin", request_type="lab", status="completed", requested_at=now, created_at=now)
    lab = SimpleNamespace(result_value="9.9", unit="ng/mL", is_critical=True, result_notes="Urgent", resulted_at=now)
    scalar = lambda value: _result(scalar=value)
    queue_rows = MagicMock(); queue_rows.all.return_value = [(queue, visit, patient, triage)]
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[scalar(1), scalar(2), scalar(3), scalar(4), queue_rows, _result(all_items=[inv]), _result(first=patient), _result(first=lab), scalar(5), scalar(6), scalar(7), scalar(8)])
    stats = await router.get_doctor_dashboard_stats(db, MagicMock())
    assert stats["next_patients"][0]["urgency"] == "urgent"
    assert stats["critical_alerts"][-1]["is_highlight"] is True


@pytest.mark.asyncio
async def test_investigation_dashboard_pending_and_critical_radiology_paths():
    pid, vid = uuid4(), uuid4(); now = datetime.utcnow(); patient = _patient(pid)
    rad = SimpleNamespace(id=uuid4(), patient_id=pid, visit_id=vid, test_name="CT", request_type="radiology", urgency="stat", status="completed", requested_at=now, created_at=now)
    lab = SimpleNamespace(id=uuid4(), patient_id=pid, visit_id=vid, test_name="CBC", request_type="lab", urgency="routine", status="pending", requested_at=now, created_at=now)
    report = SimpleNamespace(findings="Severe pneumothorax", impression="critical", reported_at=now)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[_result(all_items=[rad, lab]), _result(first=patient), _result(first=report), _result(first=patient), _result(first=None)])
    rows = await router.get_all_investigation_results(db, MagicMock())
    assert rows[0].status == "critical" and rows[0].result_values == "Severe pneumothorax"
    assert rows[1].status == "pending"


@pytest.mark.asyncio
async def test_investigation_results_skip_missing_patient_and_acknowledge_request():
    inv = SimpleNamespace(id=uuid4(), patient_id=uuid4(), visit_id=uuid4(), test_name="CBC", request_type="lab", urgency="routine", status="acknowledged", requested_at=datetime.utcnow(), created_at=datetime.utcnow())
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[_result(all_items=[inv]), _result(first=None)])
    assert await router.get_all_investigation_results(db, MagicMock()) == []
    db.execute = AsyncMock(return_value=_result(first=inv))
    assert (await router.acknowledge_investigation(inv.id, db, MagicMock()))["status"] == "success"


@pytest.mark.asyncio
async def test_investigation_results_cover_empty_completed_and_unreported_results():
    pid = uuid4(); now = datetime.utcnow(); patient = _patient(pid)
    lab = SimpleNamespace(id=uuid4(), patient_id=pid, visit_id=uuid4(), test_name="CBC", request_type="lab", urgency="routine", status="completed", requested_at=now, created_at=now)
    rad = SimpleNamespace(id=uuid4(), patient_id=pid, visit_id=uuid4(), test_name="Xray", request_type="radiology", urgency="routine", status="completed", requested_at=now, created_at=now)
    pending = SimpleNamespace(id=uuid4(), patient_id=pid, visit_id=uuid4(), test_name="Culture", request_type="laboratory", urgency="routine", status="pending", requested_at=now, created_at=now)
    db = AsyncMock(side_effect=None)
    db.execute = AsyncMock(side_effect=[_result(all_items=[lab, rad, pending]), _result(first=patient), _result(first=None), _result(first=patient), _result(first=None), _result(first=patient), _result(first=None)])
    rows = await router.get_all_investigation_results(db, MagicMock())
    assert [row.status for row in rows] == ["ready", "ready", "pending"]
