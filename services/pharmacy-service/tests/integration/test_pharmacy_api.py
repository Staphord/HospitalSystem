from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.services import pharmacy as pharmacy_svc


def test_health(pharmacist_client: TestClient):
    response = pharmacist_client.get("/health")
    assert response.status_code == 200
    assert response.json()["service"] == "pharmacy-service"


def test_get_queue(pharmacist_client: TestClient):
    response = pharmacist_client.get("/api/v1/pharmacy/queue")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    assert len(data["queue"]) == 1


def test_get_inventory_list(pharmacist_client: TestClient):
    response = pharmacist_client.get("/api/v1/pharmacy/inventory")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert any(item["drug_name"] == "Amoxicillin" for item in data["items"])


def test_get_low_stock_alerts_before_inventory_id_route(pharmacist_client: TestClient):
    response = pharmacist_client.get("/api/v1/pharmacy/inventory/low-stock-alerts")
    assert response.status_code == 200
    assert response.json()["alert_count"] == 1


def test_dispense_validation_rejects_past_expiry(pharmacist_client: TestClient):
    response = pharmacist_client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": str(pharmacy_svc.STUB_PRESCRIPTION_PENDING_ID),
            "visit_id": str(pharmacy_svc.STUB_VISIT_ID),
            "drug_name": "Amoxicillin",
            "batch_number": "BATCH-2025-089",
            "expiry_date": "2020-01-01",
            "quantity_dispensed": 21,
            "unit": "tablets",
            "interaction_alert_acknowledged": True,
        },
    )
    assert response.status_code == 422


def test_dispense_success(pharmacist_client: TestClient):
    response = pharmacist_client.post(
        "/api/v1/pharmacy/dispense",
        json={
            "prescription_id": str(pharmacy_svc.STUB_PRESCRIPTION_PENDING_ID),
            "visit_id": str(pharmacy_svc.STUB_VISIT_ID),
            "drug_name": "Amoxicillin",
            "batch_number": "BATCH-2025-089",
            "expiry_date": (date.today() + timedelta(days=365)).isoformat(),
            "quantity_dispensed": 21,
            "unit": "tablets",
            "interaction_alert_acknowledged": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["quantity_dispensed"] == 21


def test_unauthenticated_request_rejected():
    from app.main import app

    client = TestClient(app)
    response = client.get("/api/v1/pharmacy/queue")
    assert response.status_code == 401
