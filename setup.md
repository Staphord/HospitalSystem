# Hospital Management System - Microservices Setup Guide

> **Note**: This guide covers the **microservices architecture** (16 services in Docker).

---

## Architecture Overview

The system is split into **16 independent microservices** orchestrated by Docker Compose:

| Service | Port | Role |
|---------|------|------|
| **api-gateway** | 8000 | JWT verification, tenant resolution, reverse-proxy to downstream services |
| **auth-service** | 8001 | Login, signup, refresh, MFA, password reset, impersonation |
| **master-service** | 8002 | Superadmin portal, tenant management, suspension jobs |
| **reception-service** | 8010 | Patient registration, appointments, check-in |
| **triage-service** | 8011 | Patient triage, priority scoring |
| **consultation-service** | 8012 | Doctor consultations, clinical notes |
| **laboratory-service** | 8013 | Lab orders and results |
| **radiology-service** | 8014 | Imaging orders and reports |
| **pharmacy-service** | 8015 | Prescriptions, medication dispensing |
| **billing-service** | 8016 | Invoices, payments, insurance claims |
| **ward-service** | 8017 | Bed management, admissions, discharges |
| **patient-service** | 8005 | Patient registration, search, lookup, delete |
| **visit-service** | 8006 | Visit creation, payment type, insurance verification, triage queue |
| **admin-service** | 8018 | Hospital admin user CRUD within a tenant |
| **notification-service** | 8019 | Email, SMS, push notifications |
| **report-service** | 8020 | Analytics, dashboards, PDF exports |

**Infrastructure**
| Service | Port | Role |
|---------|------|------|
| postgres-master | 5432 | Global PostgreSQL (tenants, super_admins, audit logs) |
| redis | 6380 | Caching, tenant DSN cache, rate-limit storage |
| rabbitmq | 5672 / 15672 | Async messaging between services |
| keycloak | 8080 | OIDC authentication; the base realm/client are imported on a fresh volume |

---

## Prerequisites

1. **Docker Desktop** installed and running (Windows / macOS / Linux)
2. **No other services** on ports **5432**, **6380**, **5672**, **15672**, **8080**, or **8000–8020**
   - If you have a local Redis on port 6379, the Docker Compose now maps Redis to **6380** to avoid the conflict.

---

## Quick Start (Docker Compose)

> All Docker commands below must be run from the **`infrastructure/`** directory,
> which holds the only approved Compose file:
> ```bash
> cd infrastructure
> ```

### 1. Build and start everything

```bash
docker compose up --build -d
```

This will:
- Build all 16 service images
- Start `postgres-master`, `redis`, `rabbitmq`, and Keycloak
- Import the base `hospital-realm`, OIDC client, and base roles on first startup
- Wait for infrastructure health checks to pass
- Start all microservices in dependency order

### 2. Check service health

```bash

#!/bin/bash
clear

echo ""
echo "🏥 ============================================"
echo "🏥        SERVICE HEALTH CHECK"
echo "🏥 ============================================"
echo ""

check_service() {
    local name="$1"
    local url="$2"

    printf "🔍 %-15s " "$name"

    start=$(date +%s%3N)
    response=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$url")
    end=$(date +%s%3N)

    elapsed=$((end - start))

    if [ "$response" = "200" ]; then
        echo "✅ HEALTHY   | HTTP $response | ${elapsed}ms"
    else
        echo "❌ UNHEALTHY | HTTP $response | ${elapsed}ms"
    fi
}

check_service "Gateway"       "http://localhost:8000/health"
check_service "Auth"          "http://localhost:8001/health"
check_service "Master"        "http://localhost:8002/health"
check_service "Reception"     "http://localhost:8010/health"
check_service "Triage"        "http://localhost:8011/health"
check_service "Consultation"  "http://localhost:8012/health"
check_service "Laboratory"    "http://localhost:8013/health"
check_service "Radiology"     "http://localhost:8014/health"
check_service "Pharmacy"      "http://localhost:8015/health"
check_service "Billing"       "http://localhost:8016/health"
check_service "Ward"          "http://localhost:8017/health"
check_service "Patient"       "http://localhost:8005/health"
check_service "Visit"         "http://localhost:8006/health"
check_service "Admin"         "http://localhost:8018/health"
check_service "Notification"  "http://localhost:8019/health"
check_service "Report"        "http://localhost:8020/health"

echo ""
echo "🏥 ============================================"
echo "🏥        HEALTH CHECK COMPLETE"
echo "🏥 ============================================"
echo ""
```

