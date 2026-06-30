# Pharmacy Service — Developer Guide

This document covers the **pharmacy-service** microservice: what has been built, how to run it locally, how tenant database migrations work (including pharmacy tables), and how to test the API.

---

## Overview

| Item | Value |
|------|-------|
| Service folder | `HospitalSystem/services/pharmacy-service` |
| Container name | `hospital-pharmacy-service` |
| Port | **8015** |
| API prefix | `/api/v1/pharmacy` |
| Gateway route | `http://localhost:8000/api/v1/pharmacy/*` |
| Direct Swagger | `http://localhost:8015/docs` |
| Auth role required | `pharmacist` (Keycloak JWT `realm_access.roles`) |
| Tenant resolution | JWT `tenant_id` → master DB `tenants` table → encrypted tenant DSN |

Pharmacy uses the **per-tenant PostgreSQL** pattern: each hospital has its own database named `tenant_{tenant_id}` (e.g. `tenant_hosp-citygeneral`). Clinical/pharmacy tables live in the tenant DB, not in `hospital_master`.

---

## Architecture

```
Client / Frontend
    ↓
API Gateway (port 8000)  →  /api/v1/pharmacy/*
    ↓
pharmacy-service (port 8015)
    ↓
hospital_master (tenants table — resolve tenant DSN)
    ↓
tenant_{tenant_id} (drug_inventory, drug_inventory_transactions, …)
```

---

## Project Structure

```
pharmacy-service/
├── app/
│   ├── api/v1/
│   │   ├── router.py          # All 14 HTTP endpoints
│   │   └── schemas.py         # Pydantic request/response models
│   ├── core/                  # Config, security, tenant auth, middleware
│   ├── db/                    # Master + tenant async DB sessions
│   ├── models/
│   │   └── pharmacy.py        # DrugInventory, DrugInventoryTransaction ORM
│   ├── services/
│   │   ├── pharmacy.py        # Stub logic (queue, Rx, dispense, labels, notifications)
│   │   └── inventory.py       # Real DB logic for inventory
│   ├── main.py
│   └── dependencies.py
├── tests/
│   ├── unit/
│   └── integration/
├── docs/
│   └── pharmacy_service.md    # This file
├── Dockerfile
├── requirements.txt
└── pytest.ini
```

**Shared tenant migrations** (not inside pharmacy-service) live at:

```
HospitalSystem/migrations/tenant/
├── alembic.ini
├── env.py
└── versions/
    ├── 0001_initial_tenant_schema.py
    ├── …
    ├── 0006_add_radiology_reports.py
    └── 0007_add_pharmacy_inventory.py   ← pharmacy tables
```

---

## What Has Been Done

### Phase 1 — API surface (complete)

All **14 endpoints** are implemented with Pydantic schemas, OpenAPI tags, `require_role("pharmacist")`, and tenant middleware. Most endpoints return **stub data** for now.

| Tag | Method | Path | Status |
|-----|--------|------|--------|
| Queue | GET | `/queue` | Stub |
| Queue | PATCH | `/queue/{queue_id}/call` | Stub |
| Prescriptions | GET | `/prescriptions/{visit_id}` | Stub |
| Prescriptions | GET | `/prescriptions/{visit_id}/interaction-check` | Stub |
| Dispensing | POST | `/dispense` | Stub |
| Dispensing | GET | `/dispense/{visit_id}/summary` | Stub |
| Inventory | GET | `/inventory` | **Real DB** |
| Inventory | GET | `/inventory/{inventory_id}` | **Real DB** |
| Inventory | GET | `/inventory/low-stock-alerts` | **Real DB** |
| Inventory | POST | `/inventory/restock` | **Real DB** |
| Inventory | POST | `/inventory/adjust` | **Real DB** |
| Labels | POST | `/labels/generate` | Stub |
| Notifications | GET | `/notifications` | Stub |
| Notifications | PATCH | `/notifications/{notification_id}/read` | Stub |

### Phase 2 — Inventory (complete in code)

- **Migration:** `0007_add_pharmacy_inventory.py`
  - Creates `drug_inventory` and `drug_inventory_transactions`
  - Seeds two sample drugs
- **ORM models:** `app/models/pharmacy.py`
- **Service:** `app/services/inventory.py` (list, detail, restock, adjust, low-stock alerts)
- **Tests:** `tests/unit/test_inventory.py` + integration tests with SQLite in-memory

### Seed inventory IDs (for testing)

| Drug | inventory_id | Stock | Reorder |
|------|--------------|-------|---------|
| Amoxicillin | `e5000005-0005-4005-8005-000000000005` | 179 | 100 |
| Metronidazole (low stock) | `e5000005-0005-4005-8005-000000000099` | 12 | 50 |

