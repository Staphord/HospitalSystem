"""Unit test suite for visit-service business logic, schemas, status transitions, insurance, and API routers.
"""
from datetime import date, timedelta
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.visits.schemas import (
    InsurancePolicyCreateRequest,
    InsuranceVerifyRequest,
    QueueAddRequest,
    QueueStatusUpdateRequest,
    TriageCompleteRequest,
    VisitCreateRequest,
    VisitStatusUpdateRequest,
)
from app.models.visit import (
    Patient,
    PatientInsurance,
    Queue,
    Visit,
)
from app.services import insurance_service as ins_mod
from app.services import number_generator as num_mod
from app.services import queue_service as qs_mod
from app.services import visit_service as vs_mod
from app.services import visit_status_service as vss_mod
from app.main import app
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.core.security import get_current_active_user


# ---------------------------------------------------------------------------
# Visit Pydantic Schemas Tests
# ---------------------------------------------------------------------------

class TestVisitSchemas:
    def test_visit_create_request_valid(self):
        pid = str(uuid4())
        req = VisitCreateRequest(patient_id=pid, visit_type="outpatient", payment_type="cash")
        assert str(req.patient_id) == pid
        assert req.visit_type == "outpatient"

    def test_visit_create_request_insurance_requires_id(self, db_session: Session, sample_patient_id):
        reg_by = str(uuid4())
        with pytest.raises(ValueError) as exc:
            vs_mod.create_visit(
                db=db_session,
                hospital_id="hosp-001",
                patient_id=sample_patient_id,
                visit_type="outpatient",
                payment_type="insurance",
                insurance_id=None,
                registered_by=reg_by,
            )
        assert "insurance_id is required" in str(exc.value)

    def test_insurance_policy_create_request_expiry_validation(self):
        req = InsurancePolicyCreateRequest(
            insurer_name="AAR",
            policy_number="POL-100",
            coverage_limit=100000.0,
            expiry_date=date.today() + timedelta(days=30),
        )
        assert req.insurer_name == "AAR"

        with pytest.raises(ValidationError):
            InsurancePolicyCreateRequest(
                insurer_name="",
                policy_number="POL-100",
            )

    def test_insurance_verify_request_allowed_status(self):
        req = InsuranceVerifyRequest(verification_status="verified")
        assert req.verification_status == "verified"

        with pytest.raises(ValidationError):
            InsuranceVerifyRequest(verification_status="invalid_status")

    def test_triage_complete_request_invalid_priority(self):
        with pytest.raises(ValidationError):
            TriageCompleteRequest(priority="super_urgent")

    def test_insurance_policy_create_request_empty_fields(self):
        with pytest.raises(ValidationError):
            InsurancePolicyCreateRequest(insurer_name="   ", policy_number="P1")

        with pytest.raises(ValidationError):
            InsurancePolicyCreateRequest(insurer_name="AAR", policy_number="   ")

    def test_visit_create_request_invalid_types_and_id(self):
        with pytest.raises(ValidationError):
            VisitCreateRequest(patient_id=str(uuid4()), visit_type="invalid_type", payment_type="cash")

        with pytest.raises(ValidationError):
            VisitCreateRequest(patient_id=str(uuid4()), visit_type="outpatient", payment_type="invalid_pay")

        with pytest.raises(ValidationError):
            VisitCreateRequest(patient_id="   ", visit_type="outpatient", payment_type="cash")

        with pytest.raises(ValidationError):
            VisitCreateRequest(patient_id="not-a-uuid", visit_type="outpatient", payment_type="cash")

    def test_visit_status_update_request_invalid_status(self):
        with pytest.raises(ValidationError):
            VisitStatusUpdateRequest(status="invalid_status")


# ---------------------------------------------------------------------------
# Business Services: Insurance, Queue, Number Generator & Visit Logic
# ---------------------------------------------------------------------------

