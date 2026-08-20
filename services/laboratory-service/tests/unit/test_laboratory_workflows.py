from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.api.v1.schemas import (
    LabBillCreateRequest, ResultCreateRequest, ResultUpdateRequest,
    SpecimenCreateRequest, SpecimenStatusUpdateRequest,
)
from app.services import laboratory as service
from app.core.security import TokenPayload


def scalar(value):
    r = MagicMock(); r.scalar_one_or_none.return_value = value; r.scalar.return_value = value; r.scalars.return_value.first.return_value = value
    return r


def user():
    return TokenPayload("doctor", "Dr. Lab", "doctor@example.com", {"roles": []}, {"tenant_id": "tenant"})


@pytest.mark.asyncio
async def test_request_queue_and_detail_include_patient_user_specimen_and_result():
    rid, pid, vid = uuid4(), uuid4(), uuid4(); now = datetime.now(timezone.utc)
    req = SimpleNamespace(id=rid, patient_id=pid, visit_id=vid, test_name="CBC", test_code="CBC", clinical_history="fever", urgency="stat", status="pending", requested_by="doctor", created_by=None, requested_at=now, created_at=now)
    pat = SimpleNamespace(id=pid, full_name="Jane Doe", patient_number="P-1", date_of_birth=now.date(), gender="female")
    usr = SimpleNamespace(full_name="Dr Lab")
    db = AsyncMock(); rows = MagicMock(); rows.all.return_value = [(req, pat, usr)]
    db.execute = AsyncMock(side_effect=[rows])
    listed = await service.get_lab_requests(db, status="pending", urgency="stat", date_filter=now.date())
    assert listed[0]["requested_by_name"] == "Dr Lab"
    spec = SimpleNamespace(specimen_id=uuid4(), status="collected", specimen_type="blood", collected_at=now, received_at=None, rejection_reason=None)
    result = SimpleNamespace(result_id=uuid4(), status="resulted", result_value="normal", unit=None, reference_range=None, is_critical=False, resulted_at=now)
    row = MagicMock(); row.one_or_none.return_value = (req, pat)
    db.execute = AsyncMock(side_effect=[row, scalar(usr), scalar(spec), scalar(result)])
    detail = await service.get_lab_request_detail(db, rid)
    assert detail["specimen"]["status"] == "collected" and detail["result"]["result_value"] == "normal"


@pytest.mark.asyncio
async def test_request_queue_resolves_missing_patient_and_user_identifier():
    rid, vid, pid = uuid4(), uuid4(), uuid4()
    req = SimpleNamespace(id=rid, patient_id=pid, visit_id=vid, test_name="Glucose", test_code=None, clinical_history=None, urgency=None, status="pending", requested_by=str(uuid4()), created_by=None, requested_at=None, created_at=datetime.now(timezone.utc))
    row = MagicMock(); row.all.return_value = [(req, None, None)]
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[row, scalar(None)])
    listed = await service.get_lab_requests(db)
    assert listed[0]["patient_name"] == "Patient" and listed[0]["requested_by_name"] == "Doctor"
    db.execute = AsyncMock(return_value=MagicMock(one_or_none=MagicMock(return_value=None)))
    with pytest.raises(Exception): await service.get_lab_request_detail(db, rid)


@pytest.mark.asyncio
async def test_user_name_resolution_and_specimen_listing_variants():
    db = AsyncMock(); assert await service._resolve_user_name(db, None) is None; assert await service._resolve_user_name(db, "  ") is None
    found = SimpleNamespace(full_name="", first_name="Jane", last_name="Doe")
    db.execute = AsyncMock(return_value=scalar(found))
    assert await service._resolve_user_name(db, uuid4()) == "Jane Doe"
    found.first_name = ""; found.last_name = ""; db.execute = AsyncMock(return_value=scalar(found))
    assert await service._resolve_user_name(db, "short-id") == "short-id"
    sid, rid, pid = uuid4(), uuid4(), uuid4(); now = datetime.now(timezone.utc)
    spec = SimpleNamespace(specimen_id=sid, request_id=rid, patient_id=pid, specimen_type="blood", collection_site="arm", specimen_label="B1", collected_by="doctor", collected_at=now, received_at=None, status="collected", rejection_reason=None)
    db.execute = AsyncMock(side_effect=[MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[spec])))), scalar(SimpleNamespace(full_name="Dr Lab"))])
    assert (await service.get_specimens_for_request(db, rid))[0]["collected_by_name"] == "Dr Lab"
    patient = SimpleNamespace(id=pid, full_name="Jane", patient_number="P-1")
    req = SimpleNamespace(id=rid, test_name="CBC", urgency=None)
    rows = MagicMock(); rows.all.return_value = [(spec, req, patient)]
    db.execute = AsyncMock(side_effect=[rows, scalar(SimpleNamespace(full_name="Dr Lab"))])
    assert (await service.get_all_tracked_specimens(db))[0]["patient_number"] == "P-1"
    db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.update_specimen_status(db, rid, SpecimenStatusUpdateRequest(status="received"), user())


