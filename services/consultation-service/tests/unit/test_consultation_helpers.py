import uuid
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1 import router
from app.core.security import TokenPayload
from app.api.v1.schemas import (
    NotesUpdateRequest, DiagnosisCreate, DiagnosisUpdateRequest,
    InvestigationRequestCreate, PrescriptionCreate, DispositionUpdateRequest,
    DischargeRequest, ReferralCreateRequest, InpatientOrderCreate, OrderStatusUpdate,
    ConsultationCreate,
)


def _result(first=None, all_items=None):
    result = MagicMock(); result.scalars.return_value.first.return_value = first; result.scalars.return_value.all.return_value = all_items or []
    return result


@pytest.mark.asyncio
async def test_doctor_name_resolution_variants():
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = None
    db.execute.return_value = result
    assert await router._resolve_doctor_name(db, None) == "—"
    assert await router._resolve_doctor_name(db, "  ") == "—"
    assert await router._resolve_doctor_name(db, "doctor-sub") == "doctor-sub"
    result.scalars.return_value.first.return_value = MagicMock(full_name=" Dr Name ", username="d")
    assert await router._resolve_doctor_name(db, "doctor-sub") == "Dr Name"
    result.scalars.return_value.first.return_value = MagicMock(full_name="", first_name="First", last_name="Last", username="d")
    assert await router._resolve_doctor_name(db, "doctor-sub") == "First Last"
    result.scalars.return_value.first.return_value = MagicMock(full_name="", first_name="", last_name="", username="d")
    assert await router._resolve_doctor_name(db, "doctor-sub") == "d"


@pytest.mark.asyncio
async def test_role_dependency_and_queue_number(monkeypatch):
    user = TokenPayload("u", None, None, {"roles": ["doctor"]}, {})
    assert await router.require_any_role(["doctor"])(user) is user
    with pytest.raises(Exception):
        await router.require_any_role(["nurse"])(TokenPayload("u", None, None, {"roles": []}, {}))
    db = AsyncMock()
    result = MagicMock(); result.scalar.return_value = 2; db.execute.return_value = result
    assert await router._generate_queue_number(db, "lab") == "L-003"
    assert await router._generate_queue_number(db, "radiology") == "R-003"
    assert await router._generate_queue_number(db, "doctor") == "P-003"


@pytest.mark.asyncio
async def test_visit_status_transition_success_failure_and_tenant_header(monkeypatch):
    response = MagicMock(status_code=200, text="ok")
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(); client.patch = AsyncMock(return_value=response)
    monkeypatch.setattr(router.httpx, "AsyncClient", lambda **kw: client)
    await router._transition_visit_status("v", "in_consultation", "Bearer t", "dsn")
    request = client.patch.await_args.kwargs
    assert request["headers"]["X-Tenant-DB"] == "dsn"
    response.status_code = 500
    await router._transition_visit_status("v", "completed", "Bearer t")
    client.patch.side_effect = RuntimeError("offline")
    await router._transition_visit_status("v", "completed", "Bearer t")


@pytest.mark.asyncio
async def test_consultation_mutation_not_found_branches():
    empty = MagicMock(); empty.scalars.return_value.first.return_value = None
    db = AsyncMock(); db.execute = AsyncMock(return_value=empty)
    u = uuid.uuid4(); ctx = MagicMock(user_sub="doctor", raw_token={}, roles=["doctor"], is_super_admin=False)
    cases = [
        (router.update_clinical_notes, (u, NotesUpdateRequest(presenting_history="h", examination_findings="e", clinical_impression="i"), db, ctx, None)),
        (router.record_diagnosis, (u, DiagnosisCreate(diagnosis_type="final", description="d"), db, ctx, None)),
        (router.update_diagnosis, (u, u, DiagnosisUpdateRequest(diagnosis_text="d"), db, ctx, None)),
        (router.raise_investigation, (u, InvestigationRequestCreate(request_type="lab", test_name="x", clinical_indication="r"), MagicMock(), db, ctx, None, None)),
        (router.record_prescription, (u, PrescriptionCreate(drug_name="x", dose="1", frequency="daily", duration="1d", route="oral"), db, ctx, None)),
        (router.update_disposition, (u, DispositionUpdateRequest(disposition="outpatient"), db, ctx, None)),
        (router.complete_consultation, (u, MagicMock(), db, ctx, None, None)),
        (router.get_consultation_summary, (u, db, None)),
        (router.create_inpatient_order_route, (u, InpatientOrderCreate(order_type="diet", description="x"), db, None)),
        (router.update_inpatient_order_status, (u, OrderStatusUpdate(status="done"), db, None)),
        (router.discharge_inpatient_patient, (u, DischargeRequest(discharge_diagnosis="d", condition="stable", care_summary="s", instructions="i"), db, None)),
        (router.create_referral, (ReferralCreateRequest(patient_id=u, type="external", referred_to="h", reason="r", urgency="routine", category="specialist"), db, None)),
    ]
    for function, args in cases:
        with pytest.raises(Exception):
            await function(*args)


