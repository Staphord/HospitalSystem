# Hospital Flow — Testing Guide

## 0. Quick Start — Get Tokens

```bash
# Hospital admin token (for tenant operations)
HADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"hadmin1","password":"admin12345"}' | jq -r .access_token)

# Super admin token (for incidents, monitoring, tenant management)
SADMIN_TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"superadmin","password":"superadmin123"}' | jq -r .access_token)
```

## 1. Architecture & Authentication

### Gateway vs. Direct Access

```
┌─ User ──► API Gateway (port 8000) ──► [resolves tenant DB URL]
│                                          │
│            ┌─────────────────────────────┤
│            ▼                             ▼
│   reception-service (8010)        patient/visit service
│   [orchestrator]                  [direct access]
│            │
│            ├──► patient-service (8005)
│            └──► visit-service (8006)
│
└─ User ──► microservice directly (e.g. port 8005)
             ──► resolves DB URL from Master DB via JWT tenant_id
```

**Always prefer the API gateway** (`http://localhost:8000`) for testing. The gateway:
1. Authenticates your JWT token
2. Reads `tenant_id` from JWT claims
3. Looks up the tenant's database URL from the Master DB (encrypted DSN)
4. Injects `X-Tenant-DB: postgresql://user:pass@host:5432/tenant_xxx` header
5. Proxies the request to the target microservice

**reception-service** acts as an **orchestrator** (not a data service). It receives requests from the reception desk, delegates patient CRUD to **patient-service** and visit/queue management to **visit-service**, and returns combined responses. It adds orchestration value:
- `POST /api/v1/reception/register-and-visit` — register patient + create visit in one call
- JWT passthrough — downstream services authenticate independently
- Future: reception-specific audit logging, ID verification, workflow management

**Direct access** (`http://localhost:8005`, `http://localhost:8006`, `http://localhost:8010`) also works — each service resolves the DB URL from the Master DB using the `tenant_id` claim in your JWT.

### X-Tenant-DB Header

- **Type**: Internal header injected by the API gateway
- **Value**: A full PostgreSQL connection URL (e.g. `postgresql://postgres:nasr@postgres-master:5432/tenant_hosp-abc12345`)
- **When accessing directly**: You can optionally provide it, but it must be a **database URL**, not a tenant ID
- **From token fallback**: If omitted and the JWT has a `tenant_id` claim, the service resolves the DB URL automatically from the Master DB

### Required JWT Claims

| Claim | Source | Value |
|-------|--------|-------|
| `tenant_id` | Keycloak user attribute | e.g. `hosp-abc12345` (for hospital admins) |
| `hospital_id` | Keycloak user attribute | Same as tenant_id (legacy) |
| `realm_access.roles` | Keycloak realm roles | e.g. `["hospital_admin", "hospital_user"]` |

**Super admins** have no `tenant_id` claim and cannot operate on tenant data directly.

### Getting a Hospital Admin Token

```bash
# Login as hospital admin to get a token with tenant_id
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "hadmin1", "password": "admin12345"}'

# Response includes access_token with tenant_id in the JWT claims
```

---

## 2. Monitoring Endpoints (super_admin only)

All monitoring endpoints require a **super_admin Bearer token**. They are **GET-only** (no request body — if Swagger shows `Request body *`, ignore it; just click Execute).

### GET /api/v1/monitoring/telemetry — System health

**curl:**
```bash
# Set superadmin token first
SADMIN_TOKEN="eyJhbGciOi..."

curl -s http://localhost:8000/api/v1/monitoring/telemetry \
  -H "Authorization: Bearer $SADMIN_TOKEN" | jq
```

**Expected response:**
```json
{
  "timestamp": "2026-06-23T...",
  "service": "master-service",
  "system": { "platform": "...", "python_version": "3.11.1" },
  "cpu": { "percent": 12.3, "count": 8, "per_cpu": [...] },
  "memory": { "total": 17179869184, "available": 8589934592, "used": 8589934592, "percent": 50.0 },
  "disk": { "total": 500000000000, "used": 250000000000, "free": 250000000000, "percent": 50.0 },
  "db_connections": { "active": 5 },
  "db_size_bytes": 104857600
}
```

