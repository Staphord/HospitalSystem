"""Unit test suite for radiology-service workflow actions, reports, and imaging requests.
"""
from datetime import datetime, timezone, date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import radiology as rad_srv
from app.exceptions import BadRequestError, ConflictError, NotFoundError
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.core.security import get_current_active_user, TokenPayload
from app.dependencies import get_tenant_db


class TestRadiologyRouterEndpoints:
    def setup_method(self):
        self.user_id = str(uuid4())
        self.mock_user = TokenPayload(
            sub=self.user_id,
            preferred_username="radiographer1",
            email="rad@hosp.org",
            realm_access={"roles": ["radiographer"]},
            raw={},
        )
        self.mock_tenant = TenantContext(
            tenant_id="t1",
            user_sub=self.user_id,
            preferred_username="radiographer1",
            email="rad@hosp.org",
            roles=["radiographer"],
            is_super_admin=False,
        )

        app.dependency_overrides[get_current_tenant] = lambda: self.mock_tenant
        app.dependency_overrides[get_current_active_user] = lambda: self.mock_user

        mock_db = AsyncMock()
        app.dependency_overrides[get_tenant_db] = lambda: mock_db
        self.mock_db = mock_db
        self.client = TestClient(app)

    def teardown_method(self):
        app.dependency_overrides.clear()

    def test_list_imaging_requests_endpoint(self):
        with patch("app.services.radiology.list_imaging_requests", new=AsyncMock(return_value=([], 0))):
            resp = self.client.get("/api/v1/radiology/requests")
            assert resp.status_code == 200
            assert resp.json() == {"requests": [], "total": 0}

    def test_list_reports_endpoint(self):
        with patch("app.services.radiology.list_reports", new=AsyncMock(return_value=([], 0))):
            resp = self.client.get("/api/v1/radiology/reports")
            assert resp.status_code == 200
            assert resp.json() == {"reports": [], "total": 0}


# ---------------------------------------------------------------------------
# Helper & Validation Unit Tests
# ---------------------------------------------------------------------------

class TestRadiologyValidationHelpers:
    def test_validate_modality(self):
        assert rad_srv._validate_modality("xray") == "xray"
        assert rad_srv._validate_modality("ct") == "ct"

        with pytest.raises(BadRequestError):
            rad_srv._validate_modality("invalid-modality")

    def test_validate_report_status(self):
        assert rad_srv._validate_report_status("scheduled") == "scheduled"
        assert rad_srv._validate_report_status("verified") == "verified"

        with pytest.raises(BadRequestError):
            rad_srv._validate_report_status("invalid-status")

    def test_parse_user_uuid(self):
        uid = uuid4()
        assert rad_srv._parse_user_uuid(None, explicit=uid) == uid

        user_mock = MagicMock(sub=str(uid))
        assert rad_srv._parse_user_uuid(user_mock) == uid

        with pytest.raises(BadRequestError):
            rad_srv._parse_user_uuid(None, None)


# ---------------------------------------------------------------------------
# Report CRUD Operations Tests
# ---------------------------------------------------------------------------

