# Hospital System — Workflow Reference

## Project Layout

```
hospital_flow/
├── services/
│   ├── api-gateway/              port 8000  — Request routing, JWT verification
│   ├── auth-service/             port 8001  — Authentication (signup, login, password reset)
│   ├── master-service/           port 8002  — Superadmin (tenants, superadmins, roles)
│   ├── admin-service/            port 8018  — Hospital admin (user CRUD within tenant)
│   └── ... other services ...
├── migrations/
│   ├── tenant/                              — Alembic migrations for per-tenant databases
│   └── master/                              — Alembic migrations for master database
└── streamlit_app/                           — Streamlit frontend
```

---

## 0. Database Architecture (Where Data Lives)

This system uses **two physically separate databases**:

### Master Database (`hospital_master`)
This is the **global registry**. It stores:

| Table | Purpose |
|-------|---------|
| `tenants` | Hospital registry with encrypted DB connection strings |
| `super_admins` | Super admin users (not hospital users) |
| `global_audit_logs` | Audit trail |
| `refresh_tokens` | Session refresh tokens |
| `password_reset_tokens` | Password reset tokens |

> **Rule:** The `users` table in `hospital_master` should be **EMPTY** for tenants. All hospital users live in their own tenant databases.

### Tenant Databases (`tenant_hosp-xxx`)
Each hospital gets its **own isolated PostgreSQL database**. It stores:

| Table | Purpose |
|-------|---------|
| `users` | Hospital staff, doctors, nurses, patients |
| `patients` | Patient records |
| `visits` | Visit records |
| `appointments` | Appointments |

### How to verify in pgAdmin / psql

```bash
# List all databases (you'll see both master and tenant DBs)
docker-compose exec postgres-master psql -U postgres -c "\l"

# Master database — only tenant registry and superadmins
docker-compose exec postgres-master psql -U postgres -d hospital_master -c "SELECT tenant_id, name FROM tenants;"

# Master database — users table should be empty for tenants
docker-compose exec postgres-master psql -U postgres -d hospital_master -c "SELECT COUNT(*) FROM users WHERE hospital_id LIKE 'hosp-%';"
# Expected: 0

# Tenant database — hospital admin and staff live here
docker-compose exec postgres-master psql -U postgres -d tenant_hosp-xxx -c "SELECT username, role, hospital_id FROM users;"
```

> **Important:** In pgAdmin, you must connect to `tenant_hosp-xxx` as a separate database — tenant data is NOT inside `hospital_master`.

---

## 1. Tenant Database Creation

**Entry point:** `services/auth-service/app/api/v1/auth/router.py` (signup) OR `services/master-service/app/api/v1/superadmin/router.py` (superadmin)
**Function:** `signup()` (line 46) or `create_tenant()` (line 171)

**Provisioning logic:** `services/auth-service/app/services/provision.py` or `services/master-service/app/services/provision.py`
**Function:** `provision_tenant_database_sync(tenant_id, name)` (line 105)

### What it does

| Step | Description | Database | Code location |
|------|-------------|----------|---------------|
| 1 | Generate tenant ID (e.g. `hosp-a1b2c3d4`) | — | `router.py:50` |
| 2 | Insert `Tenant` row into master database | **Master** | `router.py:57-68` |
| 3 | Create Keycloak user with `hospital_admin` role | — | `keycloak_admin.py:36` |
| 4 | Set `tenant_id` attribute on Keycloak user | — | `keycloak_admin.py` — `set_user_attribute()` |
| 5 | **Create PostgreSQL database** → run Alembic migrations → encrypt DSN | **New DB** | `provision.py:105` — `provision_tenant_database_sync()` |
| 6 | Update tenant record with real encrypted DSN | **Master** | `provision.py:94` — `_update_tenant_record()` |
| 7 | Store admin user in tenant database (NOT master) | **Tenant** | `router.py:97-110` |
| 8 | Return JWT tokens / TenantOut | — | `router.py` |

### Inside `provision_tenant_database_sync()` (`provision.py:105`)

```python
def provision_tenant_database_sync(tenant_id: str, name: str) -> str:
```

1. Connect to PostgreSQL using `DB_ADMIN_URL` (admin user with CREATEDB privilege)
2. `CREATE DATABASE tenant_{tenant_id} WITH OWNER postgres ENCODING UTF8`
3. Build DSN: `postgresql://postgres:postgres@postgres-master:5432/tenant_hosp-a1b2c3d4`
4. Run `alembic upgrade head` from `migrations/tenant/` — creates tables: `users`, `patients`, `visits`, `appointments`
5. Encrypt the DSN with Fernet using `TENANT_DB_ENCRYPTION_KEY`
6. Update `tenants.db_dsn_encrypted` in master database

