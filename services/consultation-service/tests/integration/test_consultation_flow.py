import uuid
from datetime import datetime, date
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.dependencies import get_tenant_db
from app.models.consultation import (
    Patient,
    Visit,
    TriageAssessment,
    Consultation,
    Diagnosis,
    InvestigationRequest,
)


class MockAsyncSession:
    def __init__(self):
        self.patients = {}
        self.visits = {}
        self.triages = {}
        self.consultations = {}
        self.diagnoses = {}
        self.investigations = {}
        self.added = []
        self.committed = False

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()

        class MockResult:
            def __init__(self, data):
                self._data = data

            def scalars(self):
                class MockScalars:
                    def __init__(self, d):
                        self._d = d

                    def first(self):
                        return self._d[0] if self._d else None

                    def all(self):
                        return self._d

                return MockScalars(self._data)

        if "from patients" in stmt_str:
            return MockResult(list(self.patients.values()))
        elif "from visits" in stmt_str:
            return MockResult(list(self.visits.values()))
        elif "from triage_assessments" in stmt_str:
            return MockResult(list(self.triages.values()))
        elif "from consultations" in stmt_str:
            return MockResult(list(self.consultations.values()))
        elif "from diagnoses" in stmt_str:
            result_list = list(self.diagnoses.values())
            if "provisional" in stmt_str:
                result_list = [d for d in result_list if d.diagnosis_type == "provisional"]
            return MockResult(result_list)
        elif "from investigation_requests" in stmt_str:
            return MockResult(list(self.investigations.values()))

        return MockResult([])

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Patient):
            self.patients[obj.id] = obj
        elif isinstance(obj, Visit):
            self.visits[obj.visit_id] = obj
        elif isinstance(obj, TriageAssessment):
            self.triages[obj.visit_id] = obj
        elif isinstance(obj, Consultation):
            self.consultations[obj.visit_id] = obj
            self.consultations[obj.id] = obj
        elif isinstance(obj, Diagnosis):
            self.diagnoses[obj.id] = obj
        elif isinstance(obj, InvestigationRequest):
            self.investigations[obj.id] = obj

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass


@pytest.fixture(scope="function")
def mock_db():
    session = MockAsyncSession()
    # Seed mock data
    p_id = uuid.uuid4()
    v_id = uuid.uuid4()
    patient = Patient(
        id=p_id,
        hospital_id="hosp-001",
        patient_number="PAT-1001",
        full_name="John Doe",
        date_of_birth=date(1990, 5, 12),
        gender="male",
        phone_primary="+123456789",
        is_active=True,
    )
    visit = Visit(
        visit_id=v_id,
        patient_id=p_id,
        visit_number="VIS-1001",
        visit_date=date.today(),
        visit_type="outpatient",
        payment_type="cash",
        status="triaged",
    )
    triage = TriageAssessment(
        id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=str(p_id),
        blood_pressure="120/80",
        temperature=37.0,
        pulse=75,
        oxygen_saturation=98.0,
        respiratory_rate=16,
        weight=70.0,
        presenting_complaint="Fever and headache",
        triage_category="urgent",
        created_at=datetime.utcnow(),
    )
    session.add(patient)
    session.add(visit)
    session.add(triage)
    return session


async def override_get_current_tenant():
    return TenantContext(
        tenant_id="hosp-001",
        user_sub="doctor-sub-123",
        preferred_username="doctor_test",
        email="doctor@example.com",
        roles=["doctor"],
        is_super_admin=False,
        raw_token={"token": "test_token"}
    )


@pytest.fixture(autouse=True)
def setup_overrides(mock_db):
    async def override_get_tenant_db():
        yield mock_db

    app.dependency_overrides[get_current_tenant] = override_get_current_tenant
    app.dependency_overrides[get_tenant_db] = override_get_tenant_db
    yield
    app.dependency_overrides.clear()


