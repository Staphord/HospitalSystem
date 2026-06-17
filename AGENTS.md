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

### Key Files
- `services/auth-service/app/api/v1/auth/router.py` — All auth endpoints
- `services/auth-service/app/events/publisher.py` — Publishes tenant.created event on signup
- `services/master-service/app/api/v1/superadmin/router.py` — Superadmin endpoints
- `services/master-service/app/services/provision.py` — Automatic tenant database provisioning
- `services/master-service/app/events/subscriber.py` — Consumes tenant.created event and triggers provisioning
- `services/admin-service/app/api/v1/admin/router.py` — Hospital admin endpoints
- `services/api-gateway/app/proxy.py` — Route table and reverse-proxy logic
- `services/api-gateway/app/tenant.py` — Tenant DB URL resolution with Redis caching
- `streamlit_app/app.py` — Streamlit frontend

### Test Users
- Super Admin: `superadmin` / `superadmin123`
- Hospital Admin: `hadmin1` / `admin12345`
- Hospital User: `staff1` / `staff1234`

---

## Context Summary (Session 2)

### Goal
Restructured the monolith into a 14-microservice architecture as defined in `microservicearchitecture.md`.

### Repository Structure
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
└── streamlit_app/                # Streamlit frontend
```

### Architecture Notes
- **API Gateway** reverse-proxies requests to downstream services by path prefix and injects `X-Tenant-DB` header after resolving tenant DB URL from Master DB (cached in Redis).
- **Auth** and **Admin** functionality are fully migrated into dedicated services.
- **RabbitMQ** is fully wired: each service has its own `app/messaging/` module providing `publish_event()` and `start_consumer()`. All `events/publisher.py` and `events/subscriber.py` files call real messaging code. Consumers are started as background tasks in each service's FastAPI lifespan.
- Each service is self-contained with its own `app/` package, `Dockerfile`, and `requirements.txt`.

### Key Files
- `services/api-gateway/app/proxy.py` — Route table and reverse-proxy logic
- `services/api-gateway/app/tenant.py` — Tenant DB URL resolution with Redis caching
- `services/auth-service/app/api/v1/auth/router.py` — All auth endpoints
- `services/auth-service/app/events/publisher.py` — Publishes tenant.created event on signup
- `services/master-service/app/api/v1/superadmin/router.py` — Superadmin endpoints
- `services/master-service/app/services/provision.py` — Automatic tenant database provisioning
- `services/master-service/app/events/subscriber.py` — Consumes tenant.created event and triggers provisioning
- `services/admin-service/app/api/v1/admin/router.py` — Hospital admin endpoints
- `infrastructure/docker-compose.yml` — Full local dev stack

### Automatic Tenant Provisioning
- **Signup flow** (`POST /api/v1/auth/signup`) creates tenant record in Master DB and publishes `tenant.created` event
- **master-service** subscriber consumes the event and:
  1. Creates new PostgreSQL database `tenant_{tenant_id}` via `DB_ADMIN_URL`
  2. Runs `alembic upgrade head` from `migrations/tenant/`
  3. Encrypts DSN with Fernet and updates tenant record
  4. Publishes `tenant.provisioned` event for downstream services
- Requires `DB_ADMIN_URL` env var with CREATEDB privilege and `TENANT_DB_TEMPLATE` for naming new databases

---

## Context Summary (Session 3)

### Goal
Implement secure subscription lifecycle management inside `master-service`.

### Completed Features
- Full SaaS billing schema in `master-service`: `subscription_plans`, `subscriptions`, `invoices`, `saas_payments`, `super_admin_audit_log`, `announcements`, `subscription_audit_log`.
- Extended `Tenant` model with contact, address, timezone, currency, branding, region, trial, billing, and created-by fields.
- Subscription plan catalog server-side (`free_trial`, `basic`, `standard`, `premium`, `enterprise`) with prices, trial days, max users, and feature gates; plans are synced to `subscription_plans` table on startup.
- Annual and monthly billing cycles.
- Upgrade / downgrade plan endpoints with rank validation (cannot downgrade to trial).
- Subscription renewal extending term from current end date.
- One-time free trial per tenant (`has_used_trial` flag prevents abuse).
- Manual tenant account activation, suspension (with required reason + Redis blocklist + Keycloak session revocation), and reactivation (only if not cancelled and not past grace period).
- Background suspension job now handles trial expiry and grace periods.
- Master DB migrations via Alembic (`migrations/master/versions/0002_add_subscription_lifecycle.py` and `0003_add_saas_schema.py`), automatically applied on `master-service` startup.
- Comprehensive audit logging to `global_audit_logs` and `subscription_audit_log` for every subscription/account action.
- Unit tests for subscription business rules, made SQLite-compatible by skipping SaaS-table writes when those tables are absent.
- Added `services/master-service/README.md` documenting architecture, schema, and endpoints.

### New Key Endpoints (all require `super_admin`)
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/superadmin/plans` | List canonical subscription plans |
| GET | `/api/v1/superadmin/tenants/{tenant_id}/subscription` | Get subscription state |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/subscribe` | Start/replace subscription (optional trial) |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/upgrade` | Upgrade to higher plan |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/downgrade` | Downgrade to lower plan |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/renew` | Renew subscription |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/activate` | Activate hospital account |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/suspend` | Suspend hospital account |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/reactivate` | Reactivate hospital account |

### New Key Files
- `services/master-service/app/services/subscription_plans.py` — Plan catalog and ranking utilities
- `services/master-service/app/services/subscription_service.py` — Authoritative subscription state machine
- `services/master-service/app/models/saas.py` — SaaS billing, subscription, audit, and announcement models
- `services/master-service/tests/unit/test_subscription_service.py` — Unit tests
- `services/master-service/README.md` — Service documentation
- `migrations/master/versions/0002_add_subscription_lifecycle.py` — Subscription lifecycle migration
- `migrations/master/versions/0003_add_saas_schema.py` — SaaS schema migration

### Updated Files
- `services/master-service/app/models/master.py` — Added subscription lifecycle columns to `Tenant`
- `services/master-service/app/api/v1/superadmin/router.py` — Added subscription endpoints
- `services/master-service/app/api/v1/superadmin/schemas.py` — Added subscription request/response schemas
- `services/master-service/app/services/suspension_job.py` — Trial expiry handling
- `services/master-service/app/services/tenant_service.py` — Grace-period aware expiry checks
- `services/master-service/app/main.py` — Automatic Alembic migration on startup
- `infrastructure/docker-compose.yml` — Fixed build contexts and migrations volume paths
- `migrations/master/alembic.ini` — Fixed logging configuration

---

## Context Summary (Session 4)

### Goal
Extend the Hospital Flow Streamlit portal with tenant subscription visibility, user suspension/resume, tenant termination, and suspended-tenant lockout; fix auth/signup breakage caused by subscription schema drift.

### Completed Features
- Added `terminate_tenant` endpoint: `POST /api/v1/superadmin/tenants/{tenant_id}/terminate` (irreversible, revokes Keycloak sessions, Redis blocklist).
- Added `terminated_at` and `termination_reason` columns to `Tenant` model and `TERMINATED` to `SubscriptionStatus`.
- Added user `is_active` support in `admin-service` model, schemas, and update endpoint, synced to Keycloak `enabled` flag.
- Added tenant `users.is_active` column via tenant migration `0002_add_user_is_active.py`.
- Added tenant self-service subscription endpoint `GET /api/v1/tenant/subscription` in `master-service`, exposed through the API gateway.
- Updated Streamlit app with:
  - User suspend/resume toggle and active status in the user list.
  - Tenant terminate button in superadmin tenant management.
  - New "Subscription" page for hospital admins showing plan, status, feature gates, and suspension details.
  - Suspended/terminated tenant lockout screen with logout action.
- Enforced tenant suspension lockout at login and refresh in `auth-service` (`TENANT_SUSPENDED` 403).
- Aligned `auth-service` `Tenant` model with `master-service` migrations to fix signup failures (`subscription_status`, `has_used_trial`, `auto_renew`, contact/address fields).
- Rebuilt and verified all modified backend services; signup, login, user CRUD, tenant suspension/termination, and subscription self-service all pass manual API tests.

### New Key Endpoints
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/v1/superadmin/tenants/{tenant_id}/terminate` | `super_admin` | Permanently terminate a tenant |
| GET | `/api/v1/tenant/subscription` | Any authenticated tenant user | Read own tenant subscription state |

