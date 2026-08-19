from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1 import router
from app.api.v1.schemas import (
    DiagnosisCreate, DiagnosisUpdateRequest, DispositionUpdateRequest,
    InvestigationRequestCreate, NotesUpdateRequest, PrescriptionCreate,
)


def result(first=None, all_items=None):
    r = MagicMock(); r.scalars.return_value.first.return_value = first; r.scalars.return_value.all.return_value = all_items or []; return r


def consultation(status="in_progress", owner="doctor", disposition=None):
    now = __import__("datetime").datetime.utcnow()
    return SimpleNamespace(id=uuid4(), visit_id=uuid4(), patient_id=uuid4(), created_by=owner,
        consultation_status=status, disposition=disposition, history_of_presenting_illness=None,
        examination_findings=None, clinical_impression=None, referral_type=None, referral_notes=None,
        admission_reason=None, discharge_instructions=None, follow_up_date=None, return_date=None,
        return_reason=None, started_at=now, completed_at=None, created_at=now, updated_at=now)


def ctx(owner="doctor", roles=None):
    return SimpleNamespace(user_sub=owner, roles=["doctor"] if roles is None else roles, raw_token={}, preferred_username=None, is_super_admin=False, tenant_id="t")


@pytest.mark.asyncio
async def test_completed_and_ownership_gates():
    c = consultation("completed"); db = AsyncMock(); db.execute = AsyncMock(return_value=result(first=c))
    with pytest.raises(Exception): await router.update_clinical_notes(c.id, NotesUpdateRequest(presenting_history="h", examination_findings="e", clinical_impression="i"), db, ctx(), None)
    with pytest.raises(Exception): await router.record_diagnosis(c.id, DiagnosisCreate(diagnosis_type="final", description="d"), db, ctx(), None)
    with pytest.raises(Exception): await router.record_prescription(c.id, PrescriptionCreate(drug_name="x", dose="1", frequency="daily", duration="1", route="oral"), db, ctx())
    c = consultation(owner="other"); db.execute = AsyncMock(return_value=result(first=c))
    with pytest.raises(Exception): await router.update_clinical_notes(c.id, NotesUpdateRequest(presenting_history="h", examination_findings="e", clinical_impression="i"), db, ctx(), None)
    with pytest.raises(Exception): await router.update_disposition(c.id, DispositionUpdateRequest(disposition="outpatient"), db, ctx(), None)
    with pytest.raises(Exception): await router.complete_consultation(c.id, MagicMock(), db, ctx(), None, None)


@pytest.mark.asyncio
async def test_diagnosis_sequence_provisional_and_pending_investigation_gates():
    c = consultation(); db = AsyncMock(); db.add = MagicMock()
    db.execute = AsyncMock(return_value=result(first=c))
    with pytest.raises(Exception): await router.record_diagnosis(c.id, DiagnosisCreate(diagnosis_type="differential", description="d"), db, ctx(), None)
    existing = SimpleNamespace(id=uuid4(), diagnosis_type="provisional", status="active")
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=existing)])
    with pytest.raises(Exception): await router.record_diagnosis(c.id, DiagnosisCreate(diagnosis_type="provisional", description="d"), db, ctx(), None)
    pending = SimpleNamespace(status="pending")
    db.execute = AsyncMock(side_effect=[result(first=c), result(all_items=[pending])])
    with pytest.raises(Exception): await router.record_diagnosis(c.id, DiagnosisCreate(diagnosis_type="final", description="d"), db, ctx(), None)
    diagnosis = SimpleNamespace(id=uuid4(), diagnosis_type="final", recorded_by="other", description="old", code=None, sequence_order=None)
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis)])
    with pytest.raises(Exception): await router.update_diagnosis(c.id, diagnosis.id, DiagnosisUpdateRequest(diagnosis_text="new"), db, ctx(owner="doctor", roles=[]), None)
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[pending])])
    diagnosis.recorded_by = "doctor"
    with pytest.raises(Exception): await router.update_diagnosis(c.id, diagnosis.id, DiagnosisUpdateRequest(diagnosis_text="new"), db, ctx(), None)