class TestRadiologyReportCRUD:
    @pytest.mark.asyncio
    async def test_get_report_not_found(self):
        mock_db = AsyncMock()
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = None
        mock_db.execute.return_value = mock_res

        with pytest.raises(NotFoundError):
            await rad_srv.get_report(mock_db, uuid4())

    @pytest.mark.asyncio
    async def test_get_report_success(self):
        mock_db = AsyncMock()
        rep_id = uuid4()
        mock_rep = MagicMock(report_id=rep_id)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_rep
        mock_db.execute.return_value = mock_res

        res = await rad_srv.get_report(mock_db, rep_id)
        assert res.report_id == rep_id

    @pytest.mark.asyncio
    async def test_list_reports(self):
        mock_db = AsyncMock()
        mock_rep = MagicMock()
        mock_count = MagicMock()
        mock_count.scalar.return_value = 1
        mock_items = MagicMock()
        mock_items.scalars.return_value.all.return_value = [mock_rep]

        mock_db.execute.side_effect = [mock_count, mock_items]

        reports, total = await rad_srv.list_reports(mock_db, patient_id=uuid4(), visit_id=uuid4(), status="scheduled")
        assert total == 1
        assert len(reports) == 1

    @pytest.mark.asyncio
    async def test_create_report_success(self):
        mock_db = AsyncMock()
        req_id = uuid4()
        vis_id = uuid4()
        pat_id = uuid4()
        perf_id = uuid4()

        mock_req = MagicMock(id=req_id, request_type="radiology")
        mock_res_req = MagicMock()
        mock_res_req.scalar_one_or_none.return_value = mock_req

        mock_res_rep = MagicMock()
        mock_res_rep.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [mock_res_req, mock_res_rep]

        data = {
            "request_id": req_id,
            "visit_id": vis_id,
            "patient_id": pat_id,
            "modality": "xray",
            "performed_by": perf_id,
            "status": "scheduled",
        }

        report = await rad_srv.create_report(mock_db, data)
        assert report.modality == "xray"
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_report_success(self):
        mock_db = AsyncMock()
        rep_id = uuid4()
        mock_rep = MagicMock(report_id=rep_id, status="scheduled", reported_at=None)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_rep
        mock_db.execute.return_value = mock_res

        data = {"findings": "Normal chest xray", "status": "reported"}
        res = await rad_srv.update_report(mock_db, rep_id, data)
        assert res.findings == "Normal chest xray"
        mock_db.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_report_success(self):
        mock_db = AsyncMock()
        rep_id = uuid4()
        mock_rep = MagicMock(report_id=rep_id)
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = mock_rep
        mock_db.execute.return_value = mock_res

        await rad_srv.delete_report(mock_db, rep_id)
        mock_db.delete.assert_awaited_once_with(mock_rep)
        mock_db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Imaging Request Worklist Tests
# ---------------------------------------------------------------------------

class TestRadiologyWorklist:
    @pytest.mark.asyncio
    async def test_list_imaging_requests(self):
        mock_db = AsyncMock()
        req_id = uuid4()

        mock_req = MagicMock(id=req_id, test_name="Chest X-Ray", clinical_history="Cough", urgency="stat", status="pending", created_by="dr", created_at=datetime.now(timezone.utc))
        mock_pat = MagicMock(full_name="John Doe", patient_number="P-001")
        mock_vis = MagicMock(visit_id=uuid4(), visit_number="V-001")
        mock_rep = None

        mock_count_res = MagicMock()
        mock_count_res.scalar.return_value = 1

        mock_items_res = MagicMock()
        mock_items_res.all.return_value = [(mock_req, mock_pat, mock_vis, mock_rep)]

        mock_db.execute.side_effect = [mock_count_res, mock_items_res]

        items, total = await rad_srv.list_imaging_requests(mock_db, status="pending", urgency="stat", search="Chest")
        assert total == 1
        assert items[0]["test_name"] == "Chest X-Ray"

    @pytest.mark.asyncio
    async def test_get_imaging_request_detail(self):
        mock_db = AsyncMock()
        req_id = uuid4()

        mock_req = MagicMock(id=req_id, test_name="Brain MRI", request_type="radiology", clinical_history="Headache", urgency="urgent", created_by="dr", created_at=datetime.now(timezone.utc), status="pending")
        mock_pat = MagicMock(id=uuid4(), patient_number="P-002", full_name="Jane Doe", date_of_birth=date(1990, 1, 1), gender="female", phone_primary="123", allergies=None)
        mock_vis = MagicMock(visit_id=uuid4(), visit_number="V-002", visit_date=date.today(), visit_type="outpatient", payment_type="cash")
        mock_rep = None

        mock_res = MagicMock()
        mock_res.one_or_none.return_value = (mock_req, mock_pat, mock_vis, mock_rep)
        mock_db.execute.return_value = mock_res

        detail = await rad_srv.get_imaging_request_detail(mock_db, req_id)
        assert detail["test_name"] == "Brain MRI"
        assert detail["patient"]["full_name"] == "Jane Doe"