@pytest.mark.asyncio
async def test_specimen_collection_rejection_and_status_transitions():
    rid = uuid4(); req = SimpleNamespace(id=rid, patient_id=uuid4(), status="pending")
    body = SpecimenCreateRequest(specimen_type="blood", collected_at=datetime.now(timezone.utc))
    db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(side_effect=[scalar(req), scalar(None)])
    specimen = await service.collect_specimen(db, rid, body, user())
    assert specimen.status == "collected" and req.status == "specimen_collected"
    active = SimpleNamespace(specimen_id=uuid4(), status="collected", rejection_reason=None, received_at=None, updated_at=None)
    for status in ["received", "processing", "completed"]:
        req.status = "processing"; db.execute = AsyncMock(side_effect=[scalar(active), scalar(req)])
        updated = await service.update_specimen_status(db, rid, SpecimenStatusUpdateRequest(status=status), user())
        assert updated["status"] == status
    req.status = "specimen_collected"; db.execute = AsyncMock(side_effect=[scalar(active), scalar(req)])
    rejected = await service.update_specimen_status(db, rid, SpecimenStatusUpdateRequest(status="rejected", rejection_reason="Clotted"), user())
    assert rejected["request_status"] == "pending"
    db.execute = AsyncMock(side_effect=[scalar(active), scalar(req)])
    with pytest.raises(Exception): await service.update_specimen_status(db, rid, SpecimenStatusUpdateRequest(status="rejected"), user())


@pytest.mark.asyncio
async def test_specimen_collection_rejects_missing_nonpending_and_duplicate_requests():
    rid = uuid4(); body = SpecimenCreateRequest(specimen_type="blood", collected_at=datetime.now(timezone.utc))
    db = AsyncMock(); db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.collect_specimen(db, rid, body, user())
    req = SimpleNamespace(id=rid, patient_id=uuid4(), status="completed")
    db.execute = AsyncMock(return_value=scalar(req))
    with pytest.raises(Exception): await service.collect_specimen(db, rid, body, user())
    req.status = "pending"; db.execute = AsyncMock(side_effect=[scalar(req), scalar(SimpleNamespace())])
    with pytest.raises(Exception): await service.collect_specimen(db, rid, body, user())


@pytest.mark.asyncio
async def test_result_creation_updates_request_and_publishes_critical_event(monkeypatch):
    rid = uuid4(); req = SimpleNamespace(id=rid, visit_id=uuid4(), patient_id=uuid4(), status="specimen_collected", requested_by="doc", test_name="CBC", created_by=None)
    body = ResultCreateRequest(result_value="12", unit="g/dL", is_critical=True)
    db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(side_effect=[scalar(req), scalar(None)])
    publish = AsyncMock(); monkeypatch.setattr("app.events.publisher.publish_lab_critical_value", publish)
    result = await service.create_lab_result(db, rid, body, user())
    assert result.status == "resulted" and req.status == "in_progress"; publish.assert_awaited_once()
    db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.create_lab_result(db, rid, body, user())
    req.status = "pending"; db.execute = AsyncMock(return_value=scalar(req))
    with pytest.raises(Exception): await service.create_lab_result(db, rid, body, user())
    req.status = "in_progress"; existing = SimpleNamespace(result_id=uuid4()); db.execute = AsyncMock(side_effect=[scalar(req), scalar(existing)])
    with pytest.raises(Exception): await service.create_lab_result(db, rid, body, user())