class TestVisitServices:
    def test_create_and_get_insurance_policies(self, db_session: Session, sample_patient_id):
        policy = ins_mod.create_insurance_policy(
            db=db_session,
            patient_id=sample_patient_id,
            insurer_name="NHIF",
            policy_number="POL-NHIF-1",
            coverage_limit=50000.0,
            expiry_date=date.today() + timedelta(days=365),
        )
        assert policy.insurance_id is not None
        assert policy.verification_status == "pending"

        policies = ins_mod.get_patient_policies(db_session, sample_patient_id)
        assert len(policies) == 1
        assert policies[0].policy_number == "POL-NHIF-1"

    def test_update_insurance_verification_status(self, db_session: Session, pending_insurance):
        updated = ins_mod.update_verification_status(
            db=db_session,
            insurance_id=str(pending_insurance.insurance_id),
            verification_status="verified",
        )
        assert updated.verification_status == "verified"

        # Not found
        assert ins_mod.update_verification_status(db_session, str(uuid4()), "verified") is None

    def test_number_generator_sqlite_and_postgres(self, db_session: Session):
        vnum1 = num_mod.generate_visit_number(db_session)
        qnum1 = num_mod.generate_queue_number(db_session, "triage")
        assert vnum1.startswith("VIS-")
        assert qnum1.startswith("T-")

        # Mock postgres branch
        mock_db = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 10
        mock_db.execute.return_value = mock_result

        with patch("app.services.number_generator._is_sqlite", return_value=False):
            vnum_pg = num_mod.generate_visit_number(mock_db)
            qnum_pg = num_mod.generate_queue_number(mock_db, "doctor")
            assert vnum_pg.endswith("-0010")
            assert qnum_pg == "D-010"

    def test_queue_service_add_get_call_update(self, db_session: Session, sample_patient_id):
        hosp_id = "hosp-q"
        reg_by = str(uuid4())
        v_res = vs_mod.create_visit(db_session, hosp_id, sample_patient_id, "outpatient", "cash", registered_by=reg_by)
        vid = str(v_res["visit"].visit_id)
        pid = str(sample_patient_id)

        q_entry = qs_mod.add_to_queue(
            db=db_session,
            visit_id=vid,
            patient_id=pid,
            queue_type="doctor",
        )
        assert q_entry.queue_number.startswith("D-")
        assert q_entry.status == "waiting"

        queue_list = qs_mod.get_queue(db_session, queue_type="doctor")
        assert len(queue_list) >= 1

    def test_queue_service_invalid_id_and_status_transitions(self, db_session: Session, sample_patient_id):
        # Invalid queue_id -> 400
        with pytest.raises(HTTPException) as exc1:
            qs_mod.update_queue_status(db_session, "not-a-uuid", "completed")
        assert exc1.value.status_code == 400

        # Non-existent queue_id -> 404
        with pytest.raises(HTTPException) as exc2:
            qs_mod.update_queue_status(db_session, str(uuid4()), "completed")
        assert exc2.value.status_code == 404

        # Add to queue invalid visit_id -> 400
        with pytest.raises(HTTPException) as exc3:
            qs_mod.add_to_queue(db_session, "invalid-vid", str(sample_patient_id), "triage")
        assert exc3.value.status_code == 400

        # Add to queue 404 visit -> 404
        with pytest.raises(HTTPException) as exc4:
            qs_mod.add_to_queue(db_session, str(uuid4()), str(sample_patient_id), "triage")
        assert exc4.value.status_code == 404

    def test_insurance_service_verify_branches(self, db_session: Session, sample_patient_id):
        # find_insurance_policy
        p_created = ins_mod.create_insurance_policy(
            db_session, sample_patient_id, "Jubilee", "POL-JUB-1", 100.0, date.today() + timedelta(days=10)
        )
        found = ins_mod.find_insurance_policy(db_session, str(sample_patient_id), "Jubilee", "POL-JUB-1")
        assert found is not None

        # Expired policy
        p_created.verification_status = "verified"
        p_created.expiry_date = date.today() - timedelta(days=10)
        ok_exp, reason_exp = ins_mod.verify_insurance(p_created)
        assert ok_exp is False
        assert reason_exp == "Policy has expired"

        # Exhausted coverage limit
        p_created.expiry_date = date.today() + timedelta(days=10)
        p_created.coverage_limit = 0.0
        ok_lim, reason_lim = ins_mod.verify_insurance(p_created)
        assert ok_lim is False
        assert reason_lim == "Coverage limit exhausted"

        # Rejected policy
        p_created.verification_status = "rejected"
        ok_rej, reason_rej = ins_mod.verify_insurance(p_created)
        assert ok_rej is False
        assert reason_rej == "Policy verification was rejected"

    def test_create_visit_cash_and_insurance(self, db_session: Session, sample_patient_id, verified_insurance, pending_insurance):
        hosp_id = "hosp-001"
        reg_by = str(uuid4())
        # Cash visit
        res_cash = vs_mod.create_visit(
            db=db_session,
            hospital_id=hosp_id,
            patient_id=sample_patient_id,
            visit_type="outpatient",
            payment_type="cash",
            registered_by=reg_by,
        )
        assert res_cash["visit"].payment_type == "cash"
        assert res_cash["verification_flag"] is None

        # Transition cash visit step-by-step
        vss_mod.transition_visit_status(db_session, str(res_cash["visit"].visit_id), "triaged")
        vss_mod.transition_visit_status(db_session, str(res_cash["visit"].visit_id), "in_consultation")
        vss_mod.transition_visit_status(db_session, str(res_cash["visit"].visit_id), "completed")

        # Verified insurance visit
        res_ins = vs_mod.create_visit(
            db=db_session,
            hospital_id=hosp_id,
            patient_id=sample_patient_id,
            visit_type="outpatient",
            payment_type="insurance",
            insurance_id=str(verified_insurance.insurance_id),
            registered_by=reg_by,
        )
        assert res_ins["verification_flag"] is None

        vss_mod.transition_visit_status(db_session, str(res_ins["visit"].visit_id), "triaged")
        vss_mod.transition_visit_status(db_session, str(res_ins["visit"].visit_id), "in_consultation")
        vss_mod.transition_visit_status(db_session, str(res_ins["visit"].visit_id), "completed")

        # Pending insurance visit -> triggers verification_flag string
        res_pending = vs_mod.create_visit(
            db=db_session,
            hospital_id=hosp_id,
            patient_id=sample_patient_id,
            visit_type="outpatient",
            payment_type="insurance",
            insurance_id=str(pending_insurance.insurance_id),
            registered_by=reg_by,
        )
        assert res_pending["verification_flag"] is not None

    def test_complete_triage_and_enqueue_doctor(self, db_session: Session, sample_patient_id):
        hosp_id = "hosp-001"
        reg_by = str(uuid4())
        v_res = vs_mod.create_visit(db_session, hosp_id, sample_patient_id, "outpatient", "cash", registered_by=reg_by)
        visit = v_res["visit"]

        res = vs_mod.complete_triage_and_enqueue_doctor(
            db=db_session,
            visit_id=visit.visit_id,
            priority="emergency",
        )
        assert res["visit"].status == "triaged"
        assert res["queue_number"].startswith("D-")

        doctor_queue = vs_mod.get_ordered_doctor_queue(db_session)
        assert len(doctor_queue) >= 1

    def test_visit_status_transitions(self, db_session: Session, sample_patient_id):
        hosp_id = "hosp-001"
        reg_by = str(uuid4())
        v_res = vs_mod.create_visit(db_session, hosp_id, sample_patient_id, "outpatient", "cash", registered_by=reg_by)
        vid = str(v_res["visit"].visit_id)

        # Valid transition registered -> triaged -> in_consultation -> completed
        v1 = vss_mod.transition_visit_status(db_session, vid, "triaged")
        assert v1.status == "triaged"

        v2 = vss_mod.transition_visit_status(db_session, vid, "in_consultation")
        assert v2.status == "in_consultation"

        v3 = vss_mod.transition_visit_status(db_session, vid, "completed")
        assert v3.status == "completed"

        # Invalid transition completed -> triaged raises HTTPException
        with pytest.raises(HTTPException):
            vss_mod.transition_visit_status(db_session, vid, "triaged")