### New Key Files
- `services/master-service/app/api/v1/tenant/router.py` — Tenant self-service subscription endpoint
- `migrations/master/versions/0005_add_termination_columns.py` — Termination columns migration
- `migrations/tenant/versions/0002_add_user_is_active.py` — Tenant user `is_active` migration

### Updated Files
- `services/auth-service/app/models/master.py` — Aligned tenant columns with master-service migrations
- `services/auth-service/app/api/v1/auth/router.py` — Login/refresh tenant suspension lockout; superadmin login now syncs local `super_admins` record from Keycloak
- `services/auth-service/app/services/tenant_service.py` — DB fallback for `is_tenant_suspended` when Redis key is missing/expired
- `services/master-service/app/api/v1/router.py` — Registered tenant self-service router
- `services/master-service/app/api/v1/superadmin/router.py` — Added terminate, invoice, SaaS payment, and super admin audit log endpoints
- `services/master-service/app/api/v1/superadmin/schemas.py` — Added `TenantTerminateRequest`, `InvoiceCreate`, `InvoiceUpdate`, `SaaSPaymentCreate`, `is_trial`, `TerminationSnapshot`
- `services/master-service/app/services/subscription_service.py` — `terminate_tenant` logic; added `is_trial` to subscription state
- `services/master-service/app/services/subscription_plans.py` — `TERMINATED` status
- `services/api-gateway/app/tenant.py` — DB fallback for `is_tenant_suspended` when Redis key is missing/expired
- `services/admin-service/app/models/user.py` — `is_active` column
- `services/admin-service/app/api/v1/admin/schemas.py` — `is_active` in update/output
- `services/admin-service/app/api/v1/admin/router.py` — `is_active` → Keycloak sync
- `services/admin-service/app/services/keycloak_admin.py` — `is_active` handling
- `services/api-gateway/app/proxy.py` — Route `/api/v1/tenant` to master-service
- `streamlit_app/app.py` — User suspend/resume, terminate button, subscription page, lockout screen, user-list error feedback, proactive subscription status check on dashboard load
- `scripts/migrate_existing_tenants.py` — Utility to run tenant migrations on all existing tenant DBs