@pytest.mark.asyncio
async def test_result_update_and_read_cover_optional_amendments():
    rid = uuid4(); result = SimpleNamespace(result_id=uuid4(), request_id=rid, status="resulted", result_value="1", unit=None, reference_range=None, result_notes=None, is_critical=False, critical_notified_at=None, performed_by="doc", verified_by="ver", resulted_at=datetime.now(timezone.utc))
    db = AsyncMock(); db.execute = AsyncMock(return_value=scalar(result))
    updated = await service.update_lab_result(db, rid, ResultUpdateRequest(result_value="2", unit="mg", reference_range="0-3", result_notes="ok", is_critical=True), user())
    assert updated["is_critical"] is True
    db.execute = AsyncMock(return_value=scalar(SimpleNamespace(status="verified")))
    with pytest.raises(Exception): await service.update_lab_result(db, rid, ResultUpdateRequest(), user())
    db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.get_lab_result_by_request(db, rid)
    result.specimen_type = "blood"; result.specimen_label = "S1"
    db.execute = AsyncMock(side_effect=[scalar(result), scalar(None), scalar(None)])
    detail = await service.get_lab_result_by_request(db, rid)
    assert detail["performed_by_name"] is not None
    result.is_critical = True; result.critical_notified_at = datetime.now(timezone.utc)
    db.execute = AsyncMock(return_value=scalar(result))
    lowered = await service.update_lab_result(db, rid, ResultUpdateRequest(is_critical=False), user())
    assert lowered["is_critical"] is False


@pytest.mark.asyncio
async def test_result_verification_and_notification_update_related_records(monkeypatch):
    result = SimpleNamespace(result_id=uuid4(), request_id=uuid4(), status="resulted", is_critical=False, verified_by=None, verified_at=None, updated_at=None, patient_id=uuid4())
    req = SimpleNamespace(status="specimen_collected", test_name="CBC", requested_by="doctor")
    spec = SimpleNamespace(status="received", updated_at=None)
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[scalar(result), scalar(req), scalar(spec)])
    monkeypatch.setattr("app.events.publisher.publish_lab_result_ready", AsyncMock())
    verified = await service.verify_lab_result(db, result.result_id, user(), tenant_id="tenant")
    assert verified["status"] == "verified" and req.status == "completed" and spec.status == "completed"
    result.status = "completed"; db.execute = AsyncMock(return_value=scalar(result))
    with pytest.raises(Exception): await service.verify_lab_result(db, result.result_id, user())
    db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.notify_doctor_for_result(db, result.result_id, user())
    result.status = "resulted"; db.execute = AsyncMock(side_effect=[scalar(result), scalar(None), scalar(None)])
    await service.verify_lab_result(db, result.result_id, user(), tenant_id="default")
    result.status = "resulted"
    monkeypatch.setattr("app.events.publisher.publish_lab_result_ready", AsyncMock(side_effect=RuntimeError("broker down")))
    db.execute = AsyncMock(side_effect=[scalar(result), scalar(None), scalar(None)])
    await service.verify_lab_result(db, result.result_id, user(), tenant_id="default")
    notification_req = SimpleNamespace(test_name="CBC", requested_by="doctor")
    db.execute = AsyncMock(side_effect=[scalar(result), scalar(notification_req)])
    notified = await service.notify_doctor_for_result(db, result.result_id, user(), tenant_id="tenant")
    assert "Doctor notified" in notified["message"]


