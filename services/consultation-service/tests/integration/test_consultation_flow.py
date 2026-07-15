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
    Prescription,
    Queue,
    FeeSchedule,
    Bill,
    BillItem,
    LabResult,
    RadiologyReport,
)


class MockAsyncSession:
    def __init__(self):
        self.patients = {}
        self.visits = {}
        self.triages = {}
        self.consultations = {}
        self.diagnoses = {}
        self.investigations = {}
        self.prescriptions = {}
        self.queues = {}
        self.fee_schedules = {}
        self.bills = {}
        self.bill_items = {}
        self.lab_results = {}
        self.radiology_reports = {}
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

            def all(self):
                return [(x, None, None, None) for x in self._data]

            def scalar(self):
                return self._data[0] if self._data else None

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
            elif "final" in stmt_str:
                result_list = [d for d in result_list if d.diagnosis_type == "final"]
            return MockResult(result_list)
        elif "from investigation_requests" in stmt_str:
            return MockResult(list(self.investigations.values()))
        elif "from prescriptions" in stmt_str:
            return MockResult(list(self.prescriptions.values()))
        elif "from queues" in stmt_str:
            result_list = list(self.queues.values())
            # For queue endpoint we join visit/patient/triage, but MockResult expects list
            # We mock the select statement with returned tuple rows in .all() override
            class JoinResult:
                def __init__(self, q_list, s_self):
                    self.q_list = q_list
                    self.s_self = s_self
                def all(self):
                    rows = []
                    for q in self.q_list:
                        v = self.s_self.visits.get(q.visit_id)
                        p = self.s_self.patients.get(q.patient_id)
                        t = self.s_self.triages.get(q.visit_id)
                        rows.append((q, v, p, t))
                    return rows
                def scalars(self):
                    class MockScalars:
                        def __init__(self, data):
                            self.data = data
                        def first(self):
                            return self.data[0] if self.data else None
                        def all(self):
                            return self.data
                    return MockScalars(self.q_list)
                def scalar(self):
                    return len(self.q_list)
            return JoinResult(result_list, self)
        elif "from fee_schedules" in stmt_str:
            return MockResult(list(self.fee_schedules.values()))
        elif "from bills" in stmt_str:
            return MockResult(list(self.bills.values()))
        elif "from bill_items" in stmt_str:
            return MockResult(list(self.bill_items.values()))
        elif "from lab_results" in stmt_str:
            return MockResult(list(self.lab_results.values()))
        elif "from radiology_reports" in stmt_str:
            return MockResult(list(self.radiology_reports.values()))

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
        elif isinstance(obj, Prescription):
            self.prescriptions[obj.id] = obj
        elif isinstance(obj, Queue):
            self.queues[obj.queue_id] = obj
        elif isinstance(obj, FeeSchedule):
            self.fee_schedules[obj.id] = obj
        elif isinstance(obj, Bill):
            self.bills[obj.visit_id] = obj
            self.bills[obj.bill_id] = obj
        elif isinstance(obj, BillItem):
            self.bill_items[obj.bill_item_id] = obj

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        pass

    async def flush(self):
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
        triage_id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=p_id,
        triage_nurse_id=uuid.uuid4(),
        blood_pressure_systolic=120,
        blood_pressure_diastolic=80,
        temperature=37.0,
        pulse_rate=75,
        oxygen_saturation=98.0,
        respiratory_rate=16,
        weight_kg=70.0,
        chief_complaint="Fever and headache",
        triage_category="urgent",
        triage_notes="Notes text",
        assessed_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
    )
    # Seed fee schedule
    fee = FeeSchedule(
        id=uuid.uuid4(),
        item_code="CONS-001",
        item_name="Consultation Fee",
        item_type="consultation",
        standard_price=1000.0,
        insurance_price=800.0,
        is_active=True,
        effective_from=date.today()
    )
    # Seed doctor queue entry
    doctor_queue = Queue(
        queue_id=uuid.uuid4(),
        visit_id=v_id,
        patient_id=p_id,
        queue_type="doctor",
        queue_number="D-001",
        priority="urgent",
        status="waiting",
        created_at=datetime.utcnow()
    )
    session.add(patient)
    session.add(visit)
    session.add(triage)
    session.add(fee)
    session.add(doctor_queue)
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