All should return: `{"status":"ok","service":"..."}`

### 3. Watch logs

```bash
# All services
docker compose logs -f

# Specific service
docker compose logs -f api-gateway
docker compose logs -f auth-service
docker compose logs -f master-service
```

### 4. Stop everything

```bash
docker compose down
```

To remove all development volumes, including PostgreSQL and Keycloak data (full reset):

```bash
docker compose down -v
```

---

## Service URLs (via API Gateway)

All external requests go through the **API Gateway** on port 8000. The gateway resolves the tenant, verifies the JWT, and proxies to the correct downstream service.

| Endpoint | Downstream Service |
|----------|-------------------|
| `POST /api/v1/auth/login` | auth-service |
| `POST /api/v1/auth/signup` | auth-service |
| `GET /api/v1/superadmin/tenants` | master-service |
| `POST /api/v1/admin/users` | admin-service |
| `POST /api/v1/patients/register` | patient-service |
| `GET /api/v1/patients/search` | patient-service |
| `GET /api/v1/patients/{id}` | patient-service |
| `DELETE /api/v1/patients/{id}` | patient-service |
| `POST /api/v1/visits` | visit-service |
| `GET /api/v1/visits/queues/triage/today` | visit-service |
| `GET /api/v1/reception/patients` | reception-service |
| `GET /api/v1/triage/...` | triage-service |
| `GET /api/v1/consultation/...` | consultation-service |
| `GET /api/v1/laboratory/...` | laboratory-service |
| `GET /api/v1/radiology/...` | radiology-service |
| `GET /api/v1/pharmacy/...` | pharmacy-service |
| `GET /api/v1/billing/...` | billing-service |
| `GET /api/v1/ward/...` | ward-service |
| `GET /api/v1/notifications/...` | notification-service |
| `GET /api/v1/reports/...` | report-service |

## Swagger UI & API Documentation

### Why the Gateway docs only show `/health` and `/{full_path}`

The API Gateway's job is to **proxy** requests to downstream services. It does not import the actual endpoint definitions from `auth-service`, `master-service`, etc. — they run as separate containers. Therefore, the gateway's OpenAPI schema only documents its own routes:
- `GET /health` — Gateway health check
- `GET/POST/PUT/PATCH/DELETE /{full_path}` — Proxy catch-all

**The real endpoints** (login, signup, tenant management, user CRUD, etc.) are defined inside each downstream service.

### How to see the real endpoints

You have two options:

#### Option A: Access each service's Swagger directly (recommended)

Each service exposes its own interactive docs when `ENVIRONMENT=dev`:

| Service | Swagger UI URL |
|---------|----------------|
| Auth Service | http://localhost:8001/docs |
| Master Service | http://localhost:8002/docs |
| Patient Service | http://localhost:8005/docs |
| Visit Service | http://localhost:8006/docs |
| Admin Service | http://localhost:8018/docs |
| Reception Service | http://localhost:8010/docs |
| Triage Service | http://localhost:8011/docs |
| Consultation Service | http://localhost:8012/docs |
| Laboratory Service | http://localhost:8013/docs |
| Radiology Service | http://localhost:8014/docs |
| Pharmacy Service | http://localhost:8015/docs |
| Billing Service | http://localhost:8016/docs |
| Ward Service | http://localhost:8017/docs |
| Notification Service | http://localhost:8019/docs |
| Report Service | http://localhost:8020/docs |

> **Tip**: Bookmark `http://localhost:8001/docs` for auth endpoints (login, signup, refresh, password reset) and `http://localhost:8002/docs` for superadmin endpoints (tenant management, role creation).

#### Option B: Use the Gateway paths directly (for scripts / Postman)

All endpoints are reachable through the **gateway on port 8000** using the same path prefixes:

