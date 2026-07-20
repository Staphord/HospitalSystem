from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
import uuid
from datetime import datetime, date

from app.main import app
from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db, get_current_user
from app.core.security import TokenPayload, get_current_active_user
from app.models.triage import TriageAssessment, Visit, Patient, Queue


class MockAsyncSession:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = None
        self.existing_assessment = None
        self.mock_visit = None
        self.mock_queue = None

    async def execute(self, stmt):
        mock_result = MagicMock()
        mock_scalar = MagicMock()
        
        stmt_str = str(stmt).lower()
        if "patients" in stmt_str and "queues" in stmt_str:
            mock_result.all.return_value = [
                (self.mock_queue, self.mock_visit, Patient(
                    id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
                    hospital_id="hosp-001",
                    patient_number="PAT-123",
                    full_name="Jane Doe",
                    date_of_birth=date(1990, 5, 12),
                    gender="female"
                ), None)
            ]
        elif "triage_assessments" in stmt_str:
            mock_scalar.first.return_value = self.existing_assessment
        elif "queues" in stmt_str:
            mock_scalar.first.return_value = self.mock_queue
        elif "visits" in stmt_str:
            mock_scalar.first.return_value = self.mock_visit
        elif "patients" in stmt_str:
            mock_scalar.first.return_value = Patient(
                id=uuid.UUID("11111111-2222-3333-4444-555555555555"),
                hospital_id="hosp-001",
                patient_number="PAT-123",
                full_name="Jane Doe",
                date_of_birth=date(1990, 5, 12),
                gender="female"
            )
        elif "users" in stmt_str:
            from app.models.user import User
            mock_scalar.first.return_value = User(
                id=1,
                keycloak_sub="nurse-sub-123",
                username="nurse_test",
                full_name="Nurse Mary",
                role="triage_nurse"
            )
            
        mock_result.scalars.return_value = mock_scalar
        return mock_result

    def add(self, obj):
        if hasattr(obj, "triage_id") and obj.triage_id is None:
            obj.triage_id = uuid.uuid4()
        if hasattr(obj, "assessed_at") and obj.assessed_at is None:
            obj.assessed_at = datetime.utcnow()
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = datetime.utcnow()
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = datetime.utcnow()
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed = obj

    async def flush(self):
        pass


@pytest.fixture(scope="function")
def mock_db():
    return MockAsyncSession()


async def override_get_current_tenant():
    from app.core.tenant_auth import TenantContext
    return TenantContext(
        tenant_id="hosp-001",
        user_sub="22222222-3333-4444-5555-666666666666",
        preferred_username="nurse_test",
        email="nurse@example.com",
        roles=["triage_nurse"],
        is_super_admin=False
    )


async def override_get_current_user():
    return TokenPayload(
        sub="22222222-3333-4444-5555-666666666666",
        preferred_username="nurse_test",
        email="nurse@example.com",
        realm_access={"roles": ["triage_nurse"]},
        raw={"type": "user", "role": "triage_nurse"}
    )


@pytest.fixture(autouse=True)
def setup_overrides(mock_db):
    async def override_get_tenant_db():
        yield mock_db
        
    app.dependency_overrides[get_current_tenant] = override_get_current_tenant
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_current_active_user] = override_get_current_user
    app.dependency_overrides[get_tenant_db] = override_get_tenant_db
    yield
    app.dependency_overrides.clear()


def test_suggest_category_endpoint():
    client = TestClient(app)
    payload = {
        "oxygen_saturation": 88.0,
        "respiratory_rate": 16,
        "pulse_rate": 72,
        "temperature": 36.6
    }
    response = client.post("/api/v1/triage/assessments/suggest-category", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["suggested_category"] == "emergency"
    assert "SpO2" in data["reason"]


def test_create_assessment_endpoint(mock_db):
    client = TestClient(app)
    mock_visit_id = "466d94fc-34fe-4bad-a9e8-ccdd771701a0"
    mock_patient_id = "11111111-2222-3333-4444-555555555555"
    
    mock_db.mock_visit = Visit(
        visit_id=uuid.UUID(mock_visit_id),
        patient_id=mock_patient_id,
        visit_number="V-12345",
        visit_type="outpatient",
        payment_type="cash",
        status="registered"
    )
    
    payload = {
        "visit_id": mock_visit_id,
        "patient_id": mock_patient_id,
        "blood_pressure_systolic": 120,
        "blood_pressure_diastolic": 80,
        "temperature": 36.6,
        "pulse_rate": 72,
        "oxygen_saturation": 98.0,
        "respiratory_rate": 16,
        "weight_kg": 70.0,
        "chief_complaint": "Headache",
        "triage_category": "non_urgent"
    }
    
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "visit_id": mock_visit_id,
            "visit_status": "triaged",
            "queue_number": "D001"
        }
        mock_post.return_value = mock_resp
        
        with patch("app.events.publisher.publish_triage_completed") as mock_pub:
            response = client.post("/api/v1/triage/assessments", json=payload)
            assert response.status_code == 201
            data = response.json()
            assert data["visit_id"] == mock_visit_id
            assert data["triage_category"] == "non_urgent"
            assert data["chief_complaint"] == "Headache"
            assert "vitals" in data
            assert data["vitals"]["blood_pressure_systolic"] == 120
            
            # Verify db was called
            assert len(mock_db.added) == 1
            assert str(mock_db.added[0].visit_id) == mock_visit_id
            assert mock_db.committed is True


def test_get_assessment_endpoint(mock_db):
    client = TestClient(app)
    mock_visit_id = "466d94fc-34fe-4bad-a9e8-ccdd771701a0"
    mock_patient_id = "11111111-2222-3333-4444-555555555555"
    
    mock_assessment = TriageAssessment(
        triage_id=uuid.uuid4(),
        assessed_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        visit_id=uuid.UUID(mock_visit_id),
        patient_id=uuid.UUID(mock_patient_id),
        triage_nurse_id=uuid.UUID("22222222-3333-4444-5555-666666666666"),
        blood_pressure_systolic=120,
        blood_pressure_diastolic=80,
        temperature=36.6,
        pulse_rate=72,
        oxygen_saturation=98.0,
        respiratory_rate=16,
        weight_kg=70.0,
        chief_complaint="Headache",
        triage_category="non_urgent"
    )
    mock_db.existing_assessment = mock_assessment
    
    response = client.get(f"/api/v1/triage/assessments/{mock_visit_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["visit_id"] == mock_visit_id
    assert data["chief_complaint"] == "Headache"
    assert data["patient"]["full_name"] == "Jane Doe"


def test_get_queue_endpoint(mock_db):
    client = TestClient(app)
    mock_visit_id = "466d94fc-34fe-4bad-a9e8-ccdd771701a0"
    mock_patient_id = "11111111-2222-3333-4444-555555555555"
    
    mock_db.mock_queue = Queue(
        queue_id=uuid.uuid4(),
        visit_id=uuid.UUID(mock_visit_id),
        patient_id=mock_patient_id,
        queue_type="triage",
        queue_number="T001",
        priority="emergency",
        status="waiting",
        created_at=datetime.utcnow()
    )
    
    mock_db.mock_visit = Visit(
        visit_id=uuid.UUID(mock_visit_id),
        patient_id=mock_patient_id,
        visit_number="V-12345",
        visit_type="outpatient",
        payment_type="cash",
        status="registered"
    )
    
    response = client.get("/api/v1/triage/queue")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["queue"][0]["queue_number"] == "T001"
    assert data["queue"][0]["patient"]["full_name"] == "Jane Doe"