### Stub IDs (Phase 1 placeholders)

| Entity | UUID |
|--------|------|
| Visit | `b2000002-0002-4002-8002-000000000002` |
| Queue item | `a1000001-0001-4001-8001-000000000001` |
| Pending Rx | `d4000004-0004-4004-8004-000000000004` |
| Dispensing | `f6000006-0006-4006-8006-000000000006` |
| Notification | `a7000007-0007-4007-8007-000000000007` |

### Planned future phases

| Phase | Scope |
|-------|-------|
| Phase 3 | Queue + prescriptions (read from tenant DB) |
| Phase 4 | Billing gate before dispense |
| Phase 5 | Full dispense workflow + stock deduction |
| Phase 6 | Events, labels, notifications, frontend wiring |

---

## Running the Service

### Docker (recommended)

From `HospitalSystem/infrastructure`:

```powershell
docker compose up -d --build pharmacy-service
```

**Important:** After code changes, rebuild — `docker compose restart` is not enough.

Health check:

```powershell
curl http://localhost:8015/health
```

Swagger UI:

```
http://localhost:8015/docs
```

Via gateway:

```
http://localhost:8000/api/v1/pharmacy/inventory
```

### Local (without Docker)

```powershell
cd HospitalSystem/services/pharmacy-service
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8015 --reload
```

Copy `.env.example` to `.env` and set `DATABASE_URL`, Keycloak, and `TENANT_DB_ENCRYPTION_KEY` to match your environment.

---

## Authentication for API Testing

1. Log in (auth-service or gateway):

   ```
   POST http://localhost:8000/api/v1/auth/login
   ```

2. Use the returned JWT as:

   ```
   Authorization: Bearer <token>
   ```

3. The user must have the **`pharmacist`** role in Keycloak.

4. The JWT must include **`tenant_id`** matching a row in `hospital_master.tenants` with a valid encrypted DSN.

---

## Tenant Databases & Alembic Migrations

### How migrations run automatically

When a new hospital signs up (`POST /api/v1/auth/signup`), provisioning is automatic:

```
auth-service (signup)
    → creates tenant in master DB (status: pending)
    → publishes tenant.created event

master-service (subscriber)
    → CREATE DATABASE tenant_{tenant_id}
    → alembic upgrade head (tenant migrations)
    → encrypt DSN → update tenants table
    → publish tenant.provisioned
```

Implementation: `services/master-service/app/services/provision.py`

**New developers using the normal signup flow do not need to run Alembic manually** — the tenant DB is created and migrated automatically.

---

### When you need to run Alembic manually

Run migrations manually when:

- You add a **new tenant migration** (e.g. `0008_...`) and existing tenants need upgrading
- You **manually created** a tenant database (bypassing provisioning)
- Provisioning **failed** partway through
- You are developing migrations locally against a test tenant DB

---

### Option A — Migrate one tenant (script)

From `HospitalSystem` root:

```powershell
$env:MASTER_DB_URL = "postgresql://postgres:nasr@localhost:5432/hospital_master"
python scripts/migrate_tenant.py --tenant-id hosp-citygeneral
```

### Option B — Migrate via Docker (master-service container)

`master-service` mounts `../migrations` at `/app/migrations`.

```powershell
cd HospitalSystem/infrastructure

docker compose exec -e TENANT_DB_URL=postgresql://postgres:nasr@postgres-master:5432/tenant_hosp-citygeneral `
  master-service alembic -c /app/migrations/tenant/alembic.ini upgrade head