def test_create_consultation_endpoint(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]
    p_id = list(mock_db.patients.keys())[0]

    payload = {
        "visit_id": str(v_id),
        "patient_id": str(p_id),
        "history_of_presenting_illness": "Patient presents with 3-day history of malaria symptoms.",
        "examination_findings": "Mild fever, clear lungs.",
        "clinical_impression": "Suspected malaria.",
    }

    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_patch.return_value = mock_resp

        response = client.post(
            "/api/v1/consultation/encounters",
            json=payload,
            headers={"Authorization": "Bearer test_token"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["visit_id"] == str(v_id)
        assert data["history_of_presenting_illness"] == payload["history_of_presenting_illness"]
        assert len(mock_db.consultations) > 0


def test_investigations_provisional_diagnosis_validation(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]
    p_id = list(mock_db.patients.keys())[0]

    # Create consultation
    consultation = Consultation(
        id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=p_id,
        history_of_presenting_illness="Test HPI",
        examination_findings="Test Findings",
        clinical_impression="Test Impression",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    mock_db.add(consultation)

    # 1. Try to request investigations without provisional diagnosis
    investigation_payload = [
        {
            "request_type": "laboratory",
            "test_name": "Malaria BS",
            "clinical_history": "Fever",
        }
    ]
    response = client.post(
        f"/api/v1/consultation/encounters/{consultation.id}/investigations",
        json=investigation_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert response.status_code == 400
    assert "provisional diagnosis must be recorded" in response.json()["detail"]

    # 2. Add a provisional diagnosis
    diag_payload = {
        "diagnosis_type": "provisional",
        "code": "B54",
        "description": "Unspecified malaria",
    }
    diag_response = client.post(
        f"/api/v1/consultation/encounters/{consultation.id}/diagnoses",
        json=diag_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert diag_response.status_code == 201

    # 3. Add a differential diagnosis
    diff_payload = {
        "diagnosis_type": "differential",
        "code": "R50.9",
        "description": "Fever, unspecified",
    }
    diff_response = client.post(
        f"/api/v1/consultation/encounters/{consultation.id}/diagnoses",
        json=diff_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert diff_response.status_code == 201

    # 4. Now request investigations, should succeed
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_patch.return_value = mock_resp

        response2 = client.post(
            f"/api/v1/consultation/encounters/{consultation.id}/investigations",
            json=investigation_payload,
            headers={"Authorization": "Bearer test_token"},
        )
        assert response2.status_code == 201
        res_data = response2.json()
        assert len(res_data) == 1
        assert res_data[0]["test_name"] == "Malaria BS"


def test_get_patient_encounter_view(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]
    p_id = list(mock_db.patients.keys())[0]

    consultation = Consultation(
        id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=p_id,
        history_of_presenting_illness="Test HPI",
        examination_findings="Test Findings",
        clinical_impression="Test Impression",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    mock_db.add(consultation)

    response = client.get(
        f"/api/v1/consultation/encounters/patient/{p_id}/encounter-view/{v_id}",
        headers={"Authorization": "Bearer test_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["patient"]["full_name"] == "John Doe"
    assert data["triage_summary"]["presenting_complaint"] == "Fever and headache"
    assert data["consultation"]["history_of_presenting_illness"] == "Test HPI"


def test_get_patient_history(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]
    p_id = list(mock_db.patients.keys())[0]

    consultation = Consultation(
        id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=p_id,
        history_of_presenting_illness="Test HPI",
        examination_findings="Test Findings",
        clinical_impression="Test Impression",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    mock_db.add(consultation)

    response = client.get(
        f"/api/v1/consultation/encounters/patient/{p_id}/history",
        headers={"Authorization": "Bearer test_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["patient"]["full_name"] == "John Doe"
    assert len(data["previous_visits"]) == 1
    assert data["previous_visits"][0]["visit_id"] == str(v_id)
    assert data["previous_visits"][0]["triage_summary"]["presenting_complaint"] == "Fever and headache"
    assert data["previous_visits"][0]["consultation"]["history_of_presenting_illness"] == "Test HPI"
