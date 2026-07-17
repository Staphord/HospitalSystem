"""Unit tests for new orchestrator functions added in the reception module.

Because all orchestrator functions make HTTP calls via httpx, the HTTP layer
is replaced with a lightweight AsyncMock so tests run without any live services.
"""
import asyncio
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.orchestrator import (
    add_insurance_policy,
    get_insurance_policies,
    get_reception_queue,
    get_visit_detail,
    register_patient,
    search_patients,
    verify_insurance_policy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(headers=None):
    """Build a minimal Starlette-like Request mock."""
    req = MagicMock()
    req.headers.items.return_value = (headers or {}).items()
    req.query_params = MagicMock()
    str(req.query_params)   # ensure __str__ works
    req.query_params.__str__ = MagicMock(return_value="")
    return req


def _patient_fixture(patient_id=None):
    pid = patient_id or str(uuid.uuid4())
    return {
        "id": pid,
        "patient_number": "PAT-0001",
        "full_name": "Jane Doe",
        "date_of_birth": "1990-01-15",
        "gender": "female",
        "phone_primary": "0712345678",
        "created_at": "2024-01-01T08:00:00",
    }


def _insurance_fixture(patient_id=None, insurance_id=None):
    return {
        "insurance_id": insurance_id or str(uuid.uuid4()),
        "patient_id": patient_id or str(uuid.uuid4()),
        "insurer_name": "AAR Insurance",
        "policy_number": "AAR-001",
        "verification_status": "pending",
        "is_active": True,
        "created_at": "2024-01-01T08:00:00",
    }


def _visit_fixture(patient_id=None, insurance_id=None):
    return {
        "visit_id": str(uuid.uuid4()),
        "patient_id": patient_id or str(uuid.uuid4()),
        "visit_number": "VIS-20240101-0001",
        "visit_date": "2024-01-01",
        "visit_type": "outpatient",
        "payment_type": "cash",
        "insurance_id": insurance_id,
        "status": "registered",
        "queue_number": "T-001",
        "registered_by": str(uuid.uuid4()),
        "created_at": "2024-01-01T08:00:00",
    }


def _queue_fixture(patient_id=None, visit_id=None):
    return {
        "queue_id": str(uuid.uuid4()),
        "visit_id": visit_id or str(uuid.uuid4()),
        "patient_id": patient_id or str(uuid.uuid4()),
        "queue_type": "triage",
        "queue_number": "T-001",
        "priority": "non_urgent",
        "status": "waiting",
        "created_at": "2024-01-01T08:00:00",
    }


# ---------------------------------------------------------------------------
# register_patient
# ---------------------------------------------------------------------------

class TestRegisterPatient:
    @pytest.mark.asyncio
    async def test_successful_registration(self):
        patient = _patient_fixture()
        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(201, patient))):
            result = await register_patient(MagicMock(model_dump=MagicMock(return_value={})), _make_request())
        assert result["id"] == patient["id"]

    @pytest.mark.asyncio
    async def test_duplicate_national_id_returns_422(self):
        conflict = {"detail": "Patient with this national_id already exists"}
        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(409, conflict))):
            from fastapi import HTTPException
            with pytest.raises(HTTPException) as exc:
                await register_patient(MagicMock(model_dump=MagicMock(return_value={})), _make_request())
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_downstream_error_propagates(self):
        from fastapi import HTTPException
        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(500, {"detail": "DB error"}))):
            with pytest.raises(HTTPException) as exc:
                await register_patient(MagicMock(model_dump=MagicMock(return_value={})), _make_request())
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# search_patients
# ---------------------------------------------------------------------------

class TestSearchPatients:
    @pytest.mark.asyncio
    async def test_first_page_params(self):
        """Verify page=1, page_size=20 maps to limit=20, offset=0."""
        captured = {}

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            captured.update(extra_params or {})
            return {"patients": [], "total": 0}

        with patch("app.services.orchestrator._forward", new=fake_forward):
            await search_patients(_make_request(), search=None, page=1, page_size=20)

        assert captured["limit"] == 20
        assert captured["offset"] == 0

    @pytest.mark.asyncio
    async def test_second_page_offset(self):
        captured = {}

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            captured.update(extra_params or {})
            return {"patients": [], "total": 0}

        with patch("app.services.orchestrator._forward", new=fake_forward):
            await search_patients(_make_request(), search="Jane", page=2, page_size=10)

        assert captured["limit"] == 10
        assert captured["offset"] == 10
        assert captured["query"] == "Jane"

    @pytest.mark.asyncio
    async def test_page_size_capped_at_100(self):
        captured = {}

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            captured.update(extra_params or {})
            return {"patients": [], "total": 0}

        with patch("app.services.orchestrator._forward", new=fake_forward):
            await search_patients(_make_request(), page=1, page_size=500)

        assert captured["limit"] == 100


