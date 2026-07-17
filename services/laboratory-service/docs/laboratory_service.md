# Laboratory Service — Developer Guide

This document covers the **laboratory-service** microservice: what has been built, how to run it locally, how tenant database migrations work, how to manually seed data in Docker PostgreSQL for testing, and how to test the API.

---

## Overview

| Item | Value |
|------|-------|
| Service folder | `HospitalSystem/services/laboratory-service` |
| Container name | `hospital-laboratory-service` |
| Port | **8013** |
| API prefix | `/api/v1/laboratory` |
| Gateway route | `http://localhost:8000/api/v1/laboratory/*` |
| Direct Swagger | `http://localhost:8013/docs` |
| Auth role required | `lab_technician` (Keycloak JWT `realm_access.roles`) |
| Exception | `GET /patients/{patient_id}/results` — `doctor` role also allowed |
| Tenant resolution | JWT `tenant_id` → master DB `tenants` table → encrypted tenant DSN |

Laboratory uses the **per-tenant PostgreSQL** pattern: each hospital has its own database named `tenant_{tenant_id}` (e.g. `tenant_hosp-4943401c`). Clinical/lab tables live in the tenant DB, not in `hospital_master`.

---

## Architecture

```
Client / Frontend
    ↓
API Gateway (port 8000)  →  /api/v1/laboratory/*
    ↓
laboratory-service (port 8013)
    ↓
hospital_master (tenants table — resolve tenant DSN)
    ↓
tenant_{tenant_id} (investigation_requests, specimens, lab_results, queues, …)
```

---

## Project Structure

```
laboratory-service/
├── app/
│   ├── api/v1/
│   │   ├── router.py          # All 13 HTTP endpoints
│   │   └── schemas.py         # Pydantic request/response models
│   ├── core/                  # Config, security, tenant auth, middleware
│   ├── db/                    # Master + tenant async DB sessions
│   ├── events/                # RabbitMQ subscriber (background task)
│   ├── models/
│   │   └── laboratory.py      # Specimen, LabResult, + read-only ORM models
│   ├── services/
│   │   └── laboratory.py      # Full DB logic: queue, specimens, results, history
│   ├── main.py
│   └── dependencies.py
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
│   └── laboratory_service.md  # This file
├── Dockerfile
├── requirements.txt
└── pytest.ini
```

**Shared tenant migrations** (not inside laboratory-service) live at:

```
HospitalSystem/migrations/tenant/
├── alembic.ini
├── env.py
└── versions/
    ├── 0001_initial_tenant_schema.py
    ├── …
    ├── 0010_create_laboratory_tables.py   ← specimens + lab_results tables
    └── 0011_add_urgency_to_investigation_requests.py  ← urgency column
```

---

## What Has Been Done

### Phase 1 — Full Implementation (complete)

All **13 endpoints** are fully implemented with real DB queries, Pydantic schemas, OpenAPI tags, role enforcement, and tenant middleware. All endpoints hit the tenant PostgreSQL database — there are no stubs.

#### 1. Queue & Worklist Endpoints
* **`GET /queue`**
  * **Role:** `lab_technician`
  * **Description:** Retrieves the active queue of laboratory investigation requests for a specific date (defaults to today). Can filter by status (`waiting` or `in_progress`).
  * **Behavior:** Joins the `queues`, `investigation_requests`, and `patients` tables. Returns results sorted by urgency (highest weight to `stat`, then `urgent`, then `routine`) and queue registration time.
* **`POST /queue/{queue_id}/call`**
  * **Role:** `lab_technician`
  * **Description:** Initiates service for a patient by calling them from the laboratory queue.
  * **Behavior:** Transitions the queue item's status to `in_progress` and updates the `called_at` timestamp.
* **`POST /queue/{queue_id}/skip`**
  * **Role:** `lab_technician`
  * **Description:** Skips a patient in the queue worklist.
  * **Behavior:** Transitions the queue item's status to `skipped`.

#### 2. Request Details Endpoints
* **`GET /requests/{request_id}`**
  * **Role:** `lab_technician`
  * **Description:** Fetches the detailed dossier of a laboratory investigation request.
  * **Behavior:** Aggregates and returns the specific request metadata, patient demographics (full name, gender, date of birth, contact details, address, allergies), and visit context (visit number, date, payment type).

#### 3. Specimen Management Endpoints
* **`POST /requests/{request_id}/specimen`**
  * **Role:** `lab_technician`
  * **Description:** Logs the collection of a specimen (e.g. blood, urine, swab) for a request.
  * **Behavior:** Inserts a row in the `specimens` table with status `collected` and updates the `investigation_requests.status` to `sample_collected`.