@pytest.mark.asyncio
async def test_diagnosis_update_listing_and_deletion_success_paths():
    c = consultation(); diagnosis = SimpleNamespace(id=uuid4(), diagnosis_type="provisional", recorded_by="doctor", code=None, description="old", sequence_order=None)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis)])
    updated = await router.update_diagnosis(c.id, diagnosis.id, DiagnosisUpdateRequest(diagnosis_text="new", icd10_code="A00", sequence_order=2), db, ctx(), None)
    assert updated.description == "new" and updated.code == "A00"
    db.execute = AsyncMock(return_value=result(all_items=[diagnosis]))
    assert await router.get_diagnoses(c.id, db, None) == [diagnosis]
    db.execute = AsyncMock(return_value=result(first=diagnosis)); db.delete = AsyncMock()
    assert (await router.delete_diagnosis(diagnosis.id, db, ctx(), None))["message"].startswith("Diagnosis")


@pytest.mark.asyncio
async def test_disposition_validation_gates():
    c = consultation(); diagnosis = SimpleNamespace(id=uuid4())
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[])])
    for body in [DispositionUpdateRequest(disposition="invalid"), DispositionUpdateRequest(disposition="admission"), DispositionUpdateRequest(disposition="referral"), DispositionUpdateRequest(disposition="return_visit")]:
        db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[])])
        with pytest.raises(Exception): await router.update_disposition(c.id, body, db, ctx(), None)
    pending = SimpleNamespace(status="pending")
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[pending])])
    with pytest.raises(Exception): await router.update_disposition(c.id, DispositionUpdateRequest(disposition="outpatient"), db, ctx(), None)


@pytest.mark.asyncio
async def test_disposition_records_each_supported_workflow_and_parses_dates():
    diagnosis = SimpleNamespace(id=uuid4())
    cases = [
        DispositionUpdateRequest(disposition="admission", admission_reason="Needs observation"),
        DispositionUpdateRequest(disposition="referral", referral_type="Cardiology", referral_notes="Specialist review"),
        DispositionUpdateRequest(disposition="return_visit", return_date="2026-09-10", return_reason="Review results"),
        DispositionUpdateRequest(disposition="deceased", discharge_instructions="Family informed"),
    ]
    for body in cases:
        c = consultation(); db = AsyncMock()
        db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[])])
        updated = await router.update_disposition(c.id, body, db, ctx(), None)
        assert updated.disposition == body.disposition
        assert db.commit.await_count == 1

    c = consultation(); db = AsyncMock()
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[])])
    with pytest.raises(Exception):
        await router.update_disposition(c.id, DispositionUpdateRequest(disposition="outpatient", follow_up_date="bad"), db, ctx(), None)


@pytest.mark.asyncio
async def test_investigation_and_prescription_state_gates():
    c = consultation("completed"); db = AsyncMock(); body = InvestigationRequestCreate(request_type="lab", test_name="CBC", clinical_indication="fever")
    db.execute = AsyncMock(return_value=result(first=c))
    with pytest.raises(Exception): await router.raise_investigation(c.id, body, MagicMock(), db, ctx(), None, None)
    inv = SimpleNamespace(id=uuid4(), requested_by="other", status="pending", visit_id=uuid4(), request_type="lab", consultation_id=c.id)
    db.execute = AsyncMock(return_value=result(first=inv))
    with pytest.raises(Exception): await router.cancel_investigation(inv.id, MagicMock(), db, ctx(roles=[]), None, None)
    pres = SimpleNamespace(id=uuid4(), prescribed_by="other", status="pending")
    db.execute = AsyncMock(return_value=result(first=pres))
    with pytest.raises(Exception): await router.cancel_prescription(pres.id, db, ctx(roles=[]), None)
    pres.status = "dispensed"; db.execute = AsyncMock(return_value=result(first=pres))
    with pytest.raises(Exception): await router.cancel_prescription(pres.id, db, ctx(), None)


