# Hospital Flow — Multi-Tenant Hospital Management System

Multi-tenant hospital patient flow system decomposed into **14 microservices** with FastAPI backends, PostgreSQL per-tenant database isolation, Keycloak OIDC authentication, and RabbitMQ event-driven communication.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Service Map](#service-map)
3. [Repository Structure](#repository-structure)
4. [Local Development Setup](#local-development-setup)
5. [Database Migrations](#database-migrations)
6. [Provisioning a New Hospital](#provisioning-a-new-hospital)
7. [How Multi-Tenancy Works](#how-multi-tenancy-works)
8. [Event Bus](#event-bus)
9. [Testing RabbitMQ](#testing-rabbitmq)
10. [API Authentication](#api-authentication)
11. [Role Reference](#role-reference)
12. [Environment Variables](#environment-variables)
13. [Test Users](#test-users)
14. [License](#license)

---

## Project Status

### Services Implementation

| Service | Port | Status | Notes |
|---------|------|--------|-------|
| `api-gateway` | 8000 | ✅ Complete | JWT verify, tenant resolve, proxy, rate-limit |
| `auth-service` | 8001 | ✅ Complete | Login, signup, refresh, MFA, impersonate, password reset |
| `master-service` | 8002 | ✅ Complete | Tenants, subscriptions, SaaS billing, super admin |
| `patient-service` | — | ✅ Complete | Patient CRUD, search, number generation |
| `visit-service` | — | ✅ Complete | Visit CRUD, queues, status transitions, insurance check |
| `admin-service` | 8018 | ✅ Complete | Hospital user CRUD, Keycloak sync, roles |
| `triage-service` | 8011 | ✅ Complete | Triage assessments, vitals, category suggestion |
| `consultation-service` | 8012 | ✅ Complete | Consultations, diagnoses, investigation requests, encounter view |
| `radiology-service` | 8014 | ✅ Complete | Radiology reports CRUD, modality/status enums |
| `pharmacy-service` | 8015 | 🟡 Partial | Inventory (real DB-backed), queue/prescriptions/dispensing (Phase 1 stubs — returns mock data) |
| `reception-service` | 8010 | 🟡 Partial | Orchestration layer — delegates to patient-service and visit-service. No direct DB models. |
| `laboratory-service` | 8013 | 🔴 Empty | Placeholder only — no endpoints, no models, no business logic |
| `billing-service` | 8016 | 🔴 Empty | Placeholder only — no endpoints, no models, no business logic |
| `ward-service` | 8017 | 🔴 Empty | Placeholder only — no endpoints, no models, no business logic |
| `notification-service` | 8019 | 🔴 Empty | Placeholder only — no endpoints, no models, no business logic |
| `report-service` | 8020 | 🔴 Empty | Placeholder only — no endpoints, no models, no business logic |

### Database Schema Coverage

The target schema defines **32 tenant DB tables** and **8 master DB tables**. Current tenant migration coverage:

| Module | Tables in Schema | Migration Created | Endpoint Logic |
|--------|:-:|:-:|:-:|
| Reception — `patients` | ✅ | ✅ | ✅ |
| Reception — `patient_insurance` | ✅ | ✅ | ✅ |
| Reception — `visits` | ✅ | ✅ | ✅ |
| Reception — `queues` | ✅ | ✅ | ✅ |
| Reception — `appointments` | — | ✅ (extra) | ❌ |
| Triage — `triage_assessments` | ✅ | ✅ | ✅ |
| Consultation — `consultations` | ✅ | ✅ | ✅ |
| Consultation — `diagnoses` | ✅ | ❌ | ✅ (in code) |
| Consultation — `investigation_requests` | ✅ | ❌ | ✅ (in code) |
| Consultation — `prescriptions` | ✅ | ❌ | ❌ |
| Lab — `lab_results` | ✅ | ❌ | ❌ |
| Lab — `specimens` | ✅ | ❌ | ❌ |
| Radiology — `radiology_reports` | ✅ | ✅ | ✅ |
| Pharmacy — `drug_inventory` | ✅ | ✅ | ✅ |
| Pharmacy — `drug_inventory_transactions` | ✅ | ✅ | ✅ |
| Pharmacy — `dispensing_records` | ✅ | ❌ | ❌ |
| Billing — `bills` | ✅ | ❌ | ❌ |
| Billing — `bill_items` | ✅ | ❌ | ❌ |
| Billing — `payments` | ✅ | ❌ | ❌ |
| Billing — `insurance_claims` | ✅ | ❌ | ❌ |
| Ward — `beds` | ✅ | ❌ | ❌ |
| Ward — `admissions` | ✅ | ❌ | ❌ |
| Ward — `inpatient_orders` | ✅ | ❌ | ❌ |
| Ward — `nursing_notes` | ✅ | ❌ | ❌ |
| Admin — `users` | ✅ | ✅ | ✅ |
| Admin — `departments` | ✅ | ❌ | ❌ |
| Admin — `fee_schedules` | ✅ | ❌ | ❌ |
| Admin — `password_reset_tokens` | ✅ | ❌ | ❌ |
| Admin — `refresh_tokens` | ✅ | ❌ | ❌ |
| Notifications — `notifications` | ✅ | ❌ | ❌ |
| Admin — `audit_logs` | ✅ | ❌ | ❌ |

**Key gap**: Tables like `diagnoses`, `investigation_requests`, `prescriptions`, `lab_results`, `specimens`, `bills`, `payments`, `beds`, `admissions`, `inpatient_orders`, `nursing_notes`, `departments`, `fee_schedules`, `password_reset_tokens`, `refresh_tokens`, `notifications`, and `audit_logs` exist in the service SQLAlchemy models or are referenced in code but **do not have tenant migration scripts** yet.

### Radiology `request_id` Note

The `radiology_reports.request_id` column is currently **nullable** (`Optional[UUID] = None`) because the `investigation_requests` table has not been created yet. Once that table is migrated, `request_id` should be made a **required FK** (`nullable=False`) referencing `investigation_requests.request_id`. See `services/radiology-service/app/models/radiology.py:13` and `services/radiology-service/app/api/v1/schemas.py:9`.

---

## Architecture Overview

```
┌──────────────┐     ┌──────────────┐     ┌──────────────────┐
│   Client     │     │ API Gateway  │     │    Keycloak      │
│  (Web/Mobile)│────▶│  (port 8000) │────▶│  (port 8080)     │
└──────────────┘     └──────────────┘     │  OIDC Provider   │
                     │  JWT verify  │     │  RS256 JWTs      │
                     │  Tenant resolve    └──────────────────┘
                     │  Rate limit        │
                     └──────────────┘     │  ┌──────────────────┐
                     │  Proxy to services   │  │  PostgreSQL      │
                     │                      │  │  Master DB         │
                     └──────────────┘     │  │  (Tenants, Users)  │
                     │  RabbitMQ      │     └──────────────────┘
                     │  (Events)      │     │
                     └──────────────┘     │  ┌──────────────────┐
                     │  Redis         │     │  Redis           │
                     │  (Cache/Rate)  │     │  Rate Limiting,    │
                     └──────────────┘     │  Suspension        │
                                            └──────────────────┘
```

### Key Components

| Component | Role |
|-----------|------|
| **API Gateway** | JWT verification, tenant resolution, request routing, rate limiting |
| **Auth Service** | Login, token refresh, password reset, MFA (TOTP) |
| **Master Service** | Super admin portal — tenant management, subscriptions, invoicing |
| **14 Domain Services** | Reception, Triage, Consultation, Lab, Radiology, Pharmacy, Billing, Ward, Admin, Notification, Reporting |
| **Keycloak** | OIDC identity provider — issues RS256 JWTs, manages users & roles |
| **PostgreSQL (Master DB)** | Global data — tenants, user mappings, refresh tokens, audit logs |
| **PostgreSQL (Per-Tenant DBs)** | Isolated databases for each hospital's clinical data |
| **Redis** | Caching — rate limiting, tenant suspension blocklist, JWKS cache |
| **RabbitMQ** | Async event bus between services |

---

## Service Map

| Service                | Port | Status | Responsibility                                                      |
| ---------------------- | ---- | ------ | ------------------------------------------------------------------- |
| `api-gateway`          | 8000 | ✅     | JWT verification, tenant resolution, request routing, rate limiting |
| `auth-service`         | 8001 | ✅     | Login, token refresh, password reset, MFA (TOTP)                    |
| `master-service`       | 8002 | ✅     | Super admin portal — tenant management, subscriptions, invoicing    |
| `reception-service`    | 8010 | 🟡     | Patient registration, visit creation, queue assignment              |
| `triage-service`       | 8011 | ✅     | Vital signs, triage category, queue priority                        |
| `consultation-service` | 8012 | ✅     | Clinical notes, diagnoses, investigation requests, prescriptions    |
| `laboratory-service`   | 8013 | 🔴     | Specimen tracking, result entry, critical value alerts              |
| `radiology-service`    | 8014 | ✅     | Imaging scheduling, reports, DICOM references                       |
| `pharmacy-service`     | 8015 | 🟡     | Dispensing, drug interaction checks, inventory management           |
| `billing-service`      | 8016 | 🔴     | Bills, line items, payments, insurance claims                       |
| `ward-service`         | 8017 | 🔴     | Bed management, admissions, inpatient orders, nursing notes         |
| `admin-service`        | 8018 | ✅     | Staff accounts, departments, fee schedules, audit logs              |
| `notification-service` | 8019 | 🔴     | In-system notifications (critical results, low stock, queue calls)  |
| `report-service`       | 8020 | 🔴     | Analytics — census, revenue, wait times, bed occupancy              |

---

## Repository Structure

```
hospital-flow/
├── services/                     # 14 active microservices
│   ├── api-gateway/              # Port 8000
│   ├── auth-service/             # Port 8001
│   ├── master-service/           # Port 8002
│   ├── reception-service/        # Port 8010
│   ├── triage-service/           # Port 8011
│   ├── consultation-service/     # Port 8012
│   ├── laboratory-service/       # Port 8013
│   ├── radiology-service/        # Port 8014
│   ├── pharmacy-service/         # Port 8015
│   ├── billing-service/          # Port 8016
│   ├── ward-service/             # Port 8017
│   ├── admin-service/            # Port 8018
│   ├── patient-service/          # Internal — patient CRUD (called by reception)
│   ├── visit-service/            # Internal — visit & queue CRUD (called by reception/triage/consultation)
│   ├── notification-service/     # Port 8019
│   └── report-service/           # Port 8020
├── infrastructure/
│   ├── docker-compose.yml        # Full local dev stack
│   ├── docker-compose.test.yml
│   ├── k8s/
│   │   └── {service-name}/
│   │       ├── deployment.yaml
│   │       ├── service.yaml
│   │       └── hpa.yaml
│   └── nginx/
│       └── gateway.conf
├── migrations/
│   ├── master/
│   │   ├── alembic.ini
│   │   ├── env.py
│   │   └── versions/
│   │       ├── 0001_initial_master_schema.py
│   │       ├── 0002_add_subscription_lifecycle.py
│   │       ├── 0003_add_saas_schema.py
│   │       ├── 0004_announcement_creator_null.py
│   │       ├── 0005_add_termination_columns.py
│   │       └── 0006_add_keycloak_realm.py
│   └── tenant/
│       ├── alembic.ini
│       ├── env.py
│       └── versions/
│           └── 0001_initial_tenant_schema.py
├── shared/
│   └── schemas/
│       ├── common.py             # Shared enums, base response models
│       └── events.py             # RabbitMQ event payloads
├── scripts/
│   ├── provision_tenant.py         # CLI to provision a new hospital
│   ├── migrate_tenant.py         # Run tenant migrations for one hospital
│   ├── migrate_all_tenants.py      # Run tenant migrations across all hospitals
│   ├── run_all_tests.sh           # Run pytest across all 14 services
│   └── seed_dev.py                # Seed local dev data
 ├── docs/
│   ├── architecture_guide.docx
│   ├── database_schema.pdf
│   └── srs.docx
├── streamlit_app/                  # Streamlit frontend
├── .env.example
├── .gitignore
└── README.md
```

---

## Local Development Setup

### 1. Clone and configure environment

```bash
git clone https://github.com/your-org/hospital-flow.git
cd hospital-flow
cp .env.example .env
# Edit .env — fill in SECRET_KEY and any overrides
```

### 2. Start the full infrastructure stack

```bash
docker-compose up -d
```

This starts:
- PostgreSQL (Master DB on port 5432)
- Redis (port 6379)
- RabbitMQ (AMQP on 5672, management UI on 15672)
- All 14 services on their respective ports

### 3. Run Master DB migrations

```bash
cd migrations/master
alembic upgrade head
cd ../..
```

### 4. Seed development data

```bash
python scripts/seed_dev.py
# Creates 2 test hospitals, a super admin, and staff accounts for each
```

### 5. Verify

```bash
curl http://localhost:8000/health
# Should return: {"status": "ok", "services": {...}}
```

---

## Database Migrations

The system has two migration targets managed separately.

**Master DB** (tenant registry, subscriptions, super admin):

```bash
cd migrations/master
alembic upgrade head
alembic downgrade -1
alembic revision --autogenerate -m "your change description"
```

> **Troubleshooting**: If the database was created outside Alembic (e.g., via SQLAlchemy `create_all()`), the `alembic_version` table may be stamped at an old revision while columns from later migrations already exist. To fix, stamp to the correct revision and upgrade:
> ```bash
> alembic stamp 0004_announcement_creator_null
> alembic upgrade head
> ```

**Tenant DB** (all clinical tables — applied per hospital):

```bash
# Migrate a single tenant
python scripts/migrate_tenant.py --tenant-id <UUID>

# Migrate all tenants (use during maintenance window)
python scripts/migrate_all_tenants.py

# Check migration status across all tenants
python scripts/migrate_all_tenants.py --dry-run
```

---

## Provisioning a New Hospital

### Automatic provisioning (Signup flow)

When a user signs up via `POST /api/v1/auth/signup`, the system automatically provisions a new isolated PostgreSQL database for the hospital tenant:

```
auth-service (signup endpoint)
    ↓
1. Creates tenant record in Master DB (status = "pending")
2. Publishes tenant.created event to RabbitMQ
    ↓
master-service (event subscriber)
    ↓
3. Creates new PostgreSQL database: tenant_{tenant_id}
4. Runs tenant migrations (alembic upgrade head)
5. Encrypts DSN and updates tenant record in Master DB
6. Publishes tenant.provisioned event
```

### Environment variables for provisioning

| Variable | Description | Example |
|----------|-------------|---------|
| `DB_ADMIN_URL` | PostgreSQL connection with CREATEDB privilege | `postgresql://postgres:nasr@localhost:5432/postgres` |
| `TENANT_DB_TEMPLATE` | DSN template for new tenant databases | `postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}` |

### Keycloak Realm Architecture

- **Per-tenant realms**: Each hospital gets its own Keycloak realm (`hosp-XXXXXXXX`) on signup, with `hospital_user` and `hospital_admin` roles.
- **Realm fallback**: If realm creation fails (e.g., Keycloak unreachable), the tenant falls back to the shared `hospital-realm` realm.
- **Realm verification**: After creating a realm, the system verifies it exists via `GET /admin/realms/{realm}` before setting `keycloak_realm` on the tenant record.
- **Retroactive realm fix**: Super admins can create/fix a realm for an existing tenant via `POST /api/v1/superadmin/tenants/{id}/ensure-realm`.

### Realm Auto-Detection on Login

When a client sends `POST /api/v1/auth/login` without a `realm` field, the auth-service performs a **cross-realm search**:

1. **Fast path**: Check the default realm (`hospital-realm`) and the `master` realm.
2. **Full scan**: List all Keycloak realms via the admin API and search each one for the username.
3. **Fallback**: If not found anywhere, authenticate against the default realm.

This allows Streamlit and other clients to accept just a username + password without the user needing to know their realm. The resolved `tenant_id` is extracted from the JWT and returned in the response body.

### Super Admin Role CRUD Across Realms

Super admins can manage roles in **any** Keycloak realm via:

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/v1/superadmin/realms` | List all Keycloak realm names |
| GET | `/api/v1/superadmin/realms/{realm}/roles` | List roles in a realm |
| POST | `/api/v1/superadmin/realms/{realm}/roles` | Create a role in a realm |
| PUT | `/api/v1/superadmin/realms/{realm}/roles/{name}` | Rename a role |
| DELETE | `/api/v1/superadmin/realms/{realm}/roles/{name}` | Delete a role |
| GET | `/api/v1/superadmin/users` | List all users across all realms |

### Login Response

Both `POST /api/v1/auth/login` and `POST /api/v1/auth/refresh` now return `tenant_id` in the response body (extracted from the JWT claims), so clients don't need to decode the JWT themselves:

### Manual provisioning (development/testing)

```bash
python scripts/provision_tenant.py \
  --hospital-name "General Hospital" \
  --country Tanzania \
  --city "Dar es Salaam" \
  --admin-email admin@generalhospital.tz
```

This creates:
1. A new `tenant_{tenant_id}` database in PostgreSQL
2. A tenant record in the Master DB with encrypted DSN
3. All tenant tables via alembic migrations

---

## How Multi-Tenancy Works

Every request is tenant-scoped from the moment it arrives:

```
Client request (with JWT Bearer token)
    ↓
API Gateway — extracts tenant_id from JWT claims
    ↓
Gateway queries Master DB: SELECT db_connection_string WHERE tenant_id = ?
    (result cached in Redis — key: tenant:{id}:db_url — TTL 300s)
    ↓
Gateway attaches X-Tenant-DB header to proxied request
    ↓
Target service receives X-Tenant-DB → creates SQLAlchemy engine
    (engine cached in process memory per tenant_id — connection pool min=2, max=10)
    ↓
All queries in this request run against this hospital's database only
    ↓
Audit middleware writes action to audit_logs in the same tenant DB
    ↓
Response returned to client
```

**There is no application-level WHERE tenant_id = ? filter.** Isolation is enforced at the database connection level — a connection to Hospital A's database cannot see Hospital B's tables.

---

## Event Bus

Services communicate asynchronously via **RabbitMQ** using a single durable topic exchange named `hospital_events`. Routing keys follow the pattern `{domain}.{event}`.

### How it works in code

Each service contains a self-contained `app/messaging/` module (copied from `shared/messaging/` at the project root):

| File | Purpose |
|------|---------|
| `app/messaging/connection.py` | Robust `aio-pika` connection + `hospital_events` topic exchange declaration |
| `app/messaging/publisher.py` | `publish_event(routing_key, payload)` — serialises a dict to JSON and publishes |
| `app/messaging/subscriber.py` | `start_consumer(service_name, routing_keys, handler)` — durable queue + async consumer |

Every service that produces events has an `app/events/publisher.py` module that calls `publish_event(...)`.
Every service that consumes events has an `app/events/subscriber.py` module that exposes `start_subscriber()`, which is started as a background `asyncio.Task` inside the FastAPI lifespan.

Example from `reception-service`:

```python
# app/events/publisher.py
from app.messaging.publisher import publish_event

async def publish_visit_created(visit_id: str, patient_id: str, tenant_id: str) -> None:
    await publish_event("visit.created", {
        "visit_id": visit_id,
        "patient_id": patient_id,
        "tenant_id": tenant_id,
    })
```

Example from `triage-service`:

```python
# app/events/subscriber.py
from app.messaging.subscriber import start_consumer

async def handle_visit_created(visit_id: str, tenant_id: str) -> None:
    # Business logic here
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "visit.created":
        await handle_visit_created(payload["visit_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="triage-service",
        routing_keys=["visit.created"],
        handler=_dispatch,
    )
```

### Event reference

| Event                     | Publisher    | Subscribers                |
| ------------------------- | ------------ | -------------------------- |
| `visit.created`           | reception    | billing, triage            |
| `triage.completed`        | triage       | consultation               |
| `investigation.requested` | consultation | laboratory, radiology      |
| `prescription.issued`     | consultation | pharmacy, billing          |
| `lab.critical_value`      | laboratory   | notification               |
| `lab.result_ready`        | laboratory   | consultation               |
| `radiology.report_ready`  | radiology    | consultation, notification |
| `drug.dispensed`          | pharmacy     | billing                    |
| `stock.low`               | pharmacy     | notification               |
| `patient.admitted`        | ward         | billing, notification      |
| `patient.discharged`      | ward         | billing                    |
| `payment.received`        | billing      | pharmacy (clearance)       |
| `tenant.suspended`        | master       | auth (revoke tokens)       |

All event consumers are **idempotent** — processing the same event twice produces the same result as processing it once.

### Manual smoke test

Start RabbitMQ locally (or via Docker Compose):

```bash
docker-compose up -d rabbitmq
```

Run the built-in test script:

```bash
python scripts/test_rabbitmq.py
```

It will:
1. Publish a `test.event` message to `hospital_events`
2. Consume it and print the payload
3. Assert round-trip delivery

---

## Testing RabbitMQ

### Unit tests

Each service has placeholder unit tests for its publisher / subscriber functions in:
- `tests/unit/test_{domain}.py`

Example unit test pattern (uses `unittest.mock.AsyncMock`):

```python
import pytest
from unittest.mock import AsyncMock, patch

from app.events.publisher import publish_visit_created

@pytest.mark.asyncio
async def test_publish_visit_created_calls_publish_event():
    with patch("app.events.publisher.publish_event", new_callable=AsyncMock) as mock_pub:
        await publish_visit_created("v-001", "p-001", "hosp-001")
        mock_pub.assert_awaited_once_with(
            "visit.created",
            {"visit_id": "v-001", "patient_id": "p-001", "tenant_id": "hosp-001"},
        )
```

### Integration tests

Run the integration test suite against a real RabbitMQ container:

```bash
# Start RabbitMQ
docker run -d --name rabbitmq-test -p 5672:5672 -p 15672:15672 rabbitmq:3.13-management-alpine

# Set env
export RABBITMQ_URL=amqp://guest:guest@localhost:5672/

# Run a cross-service integration test
pytest tests/integration/test_messaging.py -v
```

A ready-to-run integration test is provided at `tests/integration/test_messaging.py` (see below).

### Verifying consumers are running

When a service starts successfully, you should see a log line like:

```
INFO  app.messaging.subscriber  Bound queue triage-service_events to routing key visit.created
```

In the RabbitMQ Management UI (`http://localhost:15672` — login `guest`/`guest`), navigate to **Queues** and you will see durable queues named `{service-name}_events` with active consumers.

### Quick CLI check

```bash
# Publish a test event manually (requires aio-pika installed)
python -c "
import asyncio, json, os
os.environ['RABBITMQ_URL'] = 'amqp://guest:guest@localhost:5672/'
from app.messaging.publisher import publish_event
asyncio.run(publish_event('test.heartbeat', {'msg': 'ok'}))
"
```

---

## API Authentication

All endpoints (except `/auth/login` and `/auth/password-reset`) require a Bearer JWT:

```
Authorization: Bearer <access_token>
```

**Token structure:**

```json
{
  "sub": "user_uuid",
  "tenant_id": "hospital_uuid",
  "role": "doctor",
  "exp": 1718000000,
  "iat": 1717998200
}
```

Access tokens expire in **30 minutes**. Use `POST /api/v1/auth/refresh` with your refresh token to obtain a new access token without re-logging in.

### Keycloak Integration

- **Realm per Hospital** — Each hospital gets its own realm when it registers.
- **Client `hospital-api`** — Direct access grants enabled, service accounts enabled.
- **Protocol Mapper** — `tenant-id-mapper` maps the `tenant_id` user attribute to the JWT `tenant_id` claim.
- **Direct Grant Flow** — Non-interactive password grant authentication.

### Impersonation (Super Admin)

Super admins can generate **impersonation tokens** to view data within a tenant without switching accounts:

```
POST /api/v1/auth/impersonate
Authorization: Bearer <super_admin_token>
{ "target_tenant_id": "hosp-001" }
```

The impersonation token is HS256-signed with `SECRET_KEY`, has `scope: "readonly"`, and TTL of 900 seconds (configurable via `IMPERSONATION_TOKEN_TTL`).

---

## Role Reference

| Role             | Access                                               |
| ---------------- | ---------------------------------------------------- |
| `super_admin`    | Master service only — no access to any hospital data |
| `hospital_admin` | Admin, reports, audit logs for their hospital only   |
| `receptionist`   | Reception module                                     |
| `triage_nurse`   | Triage module                                        |
| `doctor`         | Consultation, ward, investigation results (read)     |
| `lab_technician` | Laboratory module                                    |
| `radiographer`   | Radiology module                                     |
| `pharmacist`     | Pharmacy module                                      |
| `cashier`        | Billing module                                       |
| `patient`        | Patient portal access                                |
| `hospital_user`  | Base-level hospital access                           |

Roles are enforced by the `require_role()` FastAPI dependency on every protected endpoint. Attempting to access an endpoint with the wrong role returns `403 Forbidden`.

---

## Environment Variables

Copy `.env.example` to `.env`. Required variables:

| Variable                                                  | Description                                          |
| --------------------------------------------------------- | ---------------------------------------------------- |
| `SECRET_KEY`                                              | JWT signing secret — minimum 64-character hex string |
| `MASTER_DB_URL`                                           | PostgreSQL connection string for the Master DB       |
| `REDIS_URL`                                               | Redis connection string                              |
| `RABBITMQ_URL`                                            | RabbitMQ AMQP connection string                      |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` | Transactional email                                  |
| `KEYCLOAK_URL`                                            | Keycloak base URL                                    |
| `KEYCLOAK_REALM`                                          | Default super admin realm                            |
| `KEYCLOAK_CLIENT_ID`                                      | OIDC client ID                                       |
| `KEYCLOAK_CLIENT_SECRET`                                  | OIDC client secret                                   |
| `KEYCLOAK_ADMIN_USERNAME`                                 | Keycloak admin username                              |
| `KEYCLOAK_ADMIN_PASSWORD`                                 | Keycloak admin password                              |
| `TENANT_DB_ENCRYPTION_KEY`                                | 32-byte base64-encoded Fernet key for DSN encryption |
| `AUTH_SERVICE_URL`                                        | Internal URL for auth-service                        |
| `MASTER_SERVICE_URL`                                      | Internal URL for master-service                      |
| `RECEPTION_SERVICE_URL`                                   | Internal URL for reception-service                   |
| `TRIAGE_SERVICE_URL`                                      | Internal URL for triage-service                      |
| `CONSULTATION_SERVICE_URL`                                | Internal URL for consultation-service                |
| `LABORATORY_SERVICE_URL`                                  | Internal URL for laboratory-service                  |
| `RADIOLOGY_SERVICE_URL`                                   | Internal URL for radiology-service                   |
| `PHARMACY_SERVICE_URL`                                    | Internal URL for pharmacy-service                      |
| `BILLING_SERVICE_URL`                                     | Internal URL for billing-service                     |
| `WARD_SERVICE_URL`                                        | Internal URL for ward-service                        |
| `ADMIN_SERVICE_URL`                                       | Internal URL for admin-service                       |
| `NOTIFICATION_SERVICE_URL`                                | Internal URL for notification-service                  |
| `REPORT_SERVICE_URL`                                      | Internal URL for report-service                      |
| `IMPERSONATION_TOKEN_TTL`                                 | Impersonation token lifetime (seconds, default 900) |
| `SUSPENSION_CHECK_INTERVAL`                               | Background suspension check interval (seconds)       |
| `SUSPENDED_BLOCKLIST_TTL`                                 | Suspended tenant blocklist TTL (seconds)             |
| `ALLOWED_ORIGINS`                                         | CORS origins (comma-separated)                       |

See `.env.example` for the full list with example values.

**Never commit `.env` to version control.** It is in `.gitignore`. In production, inject secrets via Kubernetes Secrets or AWS Secrets Manager.

---

## Test Users

| Username | Password | Role | Tenant |
|----------|----------|------|--------|
| `superadmin` | `Nassir_05` | `super_admin` | ALL (no tenant_id) |
| `hospitaladmin` | `Nassir_05` | `hospital_admin` | `hosp-001` |
| `nurse2` | `Nassir_05` | `nurse` | `hosp-001` |
| `doctor1` | `Nassir_05` | `doctor` | `hosp-001` |
| `clinician1` | `Nassir_05` | `clinician` | `hosp-001` |
| *(signup)* | *(your choice)* | `hospital_admin` | *(auto-assigned)* |

Create test users with:
```bash
venv\Scripts\python.exe scripts/create_superuser.py --username=superadmin --password=superadmin123 --email=admin@hosp.com --role=super_admin
venv\Scripts\python.exe scripts/create_superuser.py --username=hospitaladmin --password=admin12345 --email=hospitaladmin@hospital.com --role=hospital_admin --hospital-id=hosp-001
```

---

## License

Proprietary — Hospital Patient Flow System. All rights reserved.
