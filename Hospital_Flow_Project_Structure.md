# Hospital Patient Flow System — FastAPI Project Structure

**Version:** 1.0 | **Framework:** FastAPI | **Architecture:** Multi-Tenant SaaS

---

## Overview

This project follows a layered architecture:
- **`api/`** — HTTP route handlers only (no business logic)
- **`services/`** — all business logic
- **`models/`** — SQLAlchemy ORM models
- **`db/`** — database connection management (master + per-tenant)
- **`core/`** — security, permissions, middleware, tenant resolution

---

## Full Project Tree

```
hospital_flow/
│
├── .env                          # Environment variables — DB URLs, secret keys, JWT settings (never commit)
├── .env.example                  # Template for .env — safe to commit, shows required variables
├── .gitignore                    # Excludes .env, __pycache__, .venv, migrations/versions/*.pyc
├── requirements.txt              # All Python dependencies
├── docker-compose.yml            # PostgreSQL + Redis + FastAPI app containers for local development
├── Dockerfile                    # Container definition for the FastAPI application
├── alembic.ini                   # Alembic migration tool configuration
├── README.md                     # Setup, run, and deployment instructions
│
└── app/                          # Main application package
    │
    ├── main.py                   # FastAPI app entry point — registers all routers, CORS, middleware, exception handlers
    ├── config.py                 # Settings class using pydantic-settings — reads all values from .env
    ├── dependencies.py           # Shared FastAPI dependencies — get_db(), get_current_user(), get_tenant_db(), require_role()
    ├── exceptions.py             # Custom exception classes (TenantNotFound, Unauthorized, etc.) and global handlers
    │
    ├── api/                      # All HTTP route handlers organised by module
    │   └── v1/                   # API version 1 — all endpoints prefixed with /api/v1/
    │       ├── __init__.py
    │       ├── router.py         # Aggregates all module routers into one — imported by main.py
    │       │
    │       ├── auth/             # Authentication endpoints
    │       │   ├── __init__.py
    │       │   ├── router.py     # POST /login, POST /logout, POST /refresh, POST /password-reset, POST /password-reset/confirm
    │       │   └── schemas.py    # LoginRequest, TokenResponse, PasswordResetRequest, PasswordResetConfirm
    │       │
    │       ├── reception/        # Patient registration and visit management
    │       │   ├── __init__.py
    │       │   ├── router.py     # CRUD for patients, visits, insurance records, queue entries
    │       │   └── schemas.py    # PatientCreate, PatientResponse, VisitCreate, VisitResponse, InsuranceCreate, QueueResponse
    │       │
    │       ├── triage/           # Triage assessment and vital signs
    │       │   ├── __init__.py
    │       │   ├── router.py     # POST /triage, GET /triage/{visit_id}, PATCH /triage/{id}
    │       │   └── schemas.py    # TriageCreate, TriageResponse, VitalsUpdate
    │       │
    │       ├── consultation/     # Doctor consultation, diagnoses, investigations, prescriptions
    │       │   ├── __init__.py
    │       │   ├── router.py     # Endpoints for consultations, diagnoses, investigation requests, prescriptions, referrals
    │       │   └── schemas.py    # ConsultationCreate, DiagnosisCreate, InvestigationRequestCreate, PrescriptionCreate
    │       │
    │       ├── laboratory/       # Lab requests, specimen tracking, results
    │       │   ├── __init__.py
    │       │   ├── router.py     # Collect specimen, enter result, verify result, critical value notification
    │       │   └── schemas.py    # LabResultCreate, SpecimenUpdate, ResultResponse, CriticalValueAlert
    │       │
    │       ├── radiology/        # Imaging scheduling and radiologist reports
    │       │   ├── __init__.py
    │       │   ├── router.py     # Schedule imaging, submit report, update status, upload image reference
    │       │   └── schemas.py    # RadiologyReportCreate, RadiologyReportResponse, ImagingStatusUpdate
    │       │
    │       ├── pharmacy/         # Drug dispensing and inventory management
    │       │   ├── __init__.py
    │       │   ├── router.py     # Dispense drug, check stock, restock, view inventory, transaction history
    │       │   └── schemas.py    # DispensingCreate, InventoryUpdate, StockTransactionCreate, LowStockAlert
    │       │
    │       ├── billing/          # Bills, line items, payments, insurance claims
    │       │   ├── __init__.py
    │       │   ├── router.py     # Generate bill, add line items, record payment, submit insurance claim
    │       │   └── schemas.py    # BillCreate, BillItemCreate, PaymentCreate, InsuranceClaimCreate, BillResponse
    │       │
    │       ├── ward/             # Inpatient admissions, beds, orders, nursing notes
    │       │   ├── __init__.py
    │       │   ├── router.py     # Admit patient, assign bed, create orders, nursing notes, discharge patient
    │       │   └── schemas.py    # AdmissionCreate, BedResponse, InpatientOrderCreate, NursingNoteCreate
    │       │
    │       ├── admin/            # Hospital admin — staff, departments, fees, audit logs
    │       │   ├── __init__.py
    │       │   ├── router.py     # Manage users, departments, fee schedules, view and export audit logs
    │       │   └── schemas.py    # UserCreate, UserResponse, DepartmentCreate, FeeScheduleCreate, AuditLogResponse
    │       │
    │       ├── notifications/    # In-system notifications
    │       │   ├── __init__.py
    │       │   ├── router.py     # GET /notifications, PATCH /notifications/{id}/read, DELETE /notifications/{id}
    │       │   └── schemas.py    # NotificationResponse, NotificationMarkRead
    │       │
    │       └── superadmin/       # System owner panel — tenants, subscriptions, invoices
    │           ├── __init__.py
    │           ├── router.py     # Manage hospitals, activate/suspend tenants, subscriptions, invoices, SaaS payments
    │           └── schemas.py    # TenantCreate, TenantResponse, SubscriptionCreate, InvoiceResponse, SaasPaymentCreate
    │
    ├── core/                     # Cross-cutting concerns shared across all modules
    │   ├── __init__.py
    │   ├── security.py           # JWT creation and verification, bcrypt password hashing, MFA (TOTP) helpers
    │   ├── tenant.py             # Resolves tenant_id from JWT → queries Master DB → returns tenant DB connection string
    │   ├── permissions.py        # Role-based access control — decorators and dependency checks per role (doctor, nurse, etc.)
    │   └── middleware.py         # Audit log middleware (writes every action to audit_logs), request timing, tenant context injection
    │
    ├── db/                       # Database connection and session management
    │   ├── __init__.py
    │   ├── master.py             # SQLAlchemy engine and SessionFactory for the Master DB (tenants, subscriptions, super admins)
    │   ├── tenant.py             # Dynamically creates a SQLAlchemy engine per tenant using db_connection_string from Master DB
    │   ├── base.py               # Declarative base class — all ORM models inherit from this
    │   └── session.py            # get_master_db() and get_tenant_db() generator functions used as FastAPI dependencies
    │
    ├── models/                   # SQLAlchemy ORM models — one file per module, mirrors the database schema
    │   ├── __init__.py           # Imports all models so Alembic can auto-detect tables for migrations
    │   ├── reception.py          # Patient, Visit, PatientInsurance, Queue
    │   ├── triage.py             # TriageAssessment
    │   ├── consultation.py       # Consultation, Diagnosis, InvestigationRequest, Prescription
    │   ├── laboratory.py         # LabResult, Specimen
    │   ├── radiology.py          # RadiologyReport
    │   ├── pharmacy.py           # DispensingRecord, DrugInventory, DrugInventoryTransaction
    │   ├── billing.py            # Bill, BillItem, Payment, InsuranceClaim
    │   ├── ward.py               # Bed, Admission, InpatientOrder, NursingNote
    │   ├── admin.py              # User, Department, FeeSchedule, AuditLog, Notification
    │   ├── auth.py               # PasswordResetToken, RefreshToken
    │   └── master.py             # Tenant, SubscriptionPlan, Subscription, Invoice, SaasPayment,
    │                             # SuperAdmin, SuperAdminAuditLog, Announcement, SubscriptionAuditLog
    │
    ├── services/                 # Business logic layer — routers call services, never write logic in routers
    │   ├── __init__.py
    │   ├── auth.py               # Login flow, JWT generation, token refresh, password reset, MFA verification, forced logout
    │   ├── reception.py          # Patient registration, visit creation, duplicate patient detection, queue entry creation
    │   ├── triage.py             # Triage assessment saving, triage category assignment, queue priority update
    │   ├── consultation.py       # Consultation workflow, diagnosis saving, investigation request routing to lab or radiology
    │   ├── laboratory.py         # Specimen tracking, result entry, critical value detection and notification trigger
    │   ├── radiology.py          # Imaging scheduling, report submission, status progression
    │   ├── pharmacy.py           # Dispense logic, stock deduction, drug interaction check, low-stock alert trigger
    │   ├── billing.py            # Bill generation from visit, line item addition, payment recording, insurance claim submission
    │   ├── ward.py               # Admission logic, bed availability check, bed assignment and release, discharge workflow
    │   ├── admin.py              # User creation and deactivation, fee schedule management, audit log writing
    │   ├── notifications.py      # Create, dispatch, and mark-read in-system notifications (critical results, queue calls, etc.)
    │   └── tenant.py             # Tenant lookup by domain/code, connection string resolution, subscription status validation
    │
    ├── migrations/               # Alembic database migration scripts
    │   ├── env.py                # Alembic environment config — handles both Master DB and Tenant DB migrations
    │   ├── script.py.mako        # Template used to generate new migration files
    │   └── versions/             # Auto-generated and hand-edited migration files
    │       ├── 0001_initial_master_schema.py   # Creates all Master DB tables (tenants, subscriptions, super_admins, etc.)
    │       └── 0002_initial_tenant_schema.py   # Creates all Tenant DB tables (run once per new hospital onboarded)
    │
    └── tests/                    # Full test suite
        ├── __init__.py
        ├── conftest.py           # pytest fixtures — test DB sessions, TestClient, pre-authenticated user tokens
        ├── unit/                 # Unit tests for individual service functions
        │   ├── test_auth.py      # Login, JWT decode, password reset, MFA unit tests
        │   ├── test_billing.py   # Bill calculation, discount logic, payment recording unit tests
        │   └── test_pharmacy.py  # Stock deduction, low-stock threshold, drug interaction unit tests
        └── integration/          # End-to-end API tests using FastAPI TestClient
            ├── test_patient_flow.py        # Full visit flow: register → triage → consult → lab → dispense → bill
            └── test_tenant_isolation.py    # Verifies Hospital A's JWT cannot access Hospital B's data
```