@pytest.mark.asyncio
async def test_investigation_cancellation_rejects_non_pending_and_acknowledges_result():
    inv = SimpleNamespace(id=uuid4(), requested_by="doctor", status="completed")
    db = AsyncMock(); db.execute = AsyncMock(return_value=result(first=inv))
    with pytest.raises(Exception):
        await router.cancel_investigation(inv.id, MagicMock(), db, ctx(), None, None)
    db.execute = AsyncMock(return_value=result(first=inv))
    assert (await router.acknowledge_investigation(inv.id, db, None))["status"] == "success"


@pytest.mark.asyncio
async def test_successful_investigation_prescription_cancellation_and_disposition(monkeypatch):
    c = consultation(); visit = SimpleNamespace(visit_id=c.visit_id, patient_id=c.patient_id, payment_type="cash", status="in_consultation", updated_at=None)
    diagnosis = SimpleNamespace(id=uuid4()); count = MagicMock(); count.scalar.return_value = 0
    none = result(); db = AsyncMock(); db.add = MagicMock();
    monkeypatch.setattr("app.events.publisher.publish_investigation_requested", AsyncMock())
    db.execute = AsyncMock(side_effect=[result(first=c), result(first=visit), result(first=diagnosis), count, none, result(first=None)])
    inv = await router.raise_investigation(c.id, InvestigationRequestCreate(request_type="lab", test_name="CBC", clinical_indication="fever"), MagicMock(headers={}), db, ctx(), None, None)
    assert inv.status == "pending" and visit.status == "awaiting_results"
    db.execute = AsyncMock(side_effect=[result(first=c), none, count])
    monkeypatch.setattr("app.events.publisher.publish_prescription_issued", AsyncMock())
    prescription = await router.record_prescription(c.id, PrescriptionCreate(drug_name="Drug", dose="1", frequency="daily", duration="1d", route="oral"), db, ctx())
    assert prescription.status == "pending"
    inv_obj = SimpleNamespace(id=uuid4(), requested_by="doctor", status="pending", visit_id=c.visit_id, request_type="lab", consultation_id=c.id)
    q_obj = SimpleNamespace(status="waiting", completed_at=None)
    visit_obj = SimpleNamespace(status="awaiting_results", updated_at=None)
    db.execute = AsyncMock(side_effect=[result(first=inv_obj), result(first=q_obj), result(all_items=[inv_obj]), result(first=visit_obj)])
    cancelled = await router.cancel_investigation(inv_obj.id, MagicMock(headers={}), db, ctx(), None, None)
    assert cancelled["status"] == "cancelled" and q_obj.status == "skipped"
    pres_obj = SimpleNamespace(id=uuid4(), prescribed_by="doctor", status="pending")
    db.execute = AsyncMock(return_value=result(first=pres_obj)); cancelled = await router.cancel_prescription(pres_obj.id, db, ctx(), None)
    assert cancelled["status"] == "cancelled"
    c.disposition = None; db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[])])
    updated = await router.update_disposition(c.id, DispositionUpdateRequest(disposition="outpatient", follow_up_date="2026-01-02"), db, ctx(), None)
    assert updated.disposition == "outpatient"


@pytest.mark.asyncio
async def test_successful_admission_completion_path():
    c = consultation(disposition="admission"); visit = SimpleNamespace(visit_id=c.visit_id, status="in_consultation", updated_at=None); diagnosis = SimpleNamespace(id=uuid4())
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[result(first=c), result(first=diagnosis), result(all_items=[]), result(first=visit), result(first=None)])
    response = await router.complete_consultation(c.id, MagicMock(headers={}), db, ctx(), None, None)
    assert response.visit_status == "admitted" and c.consultation_status == "completed"


@pytest.mark.asyncio
async def test_open_encounter_validation_branches():
    vid = uuid4(); request = MagicMock(headers={}); credentials = None
    db = AsyncMock(); db.execute = AsyncMock(return_value=result())
    with pytest.raises(Exception): await router.open_encounter(vid, request, db, ctx(), credentials, None)
    visit = SimpleNamespace(visit_id=vid, patient_id=uuid4(), status="completed")
    db.execute = AsyncMock(return_value=result(first=visit))
    with pytest.raises(Exception): await router.open_encounter(vid, request, db, ctx(), credentials, None)
    visit.status = "triaged"; existing = SimpleNamespace(id=uuid4())
    db.execute = AsyncMock(side_effect=[result(first=visit), result(first=existing)])
    with pytest.raises(Exception): await router.open_encounter(vid, request, db, ctx(), credentials, None)
    db.execute = AsyncMock(side_effect=[result(first=visit), result(), result(first=None)])
    with pytest.raises(Exception): await router.open_encounter(vid, request, db, ctx(), credentials, None)