# ---------------------------------------------------------------------------
# add_insurance_policy
# ---------------------------------------------------------------------------

class TestAddInsurancePolicy:
    @pytest.mark.asyncio
    async def test_success(self):
        patient_id = str(uuid.uuid4())
        insurance = _insurance_fixture(patient_id=patient_id)
        patient = _patient_fixture(patient_id=patient_id)

        body = MagicMock()
        body.model_dump.return_value = {"insurer_name": "AAR", "policy_number": "AAR-001"}

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            if "patients" in path and method == "GET":
                return (200, patient)
            return (201, insurance)

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
            with patch("app.services.orchestrator._forward", new=AsyncMock(return_value=insurance)):
                result = await add_insurance_policy(patient_id, body, _make_request())

        assert result["insurer_name"] == "AAR Insurance"

    @pytest.mark.asyncio
    async def test_patient_not_found_raises_422(self):
        from fastapi import HTTPException
        body = MagicMock()
        body.model_dump.return_value = {}

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(404, {"detail": "Not found"}))):
            with pytest.raises(HTTPException) as exc:
                await add_insurance_policy("nonexistent-id", body, _make_request())
        assert exc.value.status_code == 422
        assert "does not exist" in exc.value.detail


# ---------------------------------------------------------------------------
# get_insurance_policies
# ---------------------------------------------------------------------------

class TestGetInsurancePolicies:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        patient_id = str(uuid.uuid4())
        policies = [_insurance_fixture(patient_id=patient_id), _insurance_fixture(patient_id=patient_id)]

        with patch("app.services.orchestrator._forward", new=AsyncMock(return_value=policies)):
            result = await get_insurance_policies(patient_id, _make_request())

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_policies(self):
        with patch("app.services.orchestrator._forward", new=AsyncMock(return_value=[])):
            result = await get_insurance_policies(str(uuid.uuid4()), _make_request())
        assert result == []

    @pytest.mark.asyncio
    async def test_non_list_response_coerced_to_empty(self):
        """Defensive: if downstream returns a dict instead of list, return []."""
        with patch("app.services.orchestrator._forward", new=AsyncMock(return_value={"detail": "err"})):
            result = await get_insurance_policies(str(uuid.uuid4()), _make_request())
        assert result == []


# ---------------------------------------------------------------------------
# verify_insurance_policy
# ---------------------------------------------------------------------------

class TestVerifyInsurancePolicy:
    @pytest.mark.asyncio
    async def test_verified_success(self):
        insurance_id = str(uuid.uuid4())
        updated = _insurance_fixture()
        updated["verification_status"] = "verified"

        body = MagicMock()
        body.model_dump.return_value = {"verification_status": "verified"}

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(200, updated))):
            result = await verify_insurance_policy(insurance_id, body, _make_request())

        assert result["verification_status"] == "verified"

    @pytest.mark.asyncio
    async def test_not_found_raises_422(self):
        from fastapi import HTTPException
        body = MagicMock()
        body.model_dump.return_value = {"verification_status": "verified"}

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(422, {"detail": "insurance_id not found"}))):
            with pytest.raises(HTTPException) as exc:
                await verify_insurance_policy("bad-id", body, _make_request())
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_downstream_error_propagates(self):
        from fastapi import HTTPException
        body = MagicMock()
        body.model_dump.return_value = {"verification_status": "verified"}

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(return_value=(500, {"detail": "Server error"}))):
            with pytest.raises(HTTPException) as exc:
                await verify_insurance_policy("some-id", body, _make_request())
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# get_visit_detail
# ---------------------------------------------------------------------------