```bash
# Auth endpoints (proxied to auth-service:8001)
POST http://localhost:8000/api/v1/auth/login
POST http://localhost:8000/api/v1/auth/signup
POST http://localhost:8000/api/v1/auth/refresh
POST http://localhost:8000/api/v1/auth/logout
POST http://localhost:8000/api/v1/auth/forgot-password
POST http://localhost:8000/api/v1/auth/reset-password

# Superadmin endpoints (proxied to master-service:8002)
POST   http://localhost:8000/api/v1/superadmin/users
GET    http://localhost:8000/api/v1/superadmin/users
PATCH  http://localhost:8000/api/v1/superadmin/users/{id}
DELETE http://localhost:8000/api/v1/superadmin/users
GET    http://localhost:8000/api/v1/superadmin/tenants
POST   http://localhost:8000/api/v1/superadmin/tenants
PATCH  http://localhost:8000/api/v1/superadmin/tenants/{tenant_id}
POST   http://localhost:8000/api/v1/superadmin/roles

# Hospital admin endpoints (proxied to admin-service:8018)
POST   http://localhost:8000/api/v1/admin/users
GET    http://localhost:8000/api/v1/admin/users
PATCH  http://localhost:8000/api/v1/admin/users/{id}
DELETE http://localhost:8000/api/v1/admin/users

# Patient endpoints (proxied to patient-service:8005)
POST   http://localhost:8000/api/v1/patients/register
GET    http://localhost:8000/api/v1/patients
GET    http://localhost:8000/api/v1/patients/search
GET    http://localhost:8000/api/v1/patients/{id}
DELETE http://localhost:8000/api/v1/patients/{id}

# Visit endpoints (proxied to visit-service:8006)
POST   http://localhost:8000/api/v1/visits
GET    http://localhost:8000/api/v1/visits/queues/triage/today

# Clinical endpoints (reception, triage, etc.)
GET/POST http://localhost:8000/api/v1/reception/patients
GET/POST http://localhost:8000/api/v1/triage/...
GET/POST http://localhost:8000/api/v1/consultation/...
GET/POST http://localhost:8000/api/v1/laboratory/...
GET/POST http://localhost:8000/api/v1/radiology/...
GET/POST http://localhost:8000/api/v1/pharmacy/...
GET/POST http://localhost:8000/api/v1/billing/...
GET/POST http://localhost:8000/api/v1/ward/...
GET/POST http://localhost:8000/api/v1/notifications/...
GET/POST http://localhost:8000/api/v1/reports/...
```

---

## Database Setup

### Master Database

This Compose change does not alter Alembic files and does not add automatic migration
gate containers. The master service retains the migration behaviour already present in
the pulled repository. Check its startup log before testing data-dependent pages:

```bash
docker compose logs master-service
```

If Alembic reports multiple heads, missing columns, or an existing table/column, do not
stamp a revision or edit old migrations just to bypass the error. Record the error for
migration-chain review. Existing-tenant migration remains available through the
repository's `scripts/migrate_existing_tenants.py` workflow.

### Tenant Database Provisioning

When a new hospital signs up:
1. `auth-service` creates the tenant record in `postgres-master`
2. Publishes `tenant.created` event to RabbitMQ
3. `master-service` consumer creates the physical database `tenant_{tenant_id}`
4. Runs `alembic upgrade head` on the new database
5. Encrypts the DSN and updates the tenant record

---

## Environment Variables (Docker)

`infrastructure/docker-compose.yml` contains the Docker-network values for all services. You do **not** need a `.env` file to run the Docker stack.

Key overridden values:

| Variable | Docker Value | Why |
|----------|--------------|-----|
| `DATABASE_URL` | `postgresql://postgres:postgres@postgres-master:5432/hospital_master` | Internal Docker DNS |
| `REDIS_URL` | `redis://redis:6379/0` | Internal Docker DNS |
| `RABBITMQ_URL` | `amqp://guest:guest@rabbitmq:5672/` | Internal Docker DNS |
| `KEYCLOAK_URL` | `http://keycloak:8080` | Reach Compose-managed Keycloak over Docker DNS |
| `DB_ADMIN_URL` | `postgresql://postgres:postgres@postgres-master:5432/postgres` | Internal Docker DNS |
| `TENANT_DB_TEMPLATE` | `postgresql://postgres:postgres@postgres-master:5432/tenant_{tenant_id}` | Internal Docker DNS |
| `AUTH_SERVICE_URL` | `http://auth-service:8001` | Gateway → service internal routing |
| `MASTER_SERVICE_URL` | `http://master-service:8002` | Gateway → service internal routing |
| `ADMIN_SERVICE_URL` | `http://admin-service:8018` | Gateway → service internal routing |
| `PATIENT_SERVICE_URL` | `http://patient-service:8005` | Gateway → service internal routing |
| ... | ... | ... |