# ---------------------------------------------------------------------------
# API Router Integration Tests via TestClient
# ---------------------------------------------------------------------------

class TestVisitRouterEndpoints:
    def setup_method(self):
        def _mock_tenant_id():
            return "hosp-001"
        def _mock_user():
            return {
                "sub": str(uuid4()),
                "preferred_username": "receptionist1",
                "realm_access": {"roles": ["hospital_admin", "receptionist", "triage_nurse", "doctor"]},
            }

        app.dependency_overrides[get_tenant_id_from_token] = _mock_tenant_id
        app.dependency_overrides[get_current_active_user] = _mock_user
        self.pub_patcher = patch("app.events.publisher.publish_visit_created", new=AsyncMock())
        self.pub_patcher.start()

    def teardown_method(self):
        self.pub_patcher.stop()
        app.dependency_overrides.clear()

    def test_create_visit_route_success(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        payload = {
            "patient_id": str(sample_patient_id),
            "visit_type": "outpatient",
            "payment_type": "cash",
        }
        with TestClient(app) as client:
            resp = client.post("/api/v1/visits", json=payload)
        assert resp.status_code == 201
        assert "visit" in resp.json()

    def test_get_visit_by_id_route_found_and_not_found(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        v_res = vs_mod.create_visit(db_session, "hosp-001", sample_patient_id, "outpatient", "cash", registered_by=str(uuid4()))
        vid = str(v_res["visit"].visit_id)

        with TestClient(app) as client:
            resp_found = client.get(f"/api/v1/visits/{vid}")
            assert resp_found.status_code == 200

            resp_404 = client.get(f"/api/v1/visits/{uuid4()}")
            assert resp_404.status_code == 404

    def test_update_visit_status_route(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        v_res = vs_mod.create_visit(db_session, "hosp-001", sample_patient_id, "outpatient", "cash", registered_by=str(uuid4()))
        vid = str(v_res["visit"].visit_id)

        with TestClient(app) as client:
            resp = client.patch(f"/api/v1/visits/{vid}/status", json={"status": "triaged"})
            assert resp.status_code == 200
            assert resp.json()["status"] == "triaged"

    def test_triage_complete_route(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        v_res = vs_mod.create_visit(db_session, "hosp-001", sample_patient_id, "outpatient", "cash", registered_by=str(uuid4()))
        vid = str(v_res["visit"].visit_id)

        mock_res = {
            "status": "success",
            "visit": MagicMock(visit_id=vid, status="triaged"),
            "queue_number": "D-001",
        }
        with patch("app.api.v1.visits.router.complete_triage_and_enqueue_doctor", return_value=mock_res):
            with TestClient(app) as client:
                resp = client.post(
                    f"/api/v1/visits/{vid}/triage-complete",
                    json={"priority": "urgent"}
                )
            assert resp.status_code == 200
            assert resp.json()["status"] == "success"

    def test_insurance_policy_routes(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        with TestClient(app) as client:
            # Create policy
            resp_create = client.post(
                f"/api/v1/visits/patients/{sample_patient_id}/insurance",
                json={
                    "insurer_name": "APA",
                    "policy_number": "POL-APA-1",
                    "coverage_limit": 200000.0,
                    "expiry_date": str(date.today() + timedelta(days=365)),
                }
            )
            assert resp_create.status_code == 201
            iid = resp_create.json()["insurance_id"]

            # List policies
            resp_list = client.get(f"/api/v1/visits/patients/{sample_patient_id}/insurance")
            assert resp_list.status_code == 200

            # Verify policy
            resp_verify = client.patch(
                f"/api/v1/visits/insurance/{iid}/verify",
                json={"verification_status": "verified", "verifier_notes": "Approved"}
            )
            assert resp_verify.status_code == 200
            assert resp_verify.json()["verification_status"] == "verified"

            # Verify policy not found -> 422
            resp_422 = client.patch(
                f"/api/v1/visits/insurance/{uuid4()}/verify",
                json={"verification_status": "verified"}
            )
            assert resp_422.status_code == 422

    def test_queue_router_endpoints(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        v_res = vs_mod.create_visit(db_session, "hosp-001", sample_patient_id, "outpatient", "cash", registered_by=str(uuid4()))
        vid = str(v_res["visit"].visit_id)
        pid = str(sample_patient_id)

        with TestClient(app) as client:
            # List queue valid
            res_list = client.get("/api/v1/visits/queues/triage")
            assert res_list.status_code == 200

            # List queue invalid type -> 400
            res_bad_type = client.get("/api/v1/visits/queues/invalid_type")
            assert res_bad_type.status_code == 400

            # Call next valid
            res_next = client.get("/api/v1/visits/queues/triage/next")
            assert res_next.status_code == 200
            qid = res_next.json()["queue_id"]

            # Call next empty -> 404
            res_empty_next = client.get("/api/v1/visits/queues/triage/next")
            assert res_empty_next.status_code == 404

            # Update queue entry status (in_progress -> completed)
            res_upd = client.patch(f"/api/v1/visits/queues/{qid}/status", json={"status": "completed"})
            assert res_upd.status_code == 200

            # Add to queue directly
            res_add = client.post(
                "/api/v1/visits/queues/add",
                json={"visit_id": vid, "patient_id": pid, "queue_type": "doctor", "priority": "urgent"}
            )
            assert res_add.status_code == 201

            # Triage queue today & Doctor queue today
            res_trg_today = client.get("/api/v1/visits/queues/triage/today")
            assert res_trg_today.status_code == 200

            res_doc_today = client.get("/api/v1/visits/queues/doctor/today")
            assert res_doc_today.status_code == 200

    def test_active_patient_visit_route(self, db_session: Session, sample_patient_id):
        def _db():
            return db_session
        app.dependency_overrides[get_tenant_db] = _db

        with TestClient(app) as client:
            # Invalid UUID -> 400
            assert client.get("/api/v1/visits/active-patient/invalid-uuid").status_code == 400

            # No active visit -> returns null / null visit
            assert client.get(f"/api/v1/visits/active-patient/{sample_patient_id}").status_code == 200

            # Create active visit
            vs_mod.create_visit(db_session, "hosp-001", sample_patient_id, "outpatient", "cash", registered_by=str(uuid4()))

            res_active = client.get(f"/api/v1/visits/active-patient/{sample_patient_id}")
            assert res_active.status_code == 200
            assert res_active.json()["visit_id"] is not None
