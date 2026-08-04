import pytest
from uuid import uuid4
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import radiology as radiology_service
from app.exceptions import NotFoundError, BadRequestError, ConflictError


def _user(sub: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        sub=sub or str(uuid4()),
        preferred_username="radio1",
        email="radio@test.com",
        realm_access={"roles": ["radiographer"]},
        raw={},
    )


@pytest.mark.asyncio
async def test_create_report_valid():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    request_id = uuid4()
    req = MagicMock()
    req.request_type = "radiology"
    req.id = request_id

    req_result = MagicMock()
    req_result.scalar_one_or_none.return_value = req
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None

    db.execute = AsyncMock(side_effect=[req_result, existing_result])

    data = {
        "request_id": request_id,
        "visit_id": uuid4(),
        "patient_id": uuid4(),
        "modality": "xray",
        "body_part": "Chest",
        "performed_by": uuid4(),
        "status": "scheduled",
    }

    report = await radiology_service.create_report(db, data)
    assert report.modality == "xray"
    assert report.status == "scheduled"
    assert report.request_id == request_id
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_report_requires_request_id():
    db = AsyncMock()
    with pytest.raises(BadRequestError):
        await radiology_service.create_report(
            db,
            {
                "visit_id": uuid4(),
                "patient_id": uuid4(),
                "modality": "xray",
                "performed_by": uuid4(),
            },
        )


@pytest.mark.asyncio
async def test_create_report_invalid_modality():
    db = AsyncMock()
    data = {
        "request_id": uuid4(),
        "visit_id": uuid4(),
        "patient_id": uuid4(),
        "modality": "pet_scan",
        "performed_by": uuid4(),
        "status": "scheduled",
    }

    with pytest.raises(BadRequestError):
        await radiology_service.create_report(db, data)


@pytest.mark.asyncio
async def test_get_report_not_found():
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    with pytest.raises(NotFoundError):
        await radiology_service.get_report(db, uuid4())


@pytest.mark.asyncio
async def test_list_reports():
    db = AsyncMock()

    count_result = MagicMock()
    count_result.scalar.return_value = 0
    list_result = MagicMock()
    list_result.scalars.return_value.all.return_value = []

    async def execute_side_effect(query):
        if "count" in str(query).lower():
            return count_result
        return list_result

    db.execute = AsyncMock(side_effect=execute_side_effect)

    reports, total = await radiology_service.list_reports(db)
    assert total == 0
    assert reports == []


@pytest.mark.asyncio
async def test_schedule_imaging_creates_report():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    request_id = uuid4()
    req = MagicMock()
    req.id = request_id
    req.request_type = "radiology"
    req.status = "pending"
    req.visit_id = uuid4()
    req.patient_id = uuid4()

    req_result = MagicMock()
    req_result.scalar_one_or_none.return_value = req
    existing_result = MagicMock()
    existing_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[req_result, existing_result])

    performed_by = uuid4()
    report = await radiology_service.schedule_imaging(
        db,
        request_id,
        {
            "modality": "ct",
            "body_part": "Head",
            "scheduled_at": datetime.now(timezone.utc),
            "performed_by": performed_by,
        },
        _user(str(performed_by)),
    )
    assert report.modality == "ct"
    assert report.status == "scheduled"
    db.add.assert_called_once()


@pytest.mark.asyncio
async def test_perform_imaging_requires_existing_report():
    db = AsyncMock()
    request_id = uuid4()
    req = MagicMock()
    req.id = request_id
    req.request_type = "radiology"
    req.status = "pending"

    req_result = MagicMock()
    req_result.scalar_one_or_none.return_value = req
    missing_report = MagicMock()
    missing_report.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(side_effect=[req_result, missing_report])

    with pytest.raises(ConflictError):
        await radiology_service.perform_imaging(db, request_id, {}, _user())


@pytest.mark.asyncio
async def test_enter_report_updates_status_and_publishes():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    request_id = uuid4()
    report_id = uuid4()
    req = MagicMock()
    req.id = request_id
    req.request_type = "radiology"
    req.status = "in_progress"

    report = MagicMock()
    report.report_id = report_id
    report.request_id = request_id
    report.status = "performed"

    req_result = MagicMock()
    req_result.scalar_one_or_none.return_value = req
    report_result = MagicMock()
    report_result.scalar_one_or_none.return_value = report
    db.execute = AsyncMock(side_effect=[req_result, report_result])

    with patch(
        "app.services.radiology.publish_radiology_report_ready",
        new_callable=AsyncMock,
    ) as publish:
        result = await radiology_service.enter_report(
            db,
            request_id,
            {"findings": "Normal", "impression": "No acute findings"},
            _user(),
            tenant_id="hosp-test",
        )
        assert result.status == "reported"
        assert req.status == "completed"
        publish.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_report():
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    report_id = uuid4()
    request_id = uuid4()
    report = MagicMock()
    report.report_id = report_id
    report.request_id = request_id
    report.status = "reported"
    report.reported_by = None

    req = MagicMock()
    req.id = request_id
    req.request_type = "radiology"
    req.status = "completed"

    report_result = MagicMock()
    report_result.scalar_one_or_none.return_value = report
    req_result = MagicMock()
    req_result.scalar_one_or_none.return_value = req
    db.execute = AsyncMock(side_effect=[report_result, req_result])

    with patch(
        "app.services.radiology.publish_radiology_report_ready",
        new_callable=AsyncMock,
    ) as publish:
        result = await radiology_service.verify_report(
            db, report_id, _user(str(uuid4())), tenant_id="hosp-test"
        )
        assert result.status == "verified"
        publish.assert_awaited_once()
