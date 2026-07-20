import pytest
from datetime import datetime, timezone
from conftest import (
    TEST_PATIENT_ID,
    TEST_VISIT_ID,
    TEST_REQUEST_PENDING_ID,
    TEST_REQUEST_COMPLETED_ID,
)


def test_list_lab_requests(lab_tech_client):
    response = lab_tech_client.get("/api/v1/laboratory/requests")
    assert response.status_code == 200
    data = response.json()
    assert "requests" in data
    assert len(data["requests"]) >= 2
    # Verify stat/urgent appears before routine
    first_req = data["requests"][0]
    assert first_req["request_id"] == str(TEST_REQUEST_PENDING_ID)
    assert first_req["urgency"] == "urgent"


def test_get_lab_request_detail(lab_tech_client):
    response = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == str(TEST_REQUEST_PENDING_ID)
    assert data["test_name"] == "Full Blood Count"
    assert data["patient"]["full_name"] == "Jane Mwita"
    assert data["patient"]["patient_number"] == "P-000"
    assert data["specimen"] is None
    assert data["result"] is None


def test_specimen_lifecycle(lab_tech_client):
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Collect Specimen
    payload = {
        "specimen_type": "blood",
        "collection_site": "antecubital vein, left arm",
        "specimen_label": "SPE-20260315-001",
        "collected_at": now_iso,
    }
    response = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json=payload,
    )
    assert response.status_code == 201
    specimen = response.json()
    assert specimen["specimen_id"] is not None
    assert specimen["status"] == "collected"

    # Check request status updated to specimen_collected
    req_resp = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp.json()["status"] == "specimen_collected"

    # Duplicate specimen collection fails (request status not pending)
    resp_dup = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json=payload,
    )
    assert resp_dup.status_code in (409, 422)

    # 2. Receive Specimen
    resp_rcv = lab_tech_client.patch(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json={"status": "received", "received_at": now_iso},
    )
    assert resp_rcv.status_code == 200
    assert resp_rcv.json()["status"] == "received"

    # 3. Reject Specimen -> Reverts request to pending
    resp_rej = lab_tech_client.patch(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json={"status": "rejected", "rejection_reason": "Haemolysed sample — recollection required"},
    )
    assert resp_rej.status_code == 200
    assert resp_rej.json()["status"] == "rejected"
    assert resp_rej.json()["request_status"] == "pending"

    # Verify audit trail contains rejected specimen
    resp_audit = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen")
    assert resp_audit.status_code == 200
    assert len(resp_audit.json()["specimens"]) == 1
    assert resp_audit.json()["specimens"][0]["status"] == "rejected"

    # 4. Re-collect Specimen on pending request
    payload_recollect = {
        "specimen_type": "blood",
        "collection_site": "antecubital vein, right arm",
        "specimen_label": "SPE-20260315-002",
        "collected_at": now_iso,
    }
    resp_recollect = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json=payload_recollect,
    )
    assert resp_recollect.status_code == 201

    # Verify audit trail now contains 2 specimens
    resp_audit2 = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen")
    assert resp_audit2.status_code == 200
    assert len(resp_audit2.json()["specimens"]) == 2


def test_results_lifecycle_and_verification(lab_tech_client):
    # 1. Enter Results
    payload = {
        "result_value": "8.2",
        "unit": "g/dL",
        "reference_range": "12.0 – 16.0 g/dL",
        "is_critical": True,
        "result_notes": "Severely low haemoglobin",
        "specimen_type": "blood",
        "specimen_label": "SPE-20260315-002",
    }
    response = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/result",
        json=payload,
    )
    assert response.status_code == 201
    res = response.json()
    assert res["result_id"] is not None
    assert res["status"] == "resulted"
    assert res["is_critical"] is True
    assert res["critical_notified_at"] is not None
    result_id = res["result_id"]

    # Verify request status is in_progress during result stage
    req_resp = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp.json()["status"] == "in_progress"

    # 2. Amend Result
    update_payload = {
        "result_value": "8.5",
        "result_notes": "Amended after rerun confirmation.",
    }
    resp_up = lab_tech_client.patch(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/result",
        json=update_payload,
    )
    assert resp_up.status_code == 200
    assert resp_up.json()["result_value"] == "8.5"

    # 3. Read Result Detail
    resp_get = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/result")
    assert resp_get.status_code == 200
    assert resp_get.json()["result_value"] == "8.5"

    # 4. Verify Result
    resp_ver = lab_tech_client.post(f"/api/v1/laboratory/results/{result_id}/verify")
    assert resp_ver.status_code == 200
    assert resp_ver.json()["status"] == "verified"
    assert resp_ver.json()["request_status"] == "completed"

    # Verify parent request is now completed
    req_resp2 = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp2.json()["status"] == "completed"

    # Verify edits are locked after verification
    resp_fail = lab_tech_client.patch(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/result",
        json={"result_value": "9.0"},
    )
    assert resp_fail.status_code == 422


def test_billing(lab_tech_client):
    # Post lab charge for completed request TEST_REQUEST_PENDING_ID
    bill_payload = {
        "unit_price": 15000.00,
        "description": "Full Blood Count (FBC)",
    }
    resp = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/bill",
        json=bill_payload,
    )
    assert resp.status_code == 201
    b_data = resp.json()
    assert b_data["item_type"] == "lab"
    assert b_data["unit_price"] == 15000.00
    assert b_data["reference_id"] == str(TEST_REQUEST_PENDING_ID)

    # Duplicate billing returns 409 Conflict
    resp_dup = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/bill",
        json=bill_payload,
    )
    assert resp_dup.status_code == 409


def test_doctor_visit_results(doctor_client):
    # Doctor reads verified results for visit
    resp = doctor_client.get(f"/api/v1/laboratory/visits/{TEST_VISIT_ID}/results")
    assert resp.status_code == 200
    data = resp.json()
    assert data["visit_id"] == str(TEST_VISIT_ID)
    assert len(data["results"]) == 1
    assert data["results"][0]["request_id"] == str(TEST_REQUEST_PENDING_ID)
    assert data["results"][0]["result_value"] == "8.5"


def test_rbac_restrictions(unauthorized_client):
    resp_reqs = unauthorized_client.get("/api/v1/laboratory/requests")
    assert resp_reqs.status_code == 403

    resp_detail = unauthorized_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert resp_detail.status_code == 403

    resp_doc = unauthorized_client.get(f"/api/v1/laboratory/visits/{TEST_VISIT_ID}/results")
    assert resp_doc.status_code == 403