**Contract:** This function must succeed. There is no fallback to the master database. If provisioning fails, the endpoint returns HTTP 500 and the tenant record is rolled back.

### Key files for provisioning

| File | Purpose |
|------|---------|
| `services/auth-service/app/services/provision.py` | Sync provisioning (called from signup endpoint) |
| `services/master-service/app/services/provision.py` | Sync provisioning (called from superadmin endpoint) + async (called from RabbitMQ consumer) |
| `migrations/tenant/versions/0001_initial_tenant_schema.py` | Tenant database schema definition |
| `migrations/tenant/alembic.ini` | Alembic configuration (`script_location=%(here)s`) |
| `migrations/tenant/env.py` | Alembic environment (no `fileConfig`) |

### Inside `provision_tenant_database_sync()` (`provision.py:53`)

```python
def provision_tenant_database_sync(tenant_id: str, name: str) -> str:
```

1. Connect to PostgreSQL using `DB_ADMIN_URL` (admin user with CREATEDB privilege)
2. `CREATE DATABASE tenant_{tenant_id} WITH OWNER postgres ENCODING UTF8`
3. Build DSN: `postgresql://postgres:nasr@localhost:5432/tenant_hosp-a1b2c3d4`
4. Run `alembic upgrade head` from `migrations/tenant/` — creates tables: `users`, `patients`, `visits`, `appointments`
5. Encrypt the DSN with Fernet using `TENANT_DB_ENCRYPTION_KEY`
6. Update `tenants.db_dsn_encrypted` in master database

**Contract:** This function must succeed. There is no fallback to the master database. If provisioning fails, the signup endpoint returns HTTP 500 and the tenant record is rolled back.

### Key files for provisioning

| File | Purpose |
|------|---------|
| `services/auth-service/app/services/provision.py` | Sync provisioning (called from signup endpoint) |
| `services/master-service/app/services/provision.py` | Async provisioning (called from RabbitMQ consumer) |
| `migrations/tenant/versions/0001_initial_tenant_schema.py` | Tenant database schema definition |
| `migrations/tenant/alembic.ini` | Alembic configuration (`script_location=%(here)s`) |
| `migrations/tenant/env.py` | Alembic environment (no `fileConfig`) |

---

## 2. Authentication Flow

**Entry point:** `services/auth-service/app/api/v1/auth/router.py`
**Endpoint:** `POST /login` → function `login()` (line 121)

### Process

1. Client sends `username` + `password`
2. `services/auth-service/app/services/auth.py` — `login()` sends credentials to Keycloak token endpoint
3. Keycloak returns JWT containing `tenant_id`, `roles`, `sub`, `email`
4. JWT is returned to client

### Token validation (on every protected request)

**File:** `services/auth-service/app/core/tenant_auth.py`
**Function:** `get_current_tenant()` (line 91)

1. Extract `Authorization: Bearer <token>` header
2. Fetch Keycloak JWKS (`/protocol/openid-connect/certs`) — cached for 5 minutes
3. Verify token signature (RS256 via JWKS, or HS256 for internal superadmin tokens)
4. Extract `tenant_id` from JWT claims
5. Check tenant suspension status from `tenants` table
6. Attach `TenantContext` to `request.state.tenant`

---

## 3. Database Routing

When a hospital admin endpoint is called, requests must route to the correct tenant database.

**File:** `services/admin-service/app/dependencies.py`
**Function:** `get_tenant_db_for_request(request)` (line 30)

### Resolution chain

```
request.state.tenant.tenant_id (e.g. "hosp-a1b2c3d4")
  ↓
services/admin-service/app/core/database.py — TenantDatabaseRouter.get_session(hospital_id)
  ↓
Query: SELECT db_dsn_encrypted FROM tenants WHERE tenant_id = :tid AND is_active = true
  ↓
Decrypt DSN with Fernet (TENANT_DB_ENCRYPTION_KEY)
  ↓
Create SQLAlchemy engine + session for tenant_{tenant_id}
  ↓
Cache engine in _tenant_engine_cache dict (avoids repeated lookups)
```

**File:** `services/admin-service/app/core/database.py`
**Class:** `TenantDatabaseRouter` — implements `DatabaseRouter` ABC

**For synchronous operations:**
**File:** `services/admin-service/app/db/tenant_sync.py`
**Function:** `get_tenant_db_sync(tenant_id)` (line 40)

---

## 4. Role-Based Access Control

Roles are stored in Keycloak and appear in JWT under `realm_access.roles`. The local `role` field in PostgreSQL is a read-optimized cache only.