---

## Key Files Explained

### `app/main.py`
Entry point. Creates the FastAPI app, registers all routers from `api/v1/router.py`, adds middleware, and sets up CORS.

### `app/dependencies.py`
Central place for all FastAPI dependency injection:
- `get_master_db()` — yields a Master DB session
- `get_tenant_db()` — resolves tenant from JWT, yields that hospital's DB session
- `get_current_user()` — decodes JWT, returns the logged-in user
- `require_role(role)` — raises 403 if the user's role doesn't match

### `app/core/tenant.py`
The most critical file for multi-tenancy. On every request it:
1. Reads `tenant_id` from the JWT token
2. Queries Master DB: `SELECT db_connection_string FROM tenants WHERE tenant_id = ?`
3. Decrypts the connection string
4. Returns it so `get_tenant_db()` can open a connection to the right hospital database

### `app/db/tenant.py`
Creates a dynamic SQLAlchemy engine per tenant. Engines are cached in memory (connection pool) so the same hospital doesn't re-create an engine on every request.

### `app/migrations/env.py`
Handles two migration targets:
- Run with `--target master` to migrate the Master DB
- Run with `--target tenant --tenant-id <id>` to migrate a specific hospital's database
- Run with `--target all-tenants` to apply a migration to every hospital at once

### `app/core/middleware.py`
Intercepts every request and automatically writes an entry to `audit_logs` after the response is sent — capturing user, action, table affected, old and new values.

---

## Recommended Python Packages

| Package | Purpose |
|---|---|
| `fastapi` | Web framework |
| `uvicorn` | ASGI server |
| `sqlalchemy` | ORM and query builder |
| `alembic` | Database migrations |
| `pydantic` / `pydantic-settings` | Schema validation and config |
| `python-jose` | JWT creation and verification |
| `passlib[bcrypt]` | Password hashing |
| `pyotp` | TOTP MFA (Google Authenticator compatible) |
| `psycopg2-binary` | PostgreSQL driver |
| `redis` | Session/token blacklist and caching |
| `pytest` / `httpx` | Testing |
| `python-dotenv` | Load .env in development |

---

## How Tenant Routing Works (Summary)

```
Request arrives
    ↓
Middleware extracts tenant_id from JWT
    ↓
core/tenant.py queries Master DB for db_connection_string
    ↓
db/tenant.py creates (or reuses cached) SQLAlchemy engine
    ↓
dependencies.py yields a session to that hospital's database
    ↓
Router → Service → Model all run against that hospital's DB only
    ↓
Response returned, audit log written
```

No patient data ever touches the Master DB. The Master DB only stores the address of where each hospital's data lives.

---