* **`PATCH /specimens/{specimen_id}/receive`**
  * **Role:** `lab_technician`
  * **Description:** Acknowledges receipt of a specimen in the laboratory.
  * **Behavior:** Updates `specimens.status` to `received` and sets `received_at` to the current time.
* **`PATCH /specimens/{specimen_id}/status`**
  * **Role:** `lab_technician`
  * **Description:** Manually adjusts the status of a specimen.
* **`PATCH /specimens/{specimen_id}/reject`**
  * **Role:** `lab_technician`
  * **Description:** Rejects a collected specimen due to issues like contamination or insufficient volume.
  * **Behavior:** Sets `specimens.status` to `rejected`, records the `rejection_reason`, and leaves the investigation request status intact (allowing subsequent collections to spawn a new specimen row).

#### 4. Results Entry & Validation Endpoints
* **`POST /requests/{request_id}/results`**
  * **Role:** `lab_technician`
  * **Description:** Records findings, reference ranges, and interpretations for a request.
  * **Behavior:** Inserts a row in the `lab_results` table, updates `investigation_requests.status` to `completed`, and marks the queue item as `completed` with a `completed_at` timestamp.
* **`PATCH /results/{result_id}`**
  * **Role:** `lab_technician`
  * **Description:** Edits a drafted laboratory result prior to verification.
  * **Constraint:** If the result has already been verified, edits are rejected with a `400 Bad Request`.
* **`GET /results/{result_id}`**
  * **Role:** `lab_technician`
  * **Description:** Fetches details of a specific laboratory result entry.
* **`PATCH /results/{result_id}/verify`**
  * **Role:** `lab_technician`
  * **Description:** Authorizes and signs off on the laboratory findings.
  * **Behavior:** Sets `is_verified` to `true`, records `verified_by` and `verified_at`, and permanently locks the record against edits.

#### 5. Patient Results History Endpoints
* **`GET /patients/{patient_id}/results`**
  * **Role:** `doctor` OR `lab_technician`
  * **Description:** Retrieves the chronological laboratory test history for a specific patient.
  * **Behavior:** Allows doctors and technicians to view historical diagnostic results to track patient progress across visits.

---

## Running the Service

### Docker (recommended)

From `HospitalSystem/infrastructure`:

```bash
docker compose up -d --build laboratory-service
```

Health check:

```bash
curl http://localhost:8013/health
```

Swagger UI:

```
http://localhost:8013/docs
```

Via gateway:

```
http://localhost:8000/api/v1/laboratory/queue
```

### Local (without Docker)

```bash
cd HospitalSystem/services/laboratory-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8013 --reload
```

Copy `.env.example` to `.env` and set `DATABASE_URL`, Keycloak config, and `TENANT_DB_ENCRYPTION_KEY` to match your environment.

---

## Authentication for API Testing

1. Log in via the API gateway:

   ```bash
   curl -X POST http://localhost:8000/api/v1/auth/login \
     -H "Content-Type: application/json" \
     -d '{"username": "lab_tech_user", "password": "yourpassword"}'
   ```

2. Use the returned access token:

   ```
   Authorization: Bearer <token>
   ```

3. The authenticated user must have the **`lab_technician`** role assigned in Keycloak.
4. The JWT must include **`tenant_id`** matching an active tenant.

---

## Manual Database Seeding & API Testing via Docker PostgreSQL

If you are developing locally and want to test endpoints end-to-end, you can seed mock patients, visits, consultations, and requests inside the Dockerized PostgreSQL database.

### Step 1: Access the PostgreSQL Shell
Connect to the master database cluster inside the Docker container:
```bash
docker compose exec -it postgres-master psql -U postgres
```

### Step 2: Connect to your Tenant Database
List databases (`\l`) and switch to your specific tenant database (e.g. `tenant_hosp-4943401c`):
```sql
\c tenant_hosp-4943401c
```

### Step 3: Insert Mock Data
Run the following SQL script to create a patient, an active outpatient visit, a mock consultation, a pending laboratory request, and a corresponding queue entry.

*We use fixed, recognizable UUIDs so they are easy to copy-paste into curl commands.*

