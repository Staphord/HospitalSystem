## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).

## Context Summary (Session 1)

### Goal
Build multi-tenant hospital management system with FastAPI backend (Keycloak OIDC auth, PostgreSQL) and Streamlit frontend with role-based access control.

### Architecture
- **Backend**: FastAPI on port 8000, Keycloak auth, PostgreSQL, SQLAlchemy ORM
- **Frontend**: Streamlit on port 8501
- **Auth**: Keycloak OIDC with password grant, admin API for user CRUD
- **Multi-tenancy**: Each tenant gets a `tenant_id` (hosp-XXXXXXXX), stored in User model + Keycloak user attribute

### Roles (in order of privilege)
`super_admin` → `hospital_admin` → `nurse` / `clinician` / `doctor` / `patient` → `hospital_user`

### Security Rules
- `super_admin`: Creates/manages other superadmins, manages tenants (activate/suspend), creates roles. **Never** sees or operates on tenant internal data.
- `hospital_admin`: Full CRUD on users within their own tenant only. `tenant_id` comes from JWT, never client input.
- `super_admin` role cannot be changed via edit endpoints.
- Local `role` field is a read-optimization copy; Keycloak realm roles remain authoritative.
- Password changes go through Keycloak Admin REST API (`PUT /users/{id}/reset-password`).

### Key Endpoints
| Prefix | Auth | Description |
|--------|------|-------------|
| `/api/v1/admin/users` | `hospital_admin` | CRUD users within tenant |
| `/api/v1/superadmin/users` | `super_admin` | CRUD superadmin users |
| `/api/v1/superadmin/tenants` | `super_admin` | Manage tenants |
| `/api/v1/superadmin/roles` | `super_admin` | Create roles |

### Database Models
- **User**: `id`, `keycloak_sub`, `username`, `full_name`, `email`, `role`, `hospital_id`
- **Tenant**: `id`, `tenant_id`, `name`, `db_dsn_encrypted`, `status`, `subscription_plan`, `is_active`, `created_at`, `updated_at`

### Completed Features
- Swagger UI `/docs` CSP fix (restrictive header in prod only)
- `.env` synced with `.env.example` (SECRET_KEY, PASSWORD_RESET_TOKEN_TTL)
- `pyarrow` installed (fixes Streamlit `st.table()` crash)
- User CRUD with full_name, username, password, email, role editing
- Super admin management (create/edit/delete) with full_name, username, password, email
- Tenant management (activate/suspend)
- Role creation endpoint
- Signup flow with admin_full_name field
- Password reset via Keycloak admin API
- Rate limiting on all endpoints
- README with role hierarchy, security rules, API endpoint tables, test users

### Key Files (Legacy Monolith — preserved in `app/`)
- `app/models/user.py` — User SQLAlchemy model
- `app/core/database.py` — DB engine + auto-migration
- `app/services/keycloak_admin.py` — Keycloak admin API wrapper
- `app/api/v1/admin/router.py` — Hospital admin user CRUD
- `app/api/v1/superadmin/router.py` — Superadmin user/tenant/role management
- `app/api/v1/auth/router.py` — Login/signup/password-reset/logout
- `streamlit_app/app.py` — Streamlit frontend

> **Note:** The `app/` directory is the **legacy monolith** — preserved for reference but no longer running. The active system is in `services/`. Key files for the live architecture are listed in the Session 2 summary below.

### Test Users (from README)
- Super Admin: `superadmin` / `superadmin123`
- Hospital Admin: `hadmin1` / `admin12345`
- Hospital User: `staff1` / `staff1234`

---

## Context Summary (Session 2)

### Goal
Restructured the monolith into a 14-microservice architecture as defined in `microservicearchitecture.md` while preserving all existing working functionality in `app/`.