If you want to tweak local secrets or passwords, edit the `environment` blocks in `infrastructure/docker-compose.yml` directly.

---

## Common Issues

### Port already allocated (Redis 6379)

**Fixed**: Redis is now mapped to host port `6380` instead of `6379`:

```yaml
redis:
  ports:
    - "6380:6379"
```

If you still see port conflicts on other services, check what is listening:

```bash
# Windows
netstat -ano | findstr :PORT

# macOS / Linux
lsof -i :PORT
```

### Keycloak is not healthy

Keycloak is managed by the same Compose stack. Check its status and startup log:

```bash
docker compose ps keycloak
docker compose logs --tail 100 keycloak
```

The admin console is available at `http://localhost:8080` (`admin` / `admin` in development). The application containers reach it through `http://keycloak:8080`.

### Service unhealthy / stuck

If a service keeps restarting, check its logs:

```bash
docker compose logs --tail 50 <service-name>
```

Common causes:
- `postgres-master` is still initializing (health check timing)
- RabbitMQ is not ready yet
- A service is missing a required env variable

### Database tables not created

Do not run a manual schema initializer or stamp an Alembic revision just to bypass the
error. Capture the master-service log and current Alembic heads for review:

```bash
docker compose logs master-service
docker compose exec master-service python -m alembic -c /app/migrations/master/alembic.ini heads
```

---

## Test Users

Compose does not automatically create the three test users or register the proposed
`tenant_id` user-profile field. The pulled repository's existing setup script performs
that optional local setup:

```bash
python setup_keycloak.py
```

That script creates:

| Username | Password | Role | Portal |
|----------|----------|------|--------|
| `testuser` | `testpassword` | `hospital_user` | Hospital Portal |
| `adminuser` | `adminpassword` | `hospital_admin` | Hospital Portal |
| `superadmin` | `superadmin123` | `super_admin` | Super Admin Portal |

> **Note**: All users (including superadmins) authenticate through **Keycloak**. The superadmin login endpoint (`/api/v1/auth/superadmin/login`) verifies the `super_admin` realm role before issuing a token.
>
> `setup_keycloak.py` is the existing manual development setup path. To create another fully synced superadmin (Keycloak + local DB record), use `scripts/create_superuser.py`:

```bash
python scripts/create_superuser.py \
  --username=superadmin \
  --password=superadmin123 \
  --email=admin@hosp.com \
  --role=super_admin
```

You can also create a super admin inside the Docker container:

```bash
docker compose exec master-service python scripts/create_superuser.py \
  --username=superadmin2 \
  --password=superadmin123 \
  --email=admin2@hosp.com \
  --role=super_admin
```

Or test the signup flow directly:

```bash
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "hospital_name": "Test Hospital",
    "admin_username": "testadmin",
    "admin_password": "password123",
    "admin_email": "testadmin@hospital.com",
    "admin_full_name": "Test Admin"
  }'
```

---

## Local Development (without Docker)

If you want to run individual services locally (not in Docker), use the `.env` file in the project root and start each service with:

```bash
# Auth service
uvicorn services.auth-service.app.main:app --reload --port 8001

# Master service
uvicorn services.master-service.app.main:app --reload --port 8002

# etc.
```

Make sure your local `.env` points to `localhost` for Postgres, Redis, and Keycloak.

---

## Files Reference

| File | Purpose |
|------|---------|
| `infrastructure/docker-compose.yml` | Canonical local development stack; run `docker compose` from inside `infrastructure/` |
| `services/<service>/Dockerfile` | Individual service image build |
| `services/<service>/app/main.py` | FastAPI entry point |
| `services/<service>/app/api/v1/...` | Business routes |
| `services/api-gateway/app/proxy.py` | Route table and reverse-proxy logic |
| `services/<service>/app/messaging/...` | RabbitMQ connection, publisher, subscriber per service |
| `migrations/master/...` | Alembic for global DB |
| `migrations/tenant/...` | Alembic for per-hospital DBs |

---

## Create Superadmin
venv\Scripts\python.exe scripts/create_superuser.py --username=superadmin12 --password=Nassir_05 --email=admin@hosp1.com --role=super_admin