**Note:** CPU/memory/disk come from the Docker container, not the host. `db_size_bytes` is the master database size.

---

### GET /api/v1/monitoring/tenant-counts — Tenant status aggregation

No request body needed — just the Bearer token.

**curl:**
```bash
curl -s http://localhost:8000/api/v1/monitoring/tenant-counts \
  -H "Authorization: Bearer $SADMIN_TOKEN" | jq
```

```json
{
  "total": 10,
  "active": 7,
  "suspended": 2,
  "terminated": 0,
  "trial": 1
}
```

---

### GET /api/v1/superadmin/tenants/usage-telemetry

Returns per-tenant DB size and user count.

```json
[
  {
    "tenant_id": "hosp-abc12345",
    "name": "City Hospital",
    "db_size_bytes": 52428800,
    "user_count": 15
  }
]
```

---

### GET /api/v1/superadmin/tenants/{tenant_id}/stats

Returns detailed usage for one tenant.

**Path param:** `tenant_id` (e.g. `hosp-abc12345`)

```json
{
  "tenant_id": "hosp-abc12345",
  "name": "City Hospital",
  "plan": "standard",
  "plan_display_name": "Standard",
  "plan_max_users": 100,
  "plan_features": ["reception", "triage", "consultation"],
  "local_user_count": 12,
  "keycloak_user_count": 15,
  "patients_this_month": 340,
  "subscription": { "status": "active", "end": "2026-07-23T..." }
}
```

---

## 3. Incidents Endpoints (super_admin only)

Incidents track system problems. Available at `http://localhost:8002/docs` under **Incidents** tag.

### POST /api/v1/superadmin/incidents — Create

**curl:**
```bash
curl -X POST http://localhost:8000/api/v1/superadmin/incidents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
  "title": "Database query timeout",
  "description": "Analytics query took > 30s on hosp-abc12345",
  "severity": "severe",
  "source": "monitoring-automation",
  "tenant_id": "hosp-abc12345"
}'
```

```json
{
  "title": "Database query timeout",
  "description": "Analytics query took > 30s on hosp-abc12345",
  "severity": "severe",
  "source": "monitoring-automation",
  "tenant_id": "hosp-abc12345"
}
```

**Severity values:** `warning`, `severe`
**Status defaults to:** `open`

**Response (201):**
```json
{
  "incident_id": "uuid-here",
  "title": "Database query timeout",
  "description": "Analytics query took > 30s on hosp-abc12345",
  "severity": "severe",
  "status": "open",
  "source": "monitoring-automation",
  "tenant_id": "hosp-abc12345",
  "assigned_to": null,
  "resolved_at": null,
  "resolution_notes": null,
  "created_by": "your-superadmin-id",
  "created_at": "2026-06-23T...",
  "updated_at": "2026-06-23T..."
}
```

### GET /api/v1/superadmin/incidents — List all

Returns all incidents, newest first.

**curl:**
```bash
curl -s http://localhost:8000/api/v1/superadmin/incidents \
  -H "Authorization: Bearer $TOKEN" | jq
```

### PATCH /api/v1/superadmin/incidents/{incident_id} — Update

**Path param:** `incident_id` (UUID)

**curl:**
```bash
curl -X PATCH http://localhost:8000/api/v1/superadmin/incidents/<UUID> \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status": "resolved", "resolution_notes": "Fixed by restarting DB"}'
```

```json
{
  "status": "resolved",
  "resolution_notes": "Added index on analytics_events.created_at — query now 200ms"
}
```

**Status lifecycle:** `open` → `acknowledged` → `resolved` → `closed`

---

## 4. /api/v1/me — Profile Endpoint

Available at `http://localhost:8001/docs` (auth-service).

