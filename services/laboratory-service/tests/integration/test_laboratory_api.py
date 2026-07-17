import pytest
from uuid import uuid4
from conftest import (
    TEST_QUEUE_ID,
    TEST_PATIENT_ID,
    TEST_VISIT_ID,
    TEST_REQUEST_PENDING_ID,
    TEST_REQUEST_COMPLETED_ID,
)


def test_get_lab_queue(lab_tech_client):
    response = lab_tech_client.get("/api/v1/laboratory/queue?status=waiting")
    assert response.status_code == 200
    data = response.json()
    assert "queue" in data
    assert len(data["queue"]) == 1
    assert data["queue"][0]["queue_id"] == str(TEST_QUEUE_ID)
    assert data["queue"][0]["patient_name"] == "Jane Mwita"
    assert data["queue"][0]["test_name"] == "Full Blood Count"


def test_call_queue_patient(lab_tech_client):
    response = lab_tech_client.post(f"/api/v1/laboratory/queue/{TEST_QUEUE_ID}/call")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "in_progress"
    assert data["called_at"] is not None


def test_skip_queue_patient(lab_tech_client):
    # Setup another queue item or test on existing
    response = lab_tech_client.post(f"/api/v1/laboratory/queue/{TEST_QUEUE_ID}/skip")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "skipped"


def test_get_lab_request_detail(lab_tech_client):
    response = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert response.status_code == 200
    data = response.json()
    assert data["request_id"] == str(TEST_REQUEST_PENDING_ID)
    assert data["test_name"] == "Full Blood Count"
    assert data["patient"]["full_name"] == "Jane Mwita"
    assert data["visit"]["visit_number"] == "V-20260315-042"


def test_specimen_lifecycle(lab_tech_client):
    # 1. Collect Specimen
    payload = {
        "specimen_type": "Whole Blood",
        "collection_site": "Left antecubital fossa",
    }
    response = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/specimen",
        json=payload,
    )
    assert response.status_code == 201
    specimen = response.json()
    assert specimen["specimen_id"] is not None
    assert specimen["status"] == "collected"
    assert specimen["specimen_type"] == "Whole Blood"
    specimen_id = specimen["specimen_id"]

    # Check request status updated to specimen_collected
    req_resp = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp.json()["status"] == "specimen_collected"

    # 2. Receive Specimen
    resp_rcv = lab_tech_client.patch(f"/api/v1/laboratory/specimens/{specimen_id}/receive")
    assert resp_rcv.status_code == 200
    assert resp_rcv.json()["status"] == "received"
    assert resp_rcv.json()["received_at"] is not None

    # 3. Process Specimen (transitions parent request to in_progress)
    resp_proc = lab_tech_client.patch(
        f"/api/v1/laboratory/specimens/{specimen_id}/status",
        json={"status": "processing"},
    )
    assert resp_proc.status_code == 200
    assert resp_proc.json()["status"] == "processing"

    req_resp2 = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp2.json()["status"] == "in_progress"

    # 4. Reject Specimen
    resp_rej = lab_tech_client.patch(
        f"/api/v1/laboratory/specimens/{specimen_id}/reject",
        json={"rejection_reason": "Hemolyzed specimen"},
    )
    assert resp_rej.status_code == 200
    assert resp_rej.json()["status"] == "rejected"
    assert resp_rej.json()["rejection_reason"] == "Hemolyzed specimen"


def test_results_workflow(lab_tech_client):
    # 1. Enter Results
    payload = {
        "specimen_type": "Blood",
        "result_value": "14.2 g/dL",
        "unit": "g/dL",
        "reference_range": "12.0 - 16.0",
        "is_critical": False,
        "result_notes": "Normal hemoglobin",
    }
    response = lab_tech_client.post(
        f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}/results",
        json=payload,
    )
    assert response.status_code == 201
    res = response.json()
    assert res["result_id"] is not None
    assert res["status"] == "resulted"
    result_id = res["result_id"]

    # Verify request is now completed
    req_resp = lab_tech_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert req_resp.json()["status"] == "completed"

    # 2. Update Results
    update_payload = {
        "result_value": "8.5 g/dL",
        "unit": "g/dL",
        "reference_range": "12.0 - 16.0",
        "is_critical": True,
        "result_notes": "Corrected value - critical alert triggered",
    }
    resp_up = lab_tech_client.patch(
        f"/api/v1/laboratory/results/{result_id}",
        json=update_payload,
    )
    assert resp_up.status_code == 200
    assert resp_up.json()["is_critical"] is True
    assert resp_up.json()["critical_notified_at"] is not None

    # 3. Read Result
    resp_get = lab_tech_client.get(f"/api/v1/laboratory/results/{result_id}")
    assert resp_get.status_code == 200
    assert resp_get.json()["result_value"] == "8.5 g/dL"

    # 4. Verify Result
    resp_ver = lab_tech_client.patch(f"/api/v1/laboratory/results/{result_id}/verify")
    assert resp_ver.status_code == 200
    assert resp_ver.json()["status"] == "verified"
    assert resp_ver.json()["verified_by"] is not None

    # Verify edits are locked after verification
    resp_fail = lab_tech_client.patch(
        f"/api/v1/laboratory/results/{result_id}",
        json=update_payload,
    )
    assert resp_fail.status_code == 409


def test_patient_history_sharing(lab_tech_client, doctor_client):
    # Lab technician can read history
    resp_lab = lab_tech_client.get(f"/api/v1/laboratory/patients/{TEST_PATIENT_ID}/results")
    assert resp_lab.status_code == 200
    assert "results" in resp_lab.json()

    # Doctor can also read patient history
    resp_doc = doctor_client.get(f"/api/v1/laboratory/patients/{TEST_PATIENT_ID}/results")
    assert resp_doc.status_code == 200
    assert len(resp_doc.json()["results"]) >= 0


def test_rbac_restrictions(unauthorized_client):
    # Unauthorized client gets forbidden
    resp_q = unauthorized_client.get("/api/v1/laboratory/queue")
    assert resp_q.status_code == 403

    resp_req = unauthorized_client.get(f"/api/v1/laboratory/requests/{TEST_REQUEST_PENDING_ID}")
    assert resp_req.status_code == 403

    # History read is also barred for receptionist role
    resp_hist = unauthorized_client.get(f"/api/v1/laboratory/patients/{TEST_PATIENT_ID}/results")
    assert resp_hist.status_code == 403
