# Reception Module — Developer Guide

This document covers the **reception-service** microservice (the orchestrator/gateway proxy) and the reception database logic in the **visit-service** (the database-backed store for visits, queues, and insurance).

---

## Overview

| Item | Value |
|---|---|
| Service folder | `HospitalSystem/services/reception-service` (Orchestrator) <br> `HospitalSystem/services/visit-service` (DB Backend) |
| Container names | `hospital-reception-service` <br> `hospital-visit-service` |
| Ports | **8010** (reception-service orchestrator) <br> **8006** (visit-service backend) |
| API prefix | `/api/v1/reception` |
| Gateway route | `http://localhost:8000/api/v1/reception/*` |
| Auth role required | `receptionist` or `hospital_admin` (Keycloak JWT `realm_access.roles`) |
| Tenant resolution | JWT `tenant_id` → database connection layer |

---

## Architecture

The reception module utilizes an orchestrator proxy pattern. The frontend talks to `/api/v1/reception/*` via the API gateway. The `reception-service` acts as a routing orchestrator that performs no direct database writes itself, but instead delegates to the internal services:

```
           Client / Frontend (React)
                      ↓
           API Gateway (port 8000)  →  /api/v1/reception/*
                      ↓
       reception-service (port 8010)
         ↙                        ↘
  patient-service (8005)     visit-service (8006)
  (real DB: patients)        (real DB: patient_insurance, visits, queues)
```

---

## What Has Been Implemented

### Group 1 — Patient Registry
* **`POST /api/v1/reception/patients`**
  * **Role:** `receptionist` / `hospital_admin`
  * **Description:** Register a new patient in the system.
  * **Behavior:** Proxies request to `patient-service`. If the `national_id` is already registered, maps downstream `409 Conflict` to a clean `422 Unprocessable Entity` validation error. Validates `date_of_birth` is not in the future.
* **`GET /api/v1/reception/patients`**
  * **Description:** Search and list patients.
  * **Behavior:** Maps page-based pagination (`search`, `page`, `page_size`) to offset-based parameters (`query`, `limit`, `offset`) before requesting from `patient-service`.
* **`GET /api/v1/reception/patients/{id}`**
  * **Description:** Get patient details by ID.

### Group 2 — Patient Insurance
* **`POST /api/v1/reception/patients/{patient_id}/insurance`**
  * **Description:** Adds an insurance policy to a registered patient.
  * **Behavior:** Verifies the patient exists first via `patient-service` (returns 422 if not found), then requests `visit-service` to insert the policy. Starts with `verification_status = pending`.
* **`GET /api/v1/reception/patients/{patient_id}/insurance`**
  * **Description:** Lists all insurance policies held by a patient, sorted newest-first.
* **`PATCH /api/v1/reception/insurance/{insurance_id}/verify`**
  * **Description:** Manually records insurance policy verification outcome.
  * **Behavior:** Updates status to `verified` or `rejected`, sets `verified_at` to the current timestamp, and unlocks/locks corresponding flags.

### Group 3 — Visit Creation
* **`POST /api/v1/reception/visits`**
  * **Description:** Creates a visit for a patient and automatically enqueues them into `triage`.
  * **Behavior:** Accepts an `insurance_id` if the payment type is `insurance`. Validates that the policy exists, belongs to the patient, and is active. Generates `visit_number` and `queue_number` and returns a nested `queue` summary.
* **`GET /api/v1/reception/visits/{visit_id}`**
  * **Description:** Retrieve visit detail enriched with nested patient and insurance metadata.
  * **Behavior:** Fetches visit from `visit-service` then concurrently resolves patient details (`patient-service`) and policy details (`visit-service`) using `asyncio.gather` for minimal response latency.

### Group 4 — Reception Queue View (Worklist)
* **`GET /api/v1/reception/queue`**
  * **Description:** Active worklist for receptionists showing queued patients.
  * **Behavior:** Fetches queue entries of type `triage` (or other sections) and concurrently resolves full patient summaries and visit metadata for each entry.

---

## Running the Services

### Docker compose

From `HospitalSystem/infrastructure`:
```bash
docker compose up -d --build reception-service visit-service
```

Health check reception-service:
```bash
curl http://localhost:8010/health
```

Health check visit-service:
```bash
curl http://localhost:8006/health
```

---

## Running Tests

Verify correctness of schemas, parameters mapping, database persistence, and integration.

### 1. Test visit-service (DB and Service Layer)
Run from `services/visit-service`:
```bash
pytest tests -v
```
*Runs 42 tests checking visit number generation, triage auto-routing, policy creation, and status transitions against SQLite.*

### 2. Test reception-service (Orchestrator Layer)
Run from `services/reception-service`:
```bash
pytest tests -v
```
*Runs 52 tests checking payload validations, 409 error mapping, and asynchronous parallel lookup stitching.*

---

## Swagger UI Documentation

Once containers are running, developer Swagger interfaces are available locally:
* **reception-service:** `http://localhost:8010/docs`
* **visit-service:** `http://localhost:8006/docs`