### Super Admin response example:
```json
{
  "sub": "6deb54e5-6401-4f1c-8772-56ed45f5c049",
  "preferred_username": "superadmin12",
  "email": "admin@hosp1.com",
  "roles": ["super_admin", "offline_access", "uma_authorization"],
  "role": "super_admin",
  "tenant_id": null,
  "is_super_admin": true,
  "scope": "full"
}
```

### Hospital Admin response example:
```json
{
  "sub": "abc123-...",
  "preferred_username": "hadmin1",
  "email": "admin@cityhospital.com",
  "roles": ["hospital_admin", "hospital_user"],
  "role": "hospital_admin",
  "tenant_id": "hosp-abc12345",
  "is_super_admin": false,
  "scope": "full"
}
```

**Role priority logic:** `super_admin` → `hospital_admin` → `doctor` → `clinician` → `nurse` → `patient` → `hospital_user`. System roles (`default-roles-*`, `offline_access`, `uma_authorization`) are filtered out.

---

## 5. Running the stack

```bash
docker compose -f infrastructure/docker-compose.yml up --build -d
```

After changes, rebuild specific services:
```bash
docker compose -f infrastructure/docker-compose.yml build patient-service
docker compose -f infrastructure/docker-compose.yml up -d patient-service
```

For unit tests:
```bash
# Patient service tests (14 tests)
cd services/patient-service
$env:PYTHONPATH="."; python -m pytest tests/ -v

# Visit service tests (11 tests)
cd services/visit-service
$env:PYTHONPATH="."; python -m pytest tests/ -v

# Master-service subscription tests
cd services/master-service
$env:PYTHONPATH="."; pytest tests/unit/test_subscription_service.py -v
```

---

## 6. Patient Service (port 8005)

### How to Test on Swagger UI

**IMPORTANT**: You must use a **hospital admin** token (not super admin). Hospital admin tokens have a `tenant_id` claim.

#### Option A: Through the Reception API (Recommended for Reception Desk)

```
URL: http://localhost:8000/api/v1/reception/patients/register
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

Reception-service orchestrates the call: authenticates the JWT, delegates to patient-service, returns the result.

#### Option B: Through API Gateway (Recommended for Direct Testing)

```
URL: http://localhost:8000/api/v1/patients/register
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

The API gateway automatically:
- Extracts `tenant_id` from your JWT
- Resolves the tenant database URL from Master DB
- Injects `X-Tenant-DB` header with the resolved URL
- Proxies to patient-service

#### Option C: Direct Access (for dev/testing)