@pytest.mark.asyncio
async def test_encounter_missing_related_records():
    vid = uuid4(); db = AsyncMock(); db.execute = AsyncMock(return_value=result())
    with pytest.raises(Exception): await router.get_encounter(vid, db, MagicMock())


@pytest.mark.asyncio
async def test_encounter_view_with_lab_and_radiology_results():
    import datetime
    c = consultation(); c.visit_id = uuid4(); c.patient_id = uuid4(); now = datetime.datetime.utcnow()
    patient = SimpleNamespace(id=c.patient_id, patient_number="P", full_name="Jane Doe", date_of_birth=datetime.date(1990,1,1), gender="f", phone_primary="1", phone_secondary=None, email=None, address=None, allergies=None, blood_group=None, next_of_kin_name=None, next_of_kin_phone=None, next_of_kin_relationship=None)
    lab_inv = SimpleNamespace(id=uuid4(), visit_id=c.visit_id, consultation_id=c.id, patient_id=c.patient_id, request_type="lab", test_name="CBC", test_code="CBC", clinical_history="fever", status="completed", urgency="routine", requested_by="doctor", requested_at=now)
    rad_inv = SimpleNamespace(id=uuid4(), visit_id=c.visit_id, consultation_id=c.id, patient_id=c.patient_id, request_type="radiology", test_name="Xray", test_code="XR", clinical_history="pain", status="completed", urgency="routine", requested_by="doctor", requested_at=now)
    lab = SimpleNamespace(result_id=uuid4(), result_value="ok", unit="", reference_range="normal", is_critical=False, result_notes=None, status="final", resulted_at=now)
    rad = SimpleNamespace(report_id=uuid4(), modality="Xray", body_part="chest", findings="clear", impression="normal", status="final", reported_at=now)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[result(first=c), result(first=patient), result(), result(), result(all_items=[lab_inv, rad_inv]), result(first=lab), result(first=rad), result(all_items=[]), result(all_items=[])])
    response = await router.get_encounter(c.visit_id, db, MagicMock())
    assert response.consultation.investigation_requests[0].result["result_value"] == "ok"
    assert response.consultation.investigation_requests[1].result["impression"] == "normal"


@pytest.mark.asyncio
async def test_open_encounter_credentials_queue_fee_and_existing_bill(monkeypatch):
    import datetime
    vid, pid = uuid4(), uuid4(); now = datetime.datetime.utcnow()
    visit = SimpleNamespace(visit_id=vid, patient_id=pid, status="triaged", payment_type="insurance", updated_at=None)
    patient = SimpleNamespace(id=pid, patient_number="P", full_name="Jane Doe", date_of_birth=datetime.date(1990,1,1), gender="f", phone_primary="1", phone_secondary=None, email=None, address=None, allergies=None, blood_group=None, next_of_kin_name=None, next_of_kin_phone=None, next_of_kin_relationship=None)
    queue = SimpleNamespace(status="waiting", called_at=None)
    fee = SimpleNamespace(insurance_price=50, standard_price=100)
    bill = SimpleNamespace(bill_id=uuid4(), total_amount=10)
    db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(side_effect=[result(first=visit), result(), result(first=patient), result(), result(all_items=[]), result(first=queue), result(first=fee), result(first=bill)])
    monkeypatch.setattr(router, "_transition_visit_status", AsyncMock())
    request = MagicMock(); request.headers = {"x-tenant-db": "dsn"}; credentials = router.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    response = await router.open_encounter(vid, request, db, ctx(), credentials, None)
    assert response.consultation_status == "in_progress" and queue.status == "in_progress" and bill.total_amount == 60
    c = consultation(); db.execute = AsyncMock(side_effect=[result(first=c), result(first=None)])
    with pytest.raises(Exception): await router.get_encounter(vid, db, MagicMock())