```

Replace `hosp-citygeneral` with your tenant ID.

### Option C — Migrate all tenants

```powershell
python scripts/migrate_all_tenants.py
```

Dry run (status only):

```powershell
python scripts/migrate_all_tenants.py --dry-run
```

### Check current migration revision

```sql
\c tenant_hosp-citygeneral
SELECT * FROM alembic_version;
```

### List databases

```sql
\l
```

Tenant DBs are named `tenant_{tenant_id}`.

---

## Pharmacy Tables (Migration 0007)

Migration file: `migrations/tenant/versions/0007_add_pharmacy_inventory.py`

| Table | Purpose |
|-------|---------|
| `drug_inventory` | Current stock levels per drug |
| `drug_inventory_transactions` | Audit trail for restock, adjust, dispense |

### Verify tables and seed data in psql

```powershell
docker compose exec -it postgres-master psql -U postgres
```

```sql
\c tenant_hosp-citygeneral
\dt drug_*
SELECT drug_name, quantity_in_stock, reorder_level FROM drug_inventory;
```

If the table exists but has **0 rows**, run the seed INSERT from migration `0007` (see that file's `op.execute(...)` block) or re-run migration 0007 after fixing earlier migration failures.

### Manual seed INSERT (if tables exist but are empty)

```sql
INSERT INTO drug_inventory (
    inventory_id, drug_name, brand_name, drug_code, category, unit,
    quantity_in_stock, reorder_level, unit_cost, unit_price, location, is_active
) VALUES
(
    'e5000005-0005-4005-8005-000000000005',
    'Amoxicillin', 'Amoxil', 'AMX-500', 'Antibiotic', 'tablets',
    179, 100, 50.00, 80.00, 'Shelf B-3', true
),
(
    'e5000005-0005-4005-8005-000000000099',
    'Metronidazole', 'Flagyl', 'MTZ-400', 'Antibiotic', 'tablets',
    12, 50, 30.00, 55.00, 'Shelf C-1', true
)
ON CONFLICT (inventory_id) DO NOTHING;
```

---

## Known Migration Issue (0005)

On a **fresh** tenant DB, migration `0005_align_patients_table_with_model` can fail:

```
column "id" is of type integer but expression is of type uuid
UPDATE patients SET id = gen_random_uuid() WHERE id IS NULL
```

**Cause:** `0001` creates `patients.id` as INTEGER. `0005` assumes `id` is missing or UUID and tries to assign UUIDs to an integer column.

**Impact:** Alembic stops before `0006` and `0007`, so **pharmacy tables are never created** via the normal migration chain.

**Workarounds for local dev:**

1. **Fix migration 0005** in the repo (proper long-term fix), drop tenant DB, reprovision
2. **Manually run** the SQL from `0007_add_pharmacy_inventory.py` in psql (tables + seed data only)
3. Use the **signup/provisioning flow** after 0005 is fixed

Do **not** manually create empty tenant DBs without running migrations unless you know what you are doing.

---

## Running Tests

From `pharmacy-service`:

```powershell
cd HospitalSystem/services/pharmacy-service
pytest
```

- Unit tests: stub endpoints + inventory logic
- Integration tests: use SQLite in-memory (`tests/conftest.py`)
- `asyncio_mode = auto` in `pytest.ini`

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Master DB (`hospital_master`) |
| `TENANT_DB_ENCRYPTION_KEY` | Decrypt tenant DSNs from master DB |
| `KEYCLOAK_*` | JWT validation |
| `DEFAULT_HOSPITAL_ID` | Fallback tenant (dev) |
| `RABBITMQ_URL` | Event subscriber (future) |
| `ALLOWED_ORIGINS` | CORS |

See `.env.example` in this folder and `infrastructure/docker-compose.yml` for Docker values.

---

## Troubleshooting

| Problem | What to check |
|---------|----------------|
| Empty inventory API | Wrong tenant DB; seed data missing; migration 0007 not applied |
| 401 / 403 | Missing `pharmacist` role or invalid JWT |
| Tenant DB not found | `hospital_master.tenants` row + encrypted DSN for your `tenant_id` |
| Code changes not reflected | `docker compose up -d --build pharmacy-service` |
| Migration fails at 0005 | See [Known Migration Issue](#known-migration-issue-0005) |
| `tenant_default-hospital` does not exist | Your tenant ID is in the JWT — use `tenant_{that_id}` |

### Useful commands

```powershell
# Service logs
docker compose logs -f pharmacy-service

# psql
docker compose exec -it postgres-master psql -U postgres

# Current database in psql
SELECT current_database();

# Quit psql
\q
```

---

## Quick Start Checklist for New Developers

1. Clone repo and start stack: `docker compose up -d` from `infrastructure/`
2. Sign up a hospital via auth API **or** use existing tenant `hosp-citygeneral`
3. Confirm tenant DB exists: `\l` in psql → `tenant_hosp-citygeneral`
4. Confirm pharmacy tables: `\dt drug_*` and 2 seed rows in `drug_inventory`
5. Log in as user with `pharmacist` role
6. Open `http://localhost:8015/docs` or call `GET /api/v1/pharmacy/inventory`
7. Run `pytest` in `pharmacy-service` before pushing changes

---

## Related Files

| File | Role |
|------|------|
| `app/api/v1/router.py` | Route definitions |
| `app/services/inventory.py` | Inventory business logic |
| `app/services/pharmacy.py` | Stub responses for non-inventory endpoints |
| `migrations/tenant/versions/0007_add_pharmacy_inventory.py` | DB schema + seed |
| `infrastructure/docker-compose.yml` | Service port 8015, env vars |
| `services/api-gateway/app/proxy.py` | Gateway proxy to pharmacy |
| `scripts/migrate_tenant.py` | Single-tenant migration CLI |
| `services/master-service/app/services/provision.py` | Auto DB create + migrate on signup |