```
URL: http://localhost:8005/api/v1/patients/register
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

The patient-service will:
- Extract `tenant_id` from your JWT
- Resolve the tenant database URL from Master DB automatically
- (No need to pass X-Tenant-DB header)

### POST /api/v1/patients/register — Register a new patient

**Required fields:** `full_name`, `date_of_birth`, `gender`

**Request body:**
```json
{
  "full_name": "John Doe",
  "date_of_birth": "1990-01-15",
  "gender": "male",
  "phone": "+1234567890",
  "email": "john@example.com",
  "address": "123 Main St",
  "emergency_contact_name": "Jane Doe",
  "emergency_contact_phone": "+1987654321",
  "national_id": "NAT-001",
  "medical_history": "Asthma",
  "allergies": "Penicillin",
  "blood_group": "O+"
}
```

**Automatic:** `patient_number` is auto-generated in format `PT-YYYYMMDD-XXXX` (daily-reset zero-padded sequence).

**Response (201):**
```json
{
  "id": "uuid",
  "patient_number": "PT-20260623-0001",
  "full_name": "John Doe",
  "date_of_birth": "1990-01-15",
  "gender": "male",
  "phone": "+1234567890",
  "email": "john@example.com",
  "address": "123 Main St",
  "emergency_contact_name": "Jane Doe",
  "emergency_contact_phone": "+1987654321",
  "national_id": "NAT-001",
  "medical_history": "Asthma",
  "allergies": "Penicillin",
  "blood_group": "O+",
  "is_active": true,
  "created_at": "2026-06-23T12:00:00Z",
  "updated_at": "2026-06-23T12:00:00Z",
  "created_by": "hadmin1"
}
```

**Validation rules:**
- `full_name` cannot be empty
- `gender` must be one of `male`, `female`, `other`
- `national_id` must be unique per tenant (duplicate → 409 Conflict)
- `date_of_birth` must be a valid date in `YYYY-MM-DD` format

### GET /api/v1/patients/search — Search patients

**Query parameters** (at least one of `query`, `national_id`, or `patient_number` required):
- `query` — partial case-insensitive search on name, phone, email, patient number, national ID
- `national_id` — exact match
- `patient_number` — exact match
- `limit` (default 20, max 100)
- `offset` (default 0)

**Example:**
```
GET http://localhost:8000/api/v1/patients/search?query=John
GET http://localhost:8000/api/v1/patients/search?national_id=NAT-001
GET http://localhost:8000/api/v1/patients/search?patient_number=PT-20260623-0001
```

### GET /api/v1/patients/{patient_id} — Get patient by UUID

Returns a single patient record or 404 if not found.

### DELETE /api/v1/patients/{patient_id} — Delete patient

Returns 204 on success, 404 if not found.

### Unit tests

```bash
cd services/patient-service
python -m pytest tests/ -v
```

All 14 tests cover:
- Patient number generation (format, increment, per-hospital isolation, sequence table)
- Patient registration (full and minimal fields)
- Search by name, national_id, patient_number
- Cross-hospital isolation
- Patient retrieval by ID
- Patient deletion (found + not found)
- Patient count aggregation

---

## 7. Visit Service (port 8006)

### How to Test on Swagger UI

**Prerequisites:**
1. Login as a hospital admin to get a JWT with `tenant_id` claim
2. Register at least one patient via the Patient Service to get a `patient_id` (UUID)
3. (Optional) Create insurance policies directly in the tenant DB for insurance testing

#### Option A: Through Reception API (Recommended for Reception Desk)

```
URL: http://localhost:8000/api/v1/reception/visits
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

Or use the combined `POST /api/v1/reception/register-and-visit` to register a patient and create a visit in one call.

#### Option B: Through API Gateway

```
URL: http://localhost:8000/api/v1/visits
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

#### Option C: Direct Access

```
URL: http://localhost:8006/api/v1/visits
Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

### POST /api/v1/visits — Create a visit

**Cash payment example:**
```json
{
  "patient_id": "uuid-of-existing-patient",
  "visit_type": "outpatient",
  "payment_type": "cash"
}
```

**Insurance payment example (verified policy):**
```json
{
  "patient_id": "uuid-of-existing-patient",
  "visit_type": "emergency",
  "payment_type": "insurance",
  "insurer_name": "TestInsurer",
  "policy_number": "POL-001"
}
```

**visit_type** values: `outpatient`, `inpatient`, `emergency`
**payment_type** values: `cash`, `insurance`

**Response (201):**
```json
{
  "visit": {
    "visit_id": "uuid",
    "patient_id": "uuid",
    "visit_number": "VIS-20260623-0001",
    "visit_date": "2026-06-23",
    "visit_type": "outpatient",
    "payment_type": "cash",
    "insurance_id": null,
    "verification_flag": null,
    "queue_number": "T-001",
    "status": "registered",
    "registered_by": "uuid",
    "created_at": "2026-06-23T12:00:00Z",
    "updated_at": "2026-06-23T12:00:00Z"
  },
  "queue_number": "T-001",
  "verification_flag": null
}
```

**Insurance verification rules:**
| Scenario | insurance_id | verification_flag | HTTP Status |
|----------|-------------|-------------------|-------------|
| Cash payment | null | null | 201 |
| Verified + active + coverage > 0 + not expired | linked | null | 201 |
| Pending verification | linked | `manual_review_required` | 201 |
| Rejected policy | linked | `Policy verification was rejected` | 201 |
| Expired policy | linked | `Policy has expired` | 201 |
| No matching policy | — | — | **400** error |