```sql
-- 1. Insert Patient
INSERT INTO patients (
    id, hospital_id, patient_number, full_name, date_of_birth, gender,
    email, address, phone_primary, is_active, created_at, updated_at
) VALUES (
    'e5000005-0005-4005-8005-000000000001', 'hosp-4943401c', 'PAT-999', 'John Doe', '1990-05-15', 'male',
    'johndoe@example.com', '123 Main St, City', '0712345678', true, NOW(), NOW()
) ON CONFLICT (id) DO NOTHING;

-- 2. Insert Visit
INSERT INTO visits (
    visit_id, patient_id, visit_number, visit_date, visit_type,
    payment_type, status, registered_by, created_at, updated_at
) VALUES (
    'e5000005-0005-4005-8005-000000000002', 'e5000005-0005-4005-8005-000000000001', 'VIS-999', CURRENT_DATE, 'outpatient',
    'cash', 'registered', 'admin-user-uuid', NOW(), NOW()
) ON CONFLICT (visit_id) DO NOTHING;

-- 3. Insert Consultation (needed for investigation request foreign key)
INSERT INTO consultations (
    id, visit_id, patient_id, clinical_impression, created_at, updated_at
) VALUES (
    'e5000005-0005-4005-8005-000000000003', 'e5000005-0005-4005-8005-000000000002', 'e5000005-0005-4005-8005-000000000001',
    'Suspected malaria', NOW(), NOW()
) ON CONFLICT (id) DO NOTHING;

-- 4. Insert Laboratory Investigation Request
INSERT INTO investigation_requests (
    id, consultation_id, visit_id, patient_id, request_type,
    test_name, clinical_history, status, urgency, created_at, created_by
) VALUES (
    'e5000005-0005-4005-8005-000000000004', 'e5000005-0005-4005-8005-000000000003', 'e5000005-0005-4005-8005-000000000002',
    'e5000005-0005-4005-8005-000000000001', 'laboratory', 'Malaria BS/MPS', 'Fever and chills for 3 days',
    'pending', 'stat', NOW(), 'Dr. Smith'
) ON CONFLICT (id) DO NOTHING;

-- 5. Insert Queue Item
INSERT INTO queues (
    queue_id, visit_id, patient_id, queue_type,
    queue_number, priority, status, created_at
) VALUES (
    'e5000005-0005-4005-8005-000000000005', 'e5000005-0005-4005-8005-000000000002', 'e5000005-0005-4005-8005-000000000001',
    'lab', 'L-01', 'stat', 'waiting', NOW()
) ON CONFLICT (queue_id) DO NOTHING;
```

---

### Step 4: Verify via API Endpoints
Now you can perform requests using the mock UUIDs. (Ensure you supply the correct `Authorization: Bearer <token>` header).

#### 1. Retrieve the Queue
```bash
curl -X GET http://localhost:8000/api/v1/laboratory/queue?status=waiting \
  -H "Authorization: Bearer <token>"
```
*Verify that `John Doe` with request UUID `e5000005-...04` and queue UUID `e5000005-...05` is returned.*

#### 2. Fetch Request Details
```bash
curl -X GET http://localhost:8000/api/v1/laboratory/requests/e5000005-0005-4005-8005-000000000004 \
  -H "Authorization: Bearer <token>"
```
*Verify that patient demographics and visit details are correctly returned.*

#### 3. Call the Patient
```bash
curl -X POST http://localhost:8000/api/v1/laboratory/queue/e5000005-0005-4005-8005-000000000005/call \
  -H "Authorization: Bearer <token>"
```

#### 4. Collect Specimen
```bash
curl -X POST http://localhost:8000/api/v1/laboratory/requests/e5000005-0005-4005-8005-000000000004/specimen \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"specimen_type": "Whole Blood", "collection_method": "Venipuncture", "notes": "Green top tube"}'
```
*This returns a `specimen_id` (e.g., `s1000000-...`). Use that ID to receive/reject.*

---

## Tenant Databases & Alembic Migrations

### How migrations run automatically

When a new hospital signs up, the tenant DB is created and migrated automatically during signup provisioning.

### Running Migrations Manually
If you add a new tenant migration and existing databases need upgrading:

From `HospitalSystem/infrastructure`:

```bash
docker compose exec master-service python -c "
import os, subprocess
from sqlalchemy import create_engine, text
engine = create_engine('postgresql://postgres:nasr@postgres-master:5432/hospital_master')
tenants = [r[0] for r in engine.connect().execute(text('SELECT tenant_id FROM tenants')).all()]
for t in tenants:
    url = f'postgresql://postgres:nasr@postgres-master:5432/tenant_{t}'
    print(f'Migrating tenant_{t}...')
    subprocess.run(
        ['alembic', '-c', 'migrations/tenant/alembic.ini', 'upgrade', 'head'],
        env={**dict(os.environ), 'TENANT_DB_URL': url}, check=True
    )
"
```

---

## Running Tests

From the `laboratory-service` root folder:
```bash
pytest
```
Unit and integration tests are backed by SQLite in-memory databases with pre-seeded mock scenarios.

---

## Troubleshooting

* **403 Forbidden on History Endpoint:** Ensure your JWT contains either `doctor`, `lab_technician`, or `super_admin` in `realm_access.roles`.
* **Missing Details on GET Request:** Ensure that matching rows exist in the read-only tables (`patients` and `visits`) for the request.
* **Locked Result:** Once you run the `/verify` endpoint on a result ID, `is_verified` becomes `true`. Any further `PATCH` operations will return `400 Bad Request`.
