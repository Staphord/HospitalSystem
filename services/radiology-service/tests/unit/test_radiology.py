import pytest
from uuid import uuid4
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.services import radiology as radiology_service
from app.exceptions import NotFoundError, BadRequestError


@pytest.mark.asyncio
async def test_create_report_valid():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    data = {
        "request_id": uuid4(),
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
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


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
