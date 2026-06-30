from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db, get_current_user
from app.core.security import TokenPayload
from app.models.triage import TriageAssessment


import uuid
from datetime import datetime


class MockAsyncSession:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = None
        self.existing_assessment = None

    async def execute(self, stmt):
        mock_result = MagicMock()
        mock_scalar = MagicMock()
        mock_scalar.first.return_value = self.existing_assessment
        mock_result.scalars.return_value = mock_scalar
        return mock_result

    def add(self, obj):
        if hasattr(obj, "id") and obj.id is None:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and obj.created_at is None:
            obj.created_at = datetime.utcnow()
        if hasattr(obj, "updated_at") and obj.updated_at is None:
            obj.updated_at = datetime.utcnow()
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed = obj


@pytest.fixture(scope="function")
def mock_db():
    return MockAsyncSession()


async def override_get_current_tenant():
    from app.core.tenant_auth import TenantContext
    return TenantContext(
        tenant_id="hosp-001",
        user_sub="nurse-sub-123",
        preferred_username="nurse_test",
        email="nurse@example.com",
        roles=["nurse"],
        is_super_admin=False
    )


async def override_get_current_user():
    return TokenPayload(
        sub="nurse-sub-123",
        preferred_username="nurse_test",
        email="nurse@example.com",
        realm_access={"roles": ["nurse"]},
        raw={"type": "user", "role": "nurse"}
    )


@pytest.fixture(autouse=True)
def setup_overrides(mock_db):
    async def override_get_tenant_db():
        yield mock_db
        
    app.dependency_overrides[get_current_tenant] = override_get_current_tenant
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_tenant_db] = override_get_tenant_db
    yield
    app.dependency_overrides.clear()


def test_suggest_category_endpoint():
    client = TestClient(app)
    payload = {
        "oxygen_saturation": 88.0,
        "respiratory_rate": 16,
        "pulse": 72,
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
    payload = {
        "visit_id": mock_visit_id,
        "patient_id": "patient-456",
        "blood_pressure": "120/80",
        "temperature": 36.6,
        "pulse": 72,
        "oxygen_saturation": 98.0,
        "respiratory_rate": 16,
        "weight": 70.0,
        "presenting_complaint": "Headache",
        "triage_category": "non_urgent"
    }
    
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_post.return_value = mock_resp
        
        response = client.post("/api/v1/triage/assessments", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["visit_id"] == mock_visit_id
        assert data["triage_category"] == "non_urgent"
        
        # Verify db was called
        assert len(mock_db.added) == 1
        assert str(mock_db.added[0].visit_id) == mock_visit_id
        assert mock_db.committed is True


def test_get_assessment_endpoint(mock_db):
    client = TestClient(app)
    mock_visit_id = "466d94fc-34fe-4bad-a9e8-ccdd771701a0"
    
    mock_assessment = TriageAssessment(
        id=uuid.uuid4(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
        visit_id=uuid.UUID(mock_visit_id),
        patient_id="patient-456",
        blood_pressure="120/80",
        temperature=36.6,
        pulse=72,
        oxygen_saturation=98.0,
        respiratory_rate=16,
        weight=70.0,
        presenting_complaint="Headache",
        triage_category="non_urgent"
    )
    mock_db.existing_assessment = mock_assessment
    
    response = client.get(f"/api/v1/triage/assessments/visit/{mock_visit_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["visit_id"] == mock_visit_id
    assert data["presenting_complaint"] == "Headache"