### New Repository Structure
```
hospital-flow/
├── services/                     # 14 independent microservices
│   ├── api-gateway/              # Port 8000 — JWT verify, tenant resolve, proxy
│   ├── auth-service/             # Port 8001 — login, signup, refresh, MFA, impersonate
│   ├── master-service/           # Port 8002 — superadmin portal, tenant mgmt
│   ├── reception-service/        # Port 8010
│   ├── triage-service/           # Port 8011
│   ├── consultation-service/     # Port 8012
│   ├── laboratory-service/       # Port 8013
│   ├── radiology-service/        # Port 8014
│   ├── pharmacy-service/         # Port 8015
│   ├── billing-service/          # Port 8016
│   ├── ward-service/             # Port 8017
│   ├── admin-service/            # Port 8018 — hospital admin user CRUD
│   ├── notification-service/     # Port 8019
│   └── report-service/           # Port 8020
├── infrastructure/
│   ├── docker-compose.yml        # Full dev stack (Postgres, Redis, RabbitMQ, all services)
│   ├── docker-compose.test.yml
│   ├── k8s/                      # Deployment + Service + HPA per service
│   └── nginx/
├── migrations/
│   ├── master/                   # Alembic for global DB
│   └── tenant/                   # Alembic for per-hospital DBs
├── shared/
│   └── schemas/
│       ├── common.py             # Shared enums, base response models
│       └── events.py             # RabbitMQ event payloads (15 Pydantic models)
├── scripts/                      # provision_tenant, migrate_tenant, seed_dev, run_all_tests
├── docs/
├── app/                          # ORIGINAL MONOLITH — preserved, untouched
└── streamlit_app/                # ORIGINAL STREAMLIT — preserved, untouched
```

### Architecture Notes
- **API Gateway** reverse-proxies requests to downstream services by path prefix and injects `X-Tenant-DB` header after resolving tenant DB URL from Master DB (cached in Redis).
- **Auth** and **Admin** functionality were fully migrated; existing `app/api/v1/auth/router.py`, `app/api/v1/admin/router.py`, `app/api/v1/superadmin/router.py` remain intact in `app/`.
- **RabbitMQ** is fully wired: `app/messaging/` module in each service provides `publish_event()` and `start_consumer()`. All `events/publisher.py` and `events/subscriber.py` files call real messaging code. Consumers are started as background tasks in each service's FastAPI lifespan.
- Each service is self-contained with its own `app/` package, `Dockerfile`, and `requirements.txt`.
- **No existing files in `app/` or `streamlit_app/` were deleted or modified.**

### Key Files (New)
- `services/api-gateway/app/proxy.py` — Route table and reverse-proxy logic
- `services/api-gateway/app/tenant.py` — Tenant DB URL resolution with Redis caching
- `services/auth-service/app/api/v1/auth/router.py` — All auth endpoints (preserved from monolith)
- `services/auth-service/app/events/publisher.py` — Publishes tenant.created event on signup
- `services/master-service/app/api/v1/superadmin/router.py` — Superadmin endpoints (preserved from monolith)
- `services/master-service/app/services/provision.py` — Automatic tenant database provisioning (CREATE DATABASE + alembic migrations)
- `services/master-service/app/events/subscriber.py` — Consumes tenant.created event and triggers provisioning
- `services/admin-service/app/api/v1/admin/router.py` — Hospital admin endpoints (preserved from monolith)
- `shared/messaging/connection.py`, `publisher.py`, `subscriber.py` — Reusable RabbitMQ helpers
- `infrastructure/docker-compose.yml` — Full local dev stack

### Automatic Tenant Provisioning
- **Signup flow** (`POST /api/v1/auth/signup`) creates tenant record in Master DB and publishes `tenant.created` event
- **master-service** subscriber consumes the event and:
  1. Creates new PostgreSQL database `tenant_{tenant_id}` via `DB_ADMIN_URL`
  2. Runs `alembic upgrade head` from `migrations/tenant/`
  3. Encrypts DSN with Fernet and updates tenant record
  4. Publishes `tenant.provisioned` event for downstream services
- Requires `DB_ADMIN_URL` env var with CREATEDB privilege and `TENANT_DB_TEMPLATE` for naming new databases