class TestGetVisitDetail:
    @pytest.mark.asyncio
    async def test_cash_visit_includes_patient_no_insurance(self):
        patient_id = str(uuid.uuid4())
        patient = _patient_fixture(patient_id=patient_id)
        visit = _visit_fixture(patient_id=patient_id, insurance_id=None)

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            return visit

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            if "patients" in path:
                return (200, patient)
            return (200, [])   # insurance list

        with patch("app.services.orchestrator._forward", new=fake_forward):
            with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
                result = await get_visit_detail(visit["visit_id"], _make_request())

        assert result["patient"]["full_name"] == "Jane Doe"
        assert result["patient"]["patient_number"] == "PAT-0001"
        assert result.get("insurance") is None

    @pytest.mark.asyncio
    async def test_insurance_visit_includes_insurance_summary(self):
        patient_id = str(uuid.uuid4())
        insurance_id = str(uuid.uuid4())
        patient = _patient_fixture(patient_id=patient_id)
        ins = _insurance_fixture(patient_id=patient_id, insurance_id=insurance_id)
        visit = _visit_fixture(patient_id=patient_id, insurance_id=insurance_id)

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            return visit

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            if "patients" in path and "insurance" not in path:
                return (200, patient)
            return (200, [ins])   # insurance list

        with patch("app.services.orchestrator._forward", new=fake_forward):
            with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
                result = await get_visit_detail(visit["visit_id"], _make_request())

        assert result["insurance"]["insurer_name"] == "AAR Insurance"
        assert result["insurance"]["verification_status"] == "pending"

    @pytest.mark.asyncio
    async def test_patient_lookup_failure_does_not_crash(self):
        """If patient lookup fails, patient key is simply absent — no exception."""
        visit = _visit_fixture(insurance_id=None)

        async def fake_forward(method, base, path, req, body=None, extra_params=None):
            return visit

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            return (503, {})   # simulate downstream outage

        with patch("app.services.orchestrator._forward", new=fake_forward):
            with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
                result = await get_visit_detail(visit["visit_id"], _make_request())

        # visit data still returned; patient key missing but no crash
        assert result["visit_id"] == visit["visit_id"]
        assert result.get("patient") is None


# ---------------------------------------------------------------------------
# get_reception_queue
# ---------------------------------------------------------------------------

class TestGetReceptionQueue:
    @pytest.mark.asyncio
    async def test_empty_queue_returns_empty_list(self):
        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            return (200, [])

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
            result = await get_reception_queue(_make_request())
        assert result == []

    @pytest.mark.asyncio
    async def test_entries_enriched_with_patient_and_visit(self):
        patient_id = str(uuid.uuid4())
        visit_id = str(uuid.uuid4())
        patient = _patient_fixture(patient_id=patient_id)
        visit = _visit_fixture(patient_id=patient_id)
        visit["visit_id"] = visit_id
        queue_entry = _queue_fixture(patient_id=patient_id, visit_id=visit_id)

        call_count = {"n": 0}

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            call_count["n"] += 1
            if method == "GET" and "queue" in path:
                return (200, [queue_entry])
            if "patients" in path:
                return (200, patient)
            if "visits" in path:
                return (200, visit)
            return (200, {})

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
            result = await get_reception_queue(_make_request(), queue_type="triage")

        assert len(result) == 1
        entry = result[0]
        assert entry["patient"]["full_name"] == "Jane Doe"
        assert entry["patient"]["patient_number"] == "PAT-0001"
        assert entry["queue_number"] == "T-001"
        assert entry["visit"]["visit_number"] == visit["visit_number"]

    @pytest.mark.asyncio
    async def test_downstream_queue_error_returns_empty(self):
        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            return (503, {})

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
            result = await get_reception_queue(_make_request())
        assert result == []

    @pytest.mark.asyncio
    async def test_partial_enrichment_failure_does_not_drop_entry(self):
        """Even if patient/visit lookups fail, the queue entry is still returned."""
        patient_id = str(uuid.uuid4())
        visit_id = str(uuid.uuid4())
        queue_entry = _queue_fixture(patient_id=patient_id, visit_id=visit_id)

        async def fake_forward_raw(method, base, path, headers, body=None, params=None):
            if "queue" in path:
                return (200, [queue_entry])
            return (503, {})   # all enrichment lookups fail

        with patch("app.services.orchestrator._forward_raw", new=AsyncMock(side_effect=fake_forward_raw)):
            result = await get_reception_queue(_make_request(), queue_type="triage")

        # Entry still present, but patient.full_name falls back to "Unknown"
        assert len(result) == 1
        assert result[0]["patient"]["full_name"] == "Unknown"
        assert result[0]["queue_number"] == "T-001"