# ---------------------------------------------------------------------------
# Workflow Actions Tests (Schedule, Perform, Enter Report, Verify)
# ---------------------------------------------------------------------------

class TestRadiologyWorkflowActions:
    @pytest.mark.asyncio
    async def test_schedule_imaging_new(self):
        mock_db = AsyncMock()
        req_id = uuid4()
        user_uid = uuid4()

        mock_req = MagicMock(id=req_id, visit_id=uuid4(), patient_id=uuid4(), request_type="radiology", status="pending")
        mock_res_req = MagicMock()
        mock_res_req.scalar_one_or_none.return_value = mock_req

        mock_res_rep = MagicMock()
        mock_res_rep.scalar_one_or_none.return_value = None

        mock_db.execute.side_effect = [mock_res_req, mock_res_rep]

        user_mock = MagicMock(sub=str(user_uid))
        data = {"modality": "xray", "body_part": "Chest", "scheduled_at": datetime.now(timezone.utc)}

        report = await rad_srv.schedule_imaging(mock_db, req_id, data, user_mock)
        assert report.status == "scheduled"
        mock_db.add.assert_called_once()

    @pytest.mark.asyncio
    async def test_perform_imaging_success(self):
        mock_db = AsyncMock()
        req_id = uuid4()
        user_uid = uuid4()

        mock_req = MagicMock(id=req_id, request_type="radiology", status="pending")
        mock_rep = MagicMock(status="scheduled")
        mock_res_req = MagicMock()
        mock_res_req.scalar_one_or_none.return_value = mock_req

        mock_res_rep = MagicMock()
        mock_res_rep.scalar_one_or_none.return_value = mock_rep

        mock_db.execute.side_effect = [mock_res_req, mock_res_rep]

        user_mock = MagicMock(sub=str(user_uid))
        data = {"performed_at": datetime.now(timezone.utc)}

        report = await rad_srv.perform_imaging(mock_db, req_id, data, user_mock)
        assert report.status == "performed"
        assert mock_req.status == "in_progress"

    @pytest.mark.asyncio
    async def test_enter_report_success(self):
        mock_db = AsyncMock()
        req_id = uuid4()
        rep_id = uuid4()
        user_uid = uuid4()

        mock_req = MagicMock(id=req_id, request_type="radiology", status="in_progress")
        mock_rep = MagicMock(report_id=rep_id, request_id=req_id, status="performed")
        mock_res_req = MagicMock()
        mock_res_req.scalar_one_or_none.return_value = mock_req

        mock_res_rep = MagicMock()
        mock_res_rep.scalar_one_or_none.return_value = mock_rep

        mock_db.execute.side_effect = [mock_res_req, mock_res_rep]

        user_mock = MagicMock(sub=str(user_uid))
        data = {"findings": "No fractures", "impression": "Normal"}

        with patch("app.services.radiology.publish_radiology_report_ready", AsyncMock()):
            report = await rad_srv.enter_report(mock_db, req_id, data, user_mock, tenant_id="t1")
            assert report.status == "reported"
            assert mock_req.status == "completed"

    @pytest.mark.asyncio
    async def test_verify_report_success(self):
        mock_db = AsyncMock()
        req_id = uuid4()
        rep_id = uuid4()
        user_uid = uuid4()

        mock_req = MagicMock(id=req_id, request_type="radiology", status="completed")
        mock_rep = MagicMock(report_id=rep_id, request_id=req_id, status="reported", reported_by=user_uid)
        mock_res_rep = MagicMock()
        mock_res_rep.scalar_one_or_none.return_value = mock_rep

        mock_res_req = MagicMock()
        mock_res_req.scalar_one_or_none.return_value = mock_req

        mock_db.execute.side_effect = [mock_res_rep, mock_res_req]

        user_mock = MagicMock(sub=str(user_uid))

        with patch("app.services.radiology.publish_radiology_report_ready", AsyncMock()):
            report = await rad_srv.verify_report(mock_db, rep_id, user_mock, tenant_id="t1")
            assert report.status == "verified"