### Role hierarchy

```
super_admin  →  hospital_admin  →  nurse / clinician / doctor / patient  →  hospital_user
```

| Role | Scope | Accessible databases |
|------|-------|---------------------|
| `super_admin` | Global tenant/superadmin management | Master DB (`tenants`, `super_admins`) |
| `hospital_admin` | User CRUD within own hospital | Own tenant database |
| `hospital_user` / `nurse` / `clinician` / `doctor` / `patient` | Operational roles | Own tenant database |

### Dependency enforcement

**File:** `services/master-service/app/api/v1/superadmin/deps.py`
**Function:** `require_role(["super_admin"])`

**File:** `services/admin-service/app/api/v1/admin/deps.py`
**Function:** `require_role(["hospital_admin", ...])`

---

## 5. Superadmin Tenant Operations

**Entry point:** `services/master-service/app/api/v1/superadmin/router.py`
**Endpoint:** `POST /tenants` → function `create_tenant()` (line 50)

Similar to signup, but:
- Superadmin specifies `admin_username` and `admin_password` optionally
- Tenant database is provisioned synchronously
- User is stored in tenant database, not master

**Other superadmin endpoints:**

| Endpoint | Function | Purpose |
|----------|----------|---------|
| `GET /tenants` | `list_tenants()` | List all tenants |
| `PUT /tenants/{id}` | `update_tenant()` | Activate / suspend tenant |
| `POST /users` | `create_superadmin()` | Create superadmin user |
| `GET /users` | `list_superadmins()` | List superadmin users |
| `POST /roles` | `create_role()` | Create new Keycloak realm role |

---

## 6. Hospital Admin User CRUD

**Entry point:** `services/admin-service/app/api/v1/admin/router.py`

All endpoints use `get_tenant_db_for_request()` to get a session connected to the caller's tenant database.

| Endpoint | Function | Line |
|----------|----------|------|
| `GET /users` | `list_users()` | 40 |
| `POST /users` | `create_user()` | 60 |
| `GET /users/{id}` | `get_user()` | 100 |
| `PUT /users/{id}` | `update_user()` | 120 |
| `DELETE /users/{id}` | `delete_user()` | 160 |

Each endpoint:
1. Validates JWT and extracts `tenant_id`
2. Routes to tenant database via `TenantDatabaseRouter`
3. Performs operation on tenant-specific tables

---

## 7. Event Bus (RabbitMQ)

Each service has its own self-contained `app/messaging/` module:

**Connection:** `services/<service>/app/messaging/connection.py` — `get_rabbitmq_connection()`
**Publisher:** `services/<service>/app/messaging/publisher.py` — `publish_event(routing_key, payload)`
**Subscriber:** `services/<service>/app/messaging/subscriber.py` — `start_consumer(queue_name, bindings)`

### Events

| Event | Publisher | Subscriber | Purpose |
|-------|-----------|------------|---------|
| `tenant.created` | auth-service `events/publisher.py:13` | master-service `events/subscriber.py:18` | Trigger async provisioning |
| `tenant.provisioned` | master-service | downstream services | Notify tenant is ready |
| `tenant.suspended` | master-service | all services | Suspend tenant access |

### Consumer lifecycle

Each service starts a consumer in its FastAPI lifespan:

```python
# services/master-service/app/main.py
@asynccontextmanager
async def lifespan(app: FastAPI):
    consumer_task = asyncio.create_task(start_consumer(...))
    yield
    consumer_task.cancel()
```

---

## 8. Important Rules

1. **`tenant_id` comes from the JWT, never from client input.** Accepting `tenant_id` from request bodies is a security risk — a hospital admin could impersonate another hospital.

2. **Tenant users belong in tenant databases, not the master database.** The master database holds `tenants`, `super_admins`, and `global_audit_log` only. If a tenant database cannot be created, the operation fails — there is no silent fallback.

3. **Keycloak is the authoritative source for roles.** The `role` column in the local `users` table is a read-optimization copy. All role checks must ultimately reference Keycloak realm roles from the JWT.

4. **Superadmins must not access tenant data.** They operate on `tenants` and `super_admins` tables in the master database only.

5. **Database engines are cached per tenant.** `TenantDatabaseRouter` caches SQLAlchemy engines in `_tenant_engine_cache` to avoid repeated DSN decryption and connection pool creation.

6. **Each tenant gets its own PostgreSQL database.** Do not look for tenant data inside `hospital_master` — connect to `tenant_hosp-xxx` instead. The `tenants` table in `hospital_master` only stores the registry + encrypted connection string, never the actual patient/staff data.