async def override_get_current_active_user():
    from app.core.security import TokenPayload
    return TokenPayload(
        sub="doctor-sub-123",
        preferred_username="doctor_test",
        email="doctor@example.com",
        realm_access={"roles": ["doctor"]},
        raw={"type": "user", "role": "doctor"}
    )


@pytest.fixture(autouse=True)
def setup_overrides(mock_db):
    from app.core.security import get_current_active_user
    async def override_get_tenant_db():
        yield mock_db

    app.dependency_overrides[get_current_tenant] = override_get_current_tenant
    app.dependency_overrides[get_tenant_db] = override_get_tenant_db
    app.dependency_overrides[get_current_active_user] = override_get_current_active_user
    yield
    app.dependency_overrides.clear()



# 1. Test Queue Endpoint
def test_get_doctor_queue_endpoint(mock_db):
    client = TestClient(app)
    response = client.get(
        "/api/v1/consultation/queue",
        headers={"Authorization": "Bearer test_token"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["queue_number"] == "D-001"
    assert data[0]["full_name"] == "John Doe"
    assert data[0]["chief_complaint"] == "Fever and headache"


# 2. Test Open Encounter Endpoint
def test_open_encounter_endpoint(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]

    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_patch.return_value = mock_resp

        response = client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["visit_id"] == str(v_id)
        assert data["consultation_status"] == "in_progress"
        assert len(mock_db.consultations) > 0

        # Check that visit status was updated to in_consultation
        visit = mock_db.visits[v_id]
        assert visit.status == "in_consultation"


# 3. Test Full Encounter View & Update Clinical Notes
def test_encounter_view_and_notes(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]
    p_id = list(mock_db.patients.keys())[0]

    # Open encounter
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_resp = MagicMock()
        mock_patch.return_value = mock_resp
        client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )

    # Get Consultation ID
    cons = list(mock_db.consultations.values())[0]

    # Update notes
    notes_payload = {
        "presenting_history": "Presents with migraine",
        "examination_findings": "Normal vitals",
        "clinical_impression": "Migraine headache"
    }
    response = client.put(
        f"/api/v1/consultation/{cons.id}/notes",
        json=notes_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert response.status_code == 200
    assert response.json()["history_of_presenting_illness"] == "Presents with migraine"

    # Get encounter view
    response2 = client.get(
        f"/api/v1/consultation/encounters/{v_id}",
        headers={"Authorization": "Bearer test_token"},
    )
    assert response2.status_code == 200
    data = response2.json()
    assert data["patient"]["full_name"] == "John Doe"
    assert data["consultation"]["history_of_presenting_illness"] == "Presents with migraine"


# 4. Test Diagnoses provisional limit & final diagnosis gates
def test_diagnosis_endpoints_and_gates(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]

    # Create consultation
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )
    cons = list(mock_db.consultations.values())[0]

    # Add provisional diagnosis
    diag_payload = {
        "diagnosis_type": "provisional",
        "code": "A00",
        "description": "Cholera",
    }
    resp1 = client.post(
        f"/api/v1/consultation/{cons.id}/diagnoses",
        json=diag_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp1.status_code == 201

    # Double provisional should fail
    resp2 = client.post(
        f"/api/v1/consultation/{cons.id}/diagnoses",
        json=diag_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp2.status_code == 400

    # Add final diagnosis (should pass since no investigations exist)
    resp3 = client.post(
        f"/api/v1/consultation/{cons.id}/diagnoses",
        json={
            "diagnosis_type": "final",
            "code": "A00",
            "description": "Cholera final",
        },
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp3.status_code == 201


# 5. Test Investigations workflow
def test_investigations_and_cancellation(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]

    # Create consultation
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )
    cons = list(mock_db.consultations.values())[0]

    # Add provisional diagnosis first (required by router)
    client.post(
        f"/api/v1/consultation/{cons.id}/diagnoses",
        json={"diagnosis_type": "provisional", "code": "A00", "description": "Cholera"},
        headers={"Authorization": "Bearer test_token"},
    )

    # Request investigation
    inv_payload = {
        "request_type": "lab",
        "test_name": "Blood Culture",
        "test_code": "LAB-001",
        "clinical_indication": "Fever",
        "urgency": "urgent"
    }
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        resp1 = client.post(
            f"/api/v1/consultation/{cons.id}/investigations",
            json=inv_payload,
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp1.status_code == 201
        inv_data = resp1.json()
        assert inv_data["test_name"] == "Blood Culture"
        assert inv_data["status"] == "pending"

    # Cancel investigation request
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        resp2 = client.delete(
            f"/api/v1/consultation/{cons.id}/investigations/{inv_data['id']}",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp2.status_code == 200
        assert resp2.json()["status"] == "cancelled"


# 6. Test Prescription workflow
def test_prescription_endpoints(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]

    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )
    cons = list(mock_db.consultations.values())[0]

    presc_payload = {
        "drug_name": "Paracetamol",
        "dose": "500mg",
        "frequency": "three times daily",
        "duration": "5 days",
        "route": "oral",
        "instructions": "Take after food"
    }

    resp1 = client.post(
        f"/api/v1/consultation/{cons.id}/prescriptions",
        json=presc_payload,
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp1.status_code == 201
    p_data = resp1.json()
    assert p_data["drug_name"] == "Paracetamol"

    # Get list
    resp2 = client.get(
        f"/api/v1/consultation/{cons.id}/prescriptions",
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp2.status_code == 200
    assert len(resp2.json()) == 1

    # Cancel prescription
    resp3 = client.delete(
        f"/api/v1/consultation/{cons.id}/prescriptions/{p_data['id']}",
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp3.status_code == 200
    assert resp3.json()["status"] == "cancelled"


# 7. Test Consultation Complete & Disposition Gates
def test_complete_consultation_gates(mock_db):
    client = TestClient(app)
    v_id = list(mock_db.visits.keys())[0]

    # 1. Open encounter
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        client.post(
            f"/api/v1/consultation/encounters/{v_id}/open",
            headers={"Authorization": "Bearer test_token"},
        )
    cons = list(mock_db.consultations.values())[0]

    # Complete without final diagnosis should fail
    resp_fail1 = client.post(
        f"/api/v1/consultation/{cons.id}/complete",
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp_fail1.status_code == 400
    assert "At least one final diagnosis" in resp_fail1.json()["detail"]

    # Add final diagnosis
    client.post(
        f"/api/v1/consultation/{cons.id}/diagnoses",
        json={"diagnosis_type": "final", "code": "A00", "description": "Cholera final"},
        headers={"Authorization": "Bearer test_token"},
    )

    # Complete without disposition should fail
    resp_fail2 = client.post(
        f"/api/v1/consultation/{cons.id}/complete",
        headers={"Authorization": "Bearer test_token"},
    )
    assert resp_fail2.status_code == 400
    assert "disposition must be recorded" in resp_fail2.json()["detail"]

    # Set disposition
    disp_payload = {
        "disposition": "outpatient",
        "referral_type": None,
        "referral_notes": None
    }
    client.put(
        f"/api/v1/consultation/{cons.id}/disposition",
        json=disp_payload,
        headers={"Authorization": "Bearer test_token"},
    )

    # Complete should now succeed
    with patch("httpx.AsyncClient.patch") as mock_patch:
        mock_patch.return_value = MagicMock(status_code=200)
        resp_ok = client.post(
            f"/api/v1/consultation/{cons.id}/complete",
            headers={"Authorization": "Bearer test_token"},
        )
        assert resp_ok.status_code == 200
        assert resp_ok.json()["consultation_status"] == "completed"