**Auto-generated:**
- `visit_number` — format `VIS-YYYYMMDD-XXXX` (daily sequence)
- `queue_number` — format `T-XXX` (triage, daily sequence)
- `status` — defaults to `registered`
- Queue entry created with `priority: non_urgent`, `status: waiting`

### GET /api/v1/visits/queues/triage/today — Today's triage queue

Returns all triage queue entries created today, ordered oldest first.

### Database Tables Created in Tenant DB

When a visit is created, the service auto-creates these tables in the tenant database:
- `patient_insurance` — insurance policies for patients
- `visits` — visit/encounter records
- `queues` — queue entries (triage, doctor, lab, etc.)
- `visit_number_sequences` — daily visit number counter
- `queue_number_sequences` — daily queue number counter per type

### Setting Up Insurance Policies for Testing

To test insurance flows, you need to insert insurance records directly into the tenant database:

```sql
-- Verified insurance (will be auto-verified)
INSERT INTO patient_insurance (insurance_id, patient_id, insurer_name, policy_number, coverage_limit, expiry_date, verification_status, is_active)
VALUES (gen_random_uuid(), '<patient-uuid>', 'TestInsurer', 'POL-001', 100000.00, '2027-12-31', 'verified', true);

-- Pending insurance (will be flagged for manual review)
INSERT INTO patient_insurance (insurance_id, patient_id, insurer_name, policy_number, coverage_limit, expiry_date, verification_status, is_active)
VALUES (gen_random_uuid(), '<patient-uuid>', 'PendingInsurer', 'POL-002', 50000.00, '2027-12-31', 'pending', true);

-- Expired insurance (will be flagged as expired)
INSERT INTO patient_insurance (insurance_id, patient_id, insurer_name, policy_number, coverage_limit, expiry_date, verification_status, is_active)
VALUES (gen_random_uuid(), '<patient-uuid>', 'ExpiredInsurer', 'POL-003', 50000.00, '2024-01-01', 'verified', true);
```

### Unit tests

```bash
cd services/visit-service
python -m pytest tests/ -v
```

All 11 tests cover:
- Visit creation with cash (visit_number, queue entry, queue_number format)
- Visit with verified insurance (insurance_id linked, no verification flag)
- Visit with pending insurance (insurance linked, flag = manual_review_required)
- Visit with expired insurance (insurance linked, flag = Policy has expired)
- Visit with no matching insurance policy (400 error)
- Visit with insurance but missing insurer/policy fields (400 error)
- Visit number generation (format, increment)
- Queue number generation (format, increment, per-type isolation)

---

## 8. Reception Service (port 8010) — Orchestrator

The reception-service does **not** store data itself. It orchestrates calls between patient-service and visit-service so the reception desk has a single API to work with.

### Architecture

```
Reception Desk
  │
  ├── POST /reception/patients/register
  │     └──► patient-service /patients/register (JWT passthrough)
  │
  ├── POST /reception/visits
  │     └──► visit-service /visits (JWT passthrough)
  │
  ├── POST /reception/register-and-visit      ← Combined flow
  │     ├──► patient-service /patients/register
  │     └──► visit-service /visits (auto-injects patient_id)
  │
  └── GET /reception/visits/queues/triage/today
        └──► visit-service /visits/queues/triage/today
```

The JWT token is forwarded as-is to downstream services. Each service independently authenticates and resolves the tenant database from the JWT's `tenant_id` claim.

### How to Test on Swagger UI

```
URL: http://localhost:8000/api/v1/reception/patients/register
     (through gateway)
OR
URL: http://localhost:8010/api/v1/reception/patients/register
     (direct)

Headers:
  Authorization: Bearer <hospital_admin_token>
  Content-Type: application/json
```

### POST /api/v1/reception/patients/register — Register a patient