@pytest.mark.asyncio
async def test_lab_billing_and_verified_visit_results():
    rid, bid = uuid4(), uuid4(); req = SimpleNamespace(id=rid, visit_id=uuid4(), status="completed")
    bill = SimpleNamespace(bill_id=bid, total_amount=10.0, status="open")
    body = LabBillCreateRequest(unit_price=5, description="CBC")
    db = AsyncMock(); db.add = MagicMock(); db.execute = AsyncMock(side_effect=[scalar(req), scalar(None), scalar(bill)])
    created = await service.create_lab_bill(db, rid, body, user())
    assert created["total_price"] == 5 and bill.total_amount == 15
    db.execute = AsyncMock(side_effect=[scalar(req), scalar(SimpleNamespace())])
    with pytest.raises(Exception): await service.create_lab_bill(db, rid, body, user())
    req.status = "pending"; db.execute = AsyncMock(return_value=scalar(req))
    with pytest.raises(Exception): await service.create_lab_bill(db, rid, body, user())
    db.execute = AsyncMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    assert (await service.get_visit_verified_results(db, req.visit_id))["results"] == []
    db.execute = AsyncMock(return_value=scalar(None))
    with pytest.raises(Exception): await service.create_lab_bill(db, rid, body, user())
    req.status = "completed"; db.execute = AsyncMock(side_effect=[scalar(req), scalar(None), scalar(None)])
    with pytest.raises(Exception): await service.create_lab_bill(db, rid, body, user())
    db.execute = AsyncMock(side_effect=[scalar(req), scalar(None), scalar(None)])
    # An absent open bill follows the explicit billing failure path.
    with pytest.raises(Exception): await service.create_lab_bill(db, rid, body, user())


@pytest.mark.asyncio
async def test_dashboard_reports_priority_critical_completed_and_turnaround_metrics():
    now = datetime.now(timezone.utc); rid = uuid4(); pid = uuid4()
    req = SimpleNamespace(id=rid, patient_id=pid, test_name="CBC", requested_by="doctor", created_by=None,
        urgency="stat", status="pending", requested_at=now, created_at=now)
    patient = SimpleNamespace(full_name="Jane Doe")
    lr = SimpleNamespace(result_id=uuid4(), patient_id=pid, request_id=rid, result_value="12", unit="g/dL",
        reference_range="10-15", is_critical=True, resulted_at=now, created_at=now, status="verified", performed_by="doctor")
    db = AsyncMock(); counts = [1, 2, 3, 4]
    high = MagicMock(); high.all.return_value = [(req, patient)]
    critical = MagicMock(); critical.all.return_value = [(lr, patient, req)]
    completed = MagicMock(); completed.all.return_value = [(req, lr)]
    tat = MagicMock(); tat.all.return_value = [(req, lr)]
    db.execute = AsyncMock(side_effect=[*(scalar(x) for x in counts), high, scalar(SimpleNamespace(full_name="Dr Lab")), critical, completed, tat])
    stats = await service.get_dashboard_stats(db)
    assert stats["pending_tests"] == 1
    assert stats["high_priority_requests"][0]["patientName"] == "Jane Doe"
    assert stats["critical_values_list"][0]["result"] == "12 g/dL"
    assert stats["completed_today_list"]
    assert stats["turnaround_metrics"][0]["minutes"] >= 1


@pytest.mark.asyncio
async def test_dashboard_handles_naive_dates_and_department_turnaround_categories():
    from datetime import timedelta
    now = datetime.now(); base = now - timedelta(minutes=30)
    def request(name, urgency):
        return SimpleNamespace(id=uuid4(), patient_id=uuid4(), test_name=name, requested_by=None, created_by=None, urgency=urgency, status="completed", requested_at=base, created_at=base)
    def result_for(req):
        return SimpleNamespace(result_id=uuid4(), request_id=req.id, patient_id=req.patient_id, result_value="ok", unit=None, reference_range=None, is_critical=False, resulted_at=now, created_at=now, status="verified", performed_by=None)
    c = lambda value: scalar(value)
    empty_rows = MagicMock(); empty_rows.all.return_value = []
    completed_req = request("Routine", "routine")
    completed_rows = MagicMock(); completed_rows.all.return_value = [(completed_req, None)]
    tat_rows = MagicMock(); tat_rows.all.return_value = [(request("Urine culture", "emergency"), result_for(request("Urine culture", "emergency"))), (request("Glucose", "routine"), result_for(request("Glucose", "routine"))), (request("Hemoglobin", "stat"), result_for(request("Hemoglobin", "stat")))]
    db = AsyncMock(); db.execute = AsyncMock(side_effect=[c(0), c(0), c(0), c(0), empty_rows, empty_rows, completed_rows, tat_rows])
    report = await service.get_dashboard_stats(db)
    assert report["completed_today_list"] and report["turnaround_metrics"][0]["minutes"] >= 1