@pytest.mark.asyncio
async def test_doctor_queue_completed_and_investigation_statuses():
    now = datetime.utcnow()
    visit_id = uuid.uuid4(); patient_id = uuid.uuid4()
    queue = SimpleNamespace(queue_id=uuid.uuid4(), queue_number="D-002", priority="urgent", visit_id=visit_id,
        patient_id=patient_id, status="completed", created_at=now-timedelta(minutes=20), called_at=None,
        completed_at=now-timedelta(minutes=2))
    visit = SimpleNamespace(visit_number="V-2", status="triaged")
    patient = SimpleNamespace(date_of_birth=date(1990, 1, 1), full_name="Patient", patient_number="P-2")
    triage = SimpleNamespace(triage_category="urgent", chief_complaint="pain")
    lab = SimpleNamespace(id=uuid.uuid4(), request_type="lab", status="pending")
    rad = SimpleNamespace(id=uuid.uuid4(), request_type="radiology", status="pending")
    queue_result = MagicMock(); queue_result.all.return_value = [(queue, visit, patient, triage)]
    inv_result = MagicMock(); inv_result.scalars.return_value.all.return_value = [lab, rad]
    lab_result = MagicMock(); lab_result.scalar_one_or_none.return_value = uuid.uuid4()
    rad_result = MagicMock(); rad_result.scalar_one_or_none.return_value = None
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[queue_result, inv_result, lab_result, rad_result])
    result = await router.get_doctor_queue("completed,waiting", db, MagicMock())
    assert result[0].completed_investigations_count == 1
    assert result[0].pending_investigations_count == 1

    completed_inv = SimpleNamespace(id=uuid.uuid4(), request_type="laboratory", status="completed")
    inv_result.scalars.return_value.all.return_value = [completed_inv]
    db.execute = AsyncMock(side_effect=[queue_result, inv_result])
    ready = await router.get_doctor_queue("completed", db, MagicMock())
    assert ready[0].visit_status == "results_ready"


@pytest.mark.asyncio
async def test_investigation_results_include_laboratory_and_radiology_details():
    cid = uuid.uuid4(); vid = uuid.uuid4(); pid = uuid.uuid4(); now = datetime.utcnow()
    lab_inv = SimpleNamespace(id=uuid.uuid4(), visit_id=vid, consultation_id=cid, patient_id=pid,
        request_type="laboratory", test_name="CBC", test_code="CBC", clinical_history="fever",
        status="completed", urgency="routine", requested_by="doctor", requested_at=now)
    rad_inv = SimpleNamespace(id=uuid.uuid4(), visit_id=vid, consultation_id=cid, patient_id=pid,
        request_type="radiology", test_name="X-ray", test_code="XR", clinical_history="pain",
        status="completed", urgency="urgent", requested_by="doctor", requested_at=now)
    lab = SimpleNamespace(result_id=uuid.uuid4(), result_value="normal", unit="", reference_range="normal",
        is_critical=False, result_notes="ok", status="final", resulted_at=now)
    report = SimpleNamespace(report_id=uuid.uuid4(), modality="X-ray", body_part="chest", findings="clear",
        impression="normal", status="final", reported_at=now)
    first = MagicMock(); first.scalars.return_value.all.return_value = [lab_inv, rad_inv]
    second = MagicMock(); second.scalars.return_value.first.return_value = lab
    third = MagicMock(); third.scalars.return_value.first.return_value = report
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[first, second, third])
    result = await router.get_investigations(cid, db, MagicMock())
    assert result[0].result["result_value"] == "normal"
    assert result[1].result["impression"] == "normal"