Same request/response as patient-service (see [Patient Service §5](#5-patient-service-port-8005)).

Reception-service delegates to `POST /api/v1/patients/register` on patient-service and returns the result.

### POST /api/v1/reception/visits — Create a visit

Same request/response as visit-service (see [Visit Service §6](#6-visit-service-port-8006)).

**Cash visit example:**
```json
{
  "patient_id": "uuid-from-registration",
  "visit_type": "outpatient",
  "payment_type": "cash"
}
```

Reception-service delegates to `POST /api/v1/visits` on visit-service and returns the result including `queue_number`.

### POST /api/v1/reception/register-and-visit — Combined: register + visit

This is the **flagship orchestration endpoint**. Register a patient and immediately create their first visit in one request.

**Request:**
```json
{
  "patient": {
    "full_name": "Nasri Ahmed",
    "date_of_birth": "1990-05-15",
    "gender": "male",
    "phone": "+255712345678",
    "email": "nasri@example.com",
    "national_id": "NAT-2026-001"
  },
  "visit": {
    "visit_type": "outpatient",
    "payment_type": "cash"
  }
}
```

**How it works:**
1. Reception-service calls `POST /patients/register` on patient-service → gets back `patient_id`
2. Reception-service injects `patient_id` into the visit data
3. Reception-service calls `POST /visits` on visit-service → creates visit + triage queue entry
4. Returns both results

**Response (201):**
```json
{
  "patient": {
    "id": "uuid",
    "patient_number": "PT-20260623-0001",
    "full_name": "Nasri Ahmed",
    ...
  },
  "visit": {
    "visit": {
      "visit_id": "uuid",
      "visit_number": "VIS-20260623-0001",
      "queue_number": "T-001",
      "status": "registered",
      ...
    },
    "queue_number": "T-001",
    "verification_flag": null
  }
}
```

### GET /api/v1/reception/visits/queues/triage/today — Today's triage queue

Returns all triage queue entries created today, ordered oldest first.

---

## 9. Auto-Migration (Schema Sync)

Tenant-facing services (patient-service, visit-service, etc.) automatically sync the database schema on every connection via `sync_tenant_schema()`.

**What it does:**
- Creates missing tables
- Adds missing columns (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`)
- Creates missing indexes

**How it works:**
- Runs inside `get_tenant_session()` every time a tenant DB connection is opened
- Only executes DDL when the schema is out of sync — no overhead on matched schemas
- Uses SQLAlchemy metadata inspection — no Alembic dependency

**To add to a new service:**
```python
# in app/db/sync.py
from app.db.sync import sync_tenant_schema

def get_tenant_session(db_url: str) -> Session:
    ...
    sync_tenant_schema(engine, Base.metadata)
```

---

## 10. Troubleshooting

### "No tenant association found in token" (403)
- You are using a **super admin** token which has no `tenant_id` claim
- Use a **hospital admin** token instead (e.g. `hadmin1` / `admin12345`)

### "Could not resolve tenant database URL" (400)
- Your JWT has a `tenant_id` but the Master DB has no matching `tenants` record
- Or the tenant's `db_dsn_encrypted` is missing/corrupt
- Check that the tenant was properly provisioned

### Internal Server Error (500)
- Check the service logs for the actual error
- Common causes: DB connection failure, missing table, encryption key mismatch
- Verify `TENANT_DB_ENCRYPTION_KEY` matches the key used during provisioning
- Verify `DATABASE_URL` points to the correct Master DB

### Tables not found in tenant database
- The services auto-create missing tables via `sync_tenant_schema()` on every connection
- Missing columns are also auto-added via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
- If the DB user lacks CREATE/ALTER privileges, grant them or run migrations manually

### Column not found (e.g. "column X does not exist")
- Restart the service — `sync_tenant_schema()` runs on startup and will add missing columns
- The schema sync runs on every tenant DB session, not just the first connection

### "Multiple head revisions" in Alembic
- Two migration files share the same parent revision
- Either chain them (one depends on the other) or create a merge migration
- Current fix: `0007_add_incidents.py` now depends on `0007_add_superadmin_mfa_fields.py`
