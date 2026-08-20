"""Unit test suite for patient-service platform functions, business logic, schemas, and API router.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.api.v1.patients.schemas import (
    PatientRegisterRequest,
    PatientResponse,
    PatientSearchResponse,
    PatientUpdateRequest,
)
from app.models.patient import TenantPatient, PatientNumberSequence
from app.services import patient_service as ps_mod
from app.services import patient_number as pn_mod
from app.main import app
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.core.security import require_any_role


# ---------------------------------------------------------------------------
# PatientRegisterRequest & PatientUpdateRequest Schema Tests
# ---------------------------------------------------------------------------

class TestPatientSchemas:
    def test_patient_register_request_valid(self):
        req = PatientRegisterRequest(
            full_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            gender="female",
            phone_primary="0712345678",
        )
        assert req.full_name == "Jane Doe"
        assert req.gender == "female"

    def test_patient_register_request_name_stripped(self):
        req = PatientRegisterRequest(
            full_name="  Jane Doe  ",
            date_of_birth=date(1990, 5, 15),
            gender="female",
            phone_primary="0712345678",
        )
        assert req.full_name == "Jane Doe"

    def test_patient_register_request_empty_name_raises(self):
        with pytest.raises(ValidationError):
            PatientRegisterRequest(
                full_name="   ",
                date_of_birth=date(1990, 5, 15),
                gender="female",
                phone_primary="0712345678",
            )

    def test_patient_register_request_national_id_whitespace_returns_none(self):
        req = PatientRegisterRequest(
            full_name="Jane Doe",
            date_of_birth=date(1990, 5, 15),
            gender="female",
            phone_primary="0712345678",
            national_id="   ",
        )
        assert req.national_id is None

    def test_patient_register_request_gender_normalisation(self):
        req = PatientRegisterRequest(
            full_name="John Doe",
            date_of_birth=date(1988, 1, 1),
            gender="MALE",
            phone_primary="0712345678",
        )
        assert req.gender == "male"

    def test_patient_register_request_invalid_gender_raises(self):
        with pytest.raises(ValidationError):
            PatientRegisterRequest(
                full_name="Unknown",
                date_of_birth=date(1990, 1, 1),
                gender="other_invalid",
                phone_primary="0712345678",
            )

    def test_patient_update_request_valid_overrides(self):
        req = PatientUpdateRequest(
            full_name="Jane Smith",
            blood_group="O+",
            allergies="Penicillin",
        )
        assert req.full_name == "Jane Smith"
        assert req.blood_group == "O+"
        assert req.allergies == "Penicillin"


# ---------------------------------------------------------------------------
# Patient Service Core Business Logic Tests
# ---------------------------------------------------------------------------

class TestPatientServiceLogic:
    def test_register_patient(self, db_session: Session):
        hosp_id = "tenant-1"
        patient = ps_mod.register_patient(
            db=db_session,
            hospital_id=hosp_id,
            full_name="Alice Wambui",
            date_of_birth=date(1992, 3, 10),
            gender="female",
            phone_primary="0700112233",
            email="alice@example.com",
            address="123 Nairobi",
            next_of_kin_name="Bob",
            next_of_kin_phone="0700998877",
            next_of_kin_relationship="Spouse",
            national_id="12345678",
            allergies="Dust",
            blood_group="A+",
            created_by="receptionist1",
        )
        assert patient.id is not None
        assert patient.patient_number.startswith("PT-")
        assert patient.full_name == "Alice Wambui"
        assert patient.national_id == "12345678"

    def test_get_patient_by_id_valid_and_invalid(self, db_session: Session):
        hosp_id = "tenant-1"
        p = ps_mod.register_patient(
            db=db_session,
            hospital_id=hosp_id,
            full_name="Bob Otieno",
            date_of_birth=date(1985, 6, 20),
            gender="male",
            phone_primary="0722001122",
        )

        found = ps_mod.get_patient_by_id(db_session, hosp_id, str(p.id))
        assert found is not None
        assert found.full_name == "Bob Otieno"

        # Wrong hospital_id
        assert ps_mod.get_patient_by_id(db_session, "tenant-other", str(p.id)) is None

        # Invalid UUID string
        assert ps_mod.get_patient_by_id(db_session, hosp_id, "not-a-uuid") is None

    def test_search_patients_by_patient_number(self, db_session: Session):
        hosp_id = "tenant-1"
        p = ps_mod.register_patient(
            db=db_session,
            hospital_id=hosp_id,
            full_name="Carol Mutua",
            date_of_birth=date(1995, 8, 12),
            gender="female",
            phone_primary="0733112233",
        )
        results = ps_mod.search_patients(db_session, hosp_id, patient_number=p.patient_number)
        assert len(results) == 1
        assert results[0].id == p.id

    def test_search_patients_by_national_id(self, db_session: Session):
        hosp_id = "tenant-1"
        p = ps_mod.register_patient(
            db=db_session,
            hospital_id=hosp_id,
            full_name="David Njoroge",
            date_of_birth=date(1991, 4, 4),
            gender="male",
            phone_primary="0744112233",
            national_id="87654321",
        )
        results = ps_mod.search_patients(db_session, hosp_id, national_id="87654321")
        assert len(results) == 1
        assert results[0].id == p.id

    def test_search_patients_by_query_ilike(self, db_session: Session):
        hosp_id = "tenant-1"
        ps_mod.register_patient(
            db=db_session,
            hospital_id=hosp_id,
            full_name="Eve Adhiambo",
            date_of_birth=date(1994, 11, 30),
            gender="female",
            phone_primary="0755112233",
            email="eve@hospital.com",
        )
        results_name = ps_mod.search_patients(db_session, hosp_id, query="Adhiambo")
        assert len(results_name) == 1

        results_email = ps_mod.search_patients(db_session, hosp_id, query="eve@hospital.com")
        assert len(results_email) == 1

    def test_get_patient_count(self, db_session: Session):
        hosp_id = "tenant-count"
        assert ps_mod.get_patient_count(db_session, hosp_id) == 0

        ps_mod.register_patient(
            db=db_session, hospital_id=hosp_id, full_name="Frank",
            date_of_birth=date(1980, 1, 1), gender="male", phone_primary="0700000001",
        )
        ps_mod.register_patient(
            db=db_session, hospital_id=hosp_id, full_name="Grace",
            date_of_birth=date(1982, 2, 2), gender="female", phone_primary="0700000002",
        )
        assert ps_mod.get_patient_count(db_session, hosp_id) == 2

    def test_update_patient_all_fields(self, db_session: Session):
        hosp_id = "tenant-1"
        p = ps_mod.register_patient(
            db=db_session, hospital_id=hosp_id, full_name="Henry",
            date_of_birth=date(1990, 1, 1), gender="male", phone_primary="0711111111",
        )

        updated = ps_mod.update_patient(
            db=db_session,
            hospital_id=hosp_id,
            patient_id=str(p.id),
            full_name="Henry Updated",
            date_of_birth=date(1990, 1, 2),
            gender="other",
            phone_primary="0722222222",
            phone_secondary="0733333333",
            email="henry@new.com",
            address="New Address",
            next_of_kin_name="Kin Name",
            next_of_kin_phone="0744444444",
            next_of_kin_relationship="Parent",
            national_id="99999999",
            allergies="Nuts",
            blood_group="B-",
        )
        assert updated is not None
        assert updated.full_name == "Henry Updated"
        assert updated.phone_primary == "0722222222"
        assert updated.email == "henry@new.com"
        assert updated.blood_group == "B-"

    def test_update_patient_not_found_returns_none(self, db_session: Session):
        res = ps_mod.update_patient(db_session, "tenant-1", str(uuid4()), full_name="Nobody")
        assert res is None

    def test_delete_patient_found_and_not_found(self, db_session: Session):
        hosp_id = "tenant-1"
        p = ps_mod.register_patient(
            db=db_session, hospital_id=hosp_id, full_name="Ian",
            date_of_birth=date(1993, 3, 3), gender="male", phone_primary="0700000003",
        )
        assert ps_mod.delete_patient(db_session, hosp_id, str(p.id)) is True
        assert ps_mod.delete_patient(db_session, hosp_id, str(p.id)) is False


# ---------------------------------------------------------------------------
# Patient Number Generator Sequence Tests
# ---------------------------------------------------------------------------

def test_generate_patient_number_increments_sequence(db_session: Session):
    hosp_id = "tenant-seq"
    num1 = pn_mod.generate_patient_number(db_session, hosp_id)
    num2 = pn_mod.generate_patient_number(db_session, hosp_id)
    assert num1.endswith("-0001")
    assert num2.endswith("-0002")


def test_generate_patient_number_postgres_branch():
    mock_db = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 42
    mock_db.execute.return_value = mock_result

    with patch("app.services.patient_number._is_sqlite", return_value=False):
        num = pn_mod.generate_patient_number(mock_db, "tenant-pg")
        assert num.endswith("-0042")

from app.core.security import get_current_active_user, require_any_role


# ---------------------------------------------------------------------------
# API Router Integration Tests via TestClient
# ---------------------------------------------------------------------------

class TestPatientRouterEndpoints:
    def setup_method(self):
        def _mock_tenant_id():
            return "tenant-1"
        def _mock_user():
            return {
                "preferred_username": "receptionist1",
                "realm_access": {"roles": ["hospital_admin", "receptionist", "triage_nurse", "doctor"]},
            }

        app.dependency_overrides[get_tenant_id_from_token] = _mock_tenant_id
        app.dependency_overrides[get_current_active_user] = _mock_user
        self.sub_patcher = patch("app.events.subscriber.start_subscriber", new=AsyncMock())
        self.sub_patcher.start()

    def teardown_method(self):
        self.sub_patcher.stop()
        app.dependency_overrides.clear()

    def test_register_patient_route_success(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db

        payload = {
            "full_name": "Jack Mwangi",
            "date_of_birth": "1990-05-15",
            "gender": "male",
            "phone_primary": "0712345678",
        }
        with TestClient(app) as client:
            resp = client.post("/api/v1/patients/register", json=payload)
        assert resp.status_code == 201
        data = resp.json()
        assert data["full_name"] == "Jack Mwangi"
        assert "patient_number" in data

    def test_register_patient_route_duplicate_national_id_conflict(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        ps_mod.register_patient(
            db_session, "tenant-1", "Existing", date(1990, 1, 1),
            "female", "0711", national_id="DUP12345"
        )

        payload = {
            "full_name": "New Duplicate",
            "date_of_birth": "1990-05-15",
            "gender": "male",
            "phone_primary": "0712345678",
            "national_id": "DUP12345",
        }
        with TestClient(app) as client:
            resp = client.post("/api/v1/patients/register", json=payload)
        assert resp.status_code == 409
        assert "already exists" in resp.json()["detail"]

    def test_list_patients_route(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        with TestClient(app) as client:
            resp = client.get("/api/v1/patients")
        assert resp.status_code == 200
        assert "patients" in resp.json()

    def test_search_patients_route(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        with TestClient(app) as client:
            resp = client.get("/api/v1/patients/search?query=Jack")
        assert resp.status_code == 200
        assert "patients" in resp.json()

    def test_get_patient_by_id_route_found_and_not_found(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        p = ps_mod.register_patient(
            db_session, "tenant-1", "Kelly", date(1992, 2, 2),
            "female", "0722",
        )
        with TestClient(app) as client:
            resp_found = client.get(f"/api/v1/patients/{p.id}")
            assert resp_found.status_code == 200

            resp_404 = client.get(f"/api/v1/patients/{uuid4()}")
            assert resp_404.status_code == 404

    def test_update_patient_route(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        p = ps_mod.register_patient(
            db_session, "tenant-1", "Leo", date(1992, 2, 2),
            "male", "0722",
        )
        with TestClient(app) as client:
            resp = client.patch(f"/api/v1/patients/{p.id}", json={"full_name": "Leo Updated"})
            assert resp.status_code == 200
            assert resp.json()["full_name"] == "Leo Updated"

            resp_404 = client.patch(f"/api/v1/patients/{uuid4()}", json={"full_name": "Nobody"})
            assert resp_404.status_code == 404

    def test_delete_patient_route(self, db_session: Session):
        def _db():
            return db_session

        app.dependency_overrides[get_tenant_db] = _db
        p = ps_mod.register_patient(
            db_session, "tenant-1", "Mona", date(1992, 2, 2),
            "female", "0722",
        )
        with TestClient(app) as client:
            resp_del = client.delete(f"/api/v1/patients/{p.id}")
            assert resp_del.status_code == 204

            resp_404 = client.delete(f"/api/v1/patients/{uuid4()}")
            assert resp_404.status_code == 404