@pytest.mark.asyncio
async def test_legacy_consultation_create_and_patient_views():
    vid, pid = uuid.uuid4(), uuid.uuid4(); ctx = SimpleNamespace(user_sub="doctor")
    no_cons = MagicMock(); no_cons.scalars.return_value.first.return_value = None
    db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(return_value=no_cons)
    body = ConsultationCreate(visit_id=vid, patient_id=pid, history_of_presenting_illness="history")
    created = await router.create_consultation(MagicMock(headers={}), body, db, ctx, None)
    assert created.visit_id == vid; db.commit.assert_awaited_once()
    existing = SimpleNamespace(visit_id=vid, patient_id=pid, history_of_presenting_illness=None, examination_findings=None, clinical_impression=None, created_by=None, updated_at=None)
    found = MagicMock(); found.scalars.return_value.first.return_value = existing; db.execute = AsyncMock(return_value=found)
    await router.create_consultation(MagicMock(headers={}), body, db, ctx, None); assert existing.created_by == "doctor"
    full_patient = SimpleNamespace(id=pid, full_name="Jane", patient_number="P", date_of_birth=date(1990, 1, 1), gender="f", phone_primary="1", phone_secondary=None, email=None, address=None, allergies=None, blood_group=None, next_of_kin_name=None, next_of_kin_phone=None, next_of_kin_relationship=None)
    db.execute = AsyncMock(side_effect=[_result(first=full_patient), _result(), _result(first=None)])
    view = await router.get_patient_encounter_view(pid, vid, db); assert view.consultation is None
    db.execute = AsyncMock(side_effect=[_result(first=full_patient), _result(), _result(first=created), _result(), _result(), _result()])
    view = await router.get_patient_encounter_view(pid, vid, db); assert view.consultation.id == created.id
    db.execute = AsyncMock(side_effect=[_result(first=full_patient), _result(all_items=[])])
    history = await router.get_patient_history(pid, db); assert history.previous_visits == []

    db.execute = AsyncMock(return_value=_result(first=created))
    assert await router.get_consultation_by_visit(vid, db) is created
    db.execute = AsyncMock(return_value=_result(first=None))
    with pytest.raises(Exception): await router.get_consultation_by_visit(vid, db)
    with pytest.raises(Exception): await router.get_patient_history(pid, db)


@pytest.mark.asyncio
async def test_completed_consultation_summary_without_optional_records():
    cid, vid, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(); now = datetime.utcnow()
    consultation = SimpleNamespace(id=cid, visit_id=vid, patient_id=pid, history_of_presenting_illness="h",
        examination_findings="e", clinical_impression="i", consultation_status="completed", started_at=now,
        completed_at=now, disposition="outpatient", referral_type=None, referral_notes=None, admission_reason=None,
        discharge_instructions=None, follow_up_date=None, return_date=None, return_reason=None, created_by="doctor",
        created_at=now, updated_at=now)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[_result(first=consultation), _result(), _result(), _result()])
    summary = await router.get_consultation_summary(cid, db, MagicMock())
    assert summary.id == cid and summary.diagnoses == [] and summary.prescriptions == []


@pytest.mark.asyncio
async def test_summary_with_laboratory_and_radiology_results_and_history_visit():
    import datetime
    cid, vid, pid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(); now = datetime.datetime.utcnow()
    c = SimpleNamespace(id=cid, visit_id=vid, patient_id=pid, history_of_presenting_illness="h", examination_findings="e", clinical_impression="i", consultation_status="completed", started_at=now, completed_at=now, disposition="outpatient", referral_type=None, referral_notes=None, admission_reason=None, discharge_instructions=None, follow_up_date=None, return_date=None, return_reason=None, created_by="doctor", created_at=now, updated_at=now)
    lab_inv = SimpleNamespace(id=uuid.uuid4(), visit_id=vid, consultation_id=cid, patient_id=pid, request_type="lab", test_name="CBC", test_code="CBC", clinical_history="fever", status="completed", urgency="routine", requested_by="doctor", requested_at=now)
    rad_inv = SimpleNamespace(id=uuid.uuid4(), visit_id=vid, consultation_id=cid, patient_id=pid, request_type="radiology", test_name="Xray", test_code="XR", clinical_history="pain", status="completed", urgency="routine", requested_by="doctor", requested_at=now)
    lab = SimpleNamespace(result_id=uuid.uuid4(), result_value="ok", unit="", reference_range="normal", is_critical=False, result_notes=None, status="final", resulted_at=now)
    rad = SimpleNamespace(report_id=uuid.uuid4(), modality="Xray", body_part="chest", findings="clear", impression="normal", status="final", reported_at=now)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[_result(first=c), _result(), _result(all_items=[lab_inv, rad_inv]), _result(first=lab), _result(first=rad), _result()])
    summary = await router.get_consultation_summary(cid, db, MagicMock())
    assert len(summary.investigation_requests) == 2
    full_patient = SimpleNamespace(id=pid, full_name="Jane", patient_number="P", date_of_birth=datetime.date(1990,1,1), gender="f", phone_primary="1", phone_secondary=None, email=None, address=None, allergies=None, blood_group=None, next_of_kin_name=None, next_of_kin_phone=None, next_of_kin_relationship=None)
    old_visit = SimpleNamespace(visit_id=uuid.uuid4(), visit_date=datetime.date.today(), visit_type="outpatient", status="completed", created_at=now)
    db.execute = AsyncMock(side_effect=[_result(first=full_patient), _result(all_items=[old_visit]), _result(), _result(first=None)])
    history = await router.get_patient_history(pid, db)
    assert len(history.previous_visits) == 1 and history.previous_visits[0].consultation is None
    c.visit_id = old_visit.visit_id
    db.execute = AsyncMock(side_effect=[_result(first=full_patient), _result(all_items=[old_visit]), _result(), _result(first=c), _result(), _result(), _result()])
    history = await router.get_patient_history(pid, db)
    assert history.previous_visits[0].consultation.id == cid
