# Hospital Flow — Multi-Tenant Hospital Management System

Multi-tenant hospital patient flow system with Keycloak OIDC authentication, per-tenant database isolation, and role-based access control.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Authentication System](#authentication-system)
   - [How Login Works](#how-login-works)
   - [JWT Token Structure](#jwt-token-structure)
   - [Keycloak Integration](#keycloak-integration)
   - [Token Validation Flow](#token-validation-flow)
   - [Impersonation (Super Admin)](#impersonation-super-admin)
3. [Multi-Tenant System](#multi-tenant-system)
   - [How Tenants Are Registered](#how-tenants-are-registered)
   - [Realm per Hospital](#realm-per-hospital)
   - [Tenant Database Isolation](#tenant-database-isolation)
   - [Tenant Suspension](#tenant-suspension)
4. [User Management](#user-management)
   - [User Storage](#user-storage)
   - [Roles and Permissions](#roles-and-permissions)
   - [User Creation Flow](#user-creation-flow)
5. [API Reference](#api-reference)
   - [Public Endpoints](#public-endpoints)
   - [Protected Endpoints](#protected-endpoints)
   - [Admin Endpoints](#admin-endpoints)
   - [Super Admin Endpoints](#super-admin-endpoints)
6. [Streamlit Frontend](#streamlit-frontend)
7. [Quick Start](#quick-start)
8. [Environment Variables](#environment-variables)
9. [Project Structure](#project-structure)
10. [Security Architecture](#security-architecture)

---

## Architecture Overview

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Streamlit   │     │  FastAPI Backend  │     │    Keycloak      │
│  Frontend    │────▶│  (port 8000)      │────▶│  (port 8080)     │
│  (port 8501) │     │                   │     │  OIDC Provider   │
└──────────────┘     │  ┌─────────────┐  │     │  RS256 JWTs      │
                     │  │ Tenant Auth │  │     └──────────────────┘
                     │  │ Middleware   │  │              │
                     │  └─────────────┘  │     ┌──────────────────┐
                     │         │         │     │  PostgreSQL      │
                     │         ▼         │     │  (Master DB)     │
                     │  ┌─────────────┐  │     │  Tenants, Users, │
                     │  │ PostgreSQL  │  │     │  Audit Logs      │
                     │  │ Master DB   │  │     └──────────────────┘
                     │  └─────────────┘  │              │
                     │         │         │     ┌──────────────────┐
                     │         ▼         │     │  Redis           │
                     │  ┌─────────────┐  │     │  Rate Limiting,  │
                     │  │ Redis Cache │  │     │  Suspension      │
                     │  └─────────────┘  │     └──────────────────┘
                     └──────────────────┘
```

### Key Components

| Component | Role |
|-----------|------|
| **FastAPI** | REST API backend — handles auth, user management, tenant operations |
| **Keycloak 26** | OIDC identity provider — issues RS256 JWTs, manages users & roles |
| **PostgreSQL (Master DB)** | Global data — tenants, user mappings, refresh tokens, audit logs |
| **PostgreSQL (Per-Tenant DBs)** | Isolated databases for each hospital's clinical data |
| **Redis** | Caching — rate limiting, tenant suspension blocklist |
| **Streamlit** | Frontend — login, signup, hospital admin dashboard |

---

## Authentication System

### How Login Works

The system uses the **OAuth 2.0 Password Grant** flow (also called Direct Access Grant). No browser redirect — the client sends credentials directly to the backend.

```
Client                     FastAPI                          Keycloak
  │                          │                                │
  │  POST /api/v1/auth/login │                                │
  │  {username, password}    │                                │
  │ ───────────────────────► │                                │
  │                          │  POST /realms/{realm}/token    │
  │                          │  grant_type=password           │
  │                          │ ──────────────────────────────►│
  │                          │                                │
  │                          │  ◄──── access_token (RS256) ───│
  │                          │  ◄──── refresh_token ──────────│
  │                          │                                │
  │                          │  Store refresh_token hash      │
  │                          │  in local `refresh_tokens`     │
  │                          │  table                         │
  │                          │                                │
  │  ◄── TokenResponse ─────│                                │
  │      {access_token,      │                                │
  │       refresh_token,     │                                │
  │       expires_in,        │                                │
  │       tenant_id}         │                                │
```

**Login flow step by step:**

1. Client sends `username` + `password` to `POST /api/v1/auth/login`
2. Backend forwards credentials to Keycloak's token endpoint for the tenant's realm
3. Keycloak validates credentials, checks MFA requirements, returns RS256 JWT
4. Backend stores the refresh token (SHA-256 hashed) in the local DB for rotation
5. Backend logs a `LOGIN` event to the `global_audit_logs` table
6. Response includes `access_token`, `refresh_token`, `expires_in`, `scope`, `tenant_id`

> **Super Admin Login**: Super admins login without a `tenant_id`. The backend uses the default `hospital-realm` (the global management realm). Their JWT has no `tenant_id` claim and includes the `super_admin` role.

> **Hospital Admin/User Login**: These users login with their hospital's realm (e.g., `hosp-a1b2c3d4`). The `tenant_id` claim is embedded in their JWT via a protocol mapper.

### JWT Token Structure

**Regular hospital user token (decoded):**
```json
{
  "exp": 1780567983,
  "iat": 1780567683,
  "iss": "http://127.0.0.1:8080/realms/hosp-a1b2c3d4",
  "sub": "a0f4069b-f124-4fe6-985c-8376e99781a7",
  "typ": "Bearer",
  "azp": "hospital-api",
  "tenant_id": "hosp-001",
  "realm_access": {
    "roles": [
      "default-roles-hosp-a1b2c3d4",
      "hospital_user",
      "hospital_admin",
      "offline_access"
    ]
  },
  "scope": "profile email",
  "email": "admin@hospital.com",
  "preferred_username": "hospitaladmin"
}
```

**Key claims:**

| Claim | Source | Description |
|-------|--------|-------------|
| `iss` | Keycloak | Issuer URL — includes the realm name |
| `sub` | Keycloak | Unique user ID (UUID) |
| `tenant_id` | Protocol Mapper | Mapped from user attribute `tenant_id` → JWT claim. Null for super admins |
| `realm_access.roles` | Keycloak | List of realm roles — determines permissions |
| `scope` | Keycloak | OIDC scopes. On impersonation tokens this becomes `"readonly"` |

### Keycloak Integration

Keycloak is configured with:

1. **Realm per Hospital** — Each hospital gets its own realm when it registers. The realm name follows the pattern `hosp-{uuid[:8]}` (e.g., `hosp-a1b2c3d4`).

2. **Client `hospital-api`** — Each realm has an OIDC client named `hospital-api` with:
   - `directAccessGrantsEnabled: true` (password grant)
   - `serviceAccountsEnabled: true` (for Keycloak admin API)
   - `standardFlowEnabled: false` (no browser redirect)

3. **Realm Roles** — Each realm has these roles:
   - `hospital_user` — Basic access to hospital-scoped resources
   - `hospital_admin` — Can manage users within their hospital

4. **Protocol Mapper `tenant-id-mapper`** — Maps the user's `tenant_id` attribute to a JWT claim. Configured as:
   - `protocolMapper: oidc-usermodel-attribute-mapper`
   - `user.attribute: tenant_id`
   - `claim.name: tenant_id`
   - `access.token.claim: true`

5. **User Profile** — The `tenant_id` attribute is declared in the realm's User Profile configuration so Keycloak 26 allows it to be set. Without this, Keycloak silently ignores custom attributes.

6. **Direct Grant Flow** — Non-interactive authentication flow. The conditional OTP subflow is disabled (OTP is not required for password grant).

### Token Validation Flow

Every protected endpoint goes through two layers of validation:

**Layer 1: `get_current_active_user()`** (in `app/core/security.py`)
- Extracts Bearer token from `Authorization` header
- Decodes JWT header to get `kid` (key ID)
- If `kid == "impersonation-key"`: validates as HS256 using `SECRET_KEY`
- Otherwise: fetches JWKS from correct Keycloak realm, decodes with matching RSA key
- Verifies: `exp` (not expired), `iss` (trusted issuer)
- Returns `TokenPayload` dataclass

**Layer 2: `get_current_tenant()`** (in `app/core/tenant_auth.py`)  
- Extracts `tenant_id` from JWT payload
- If `is_super_admin` is True: allows access without tenant_id
- If NOT super admin and no tenant_id: rejects with 403
- Checks Redis blocklist for tenant suspension status
- Returns `TenantContext` dataclass (available via `request.state.tenant`)

**JWKS Caching:** The JWKS (JSON Web Key Set) is fetched from Keycloak and cached for 5 minutes using `cachetools.TTLCache`.

### Impersonation (Super Admin)

Super admins can generate **impersonation tokens** to view data within a tenant without switching accounts:

```
POST /api/v1/auth/impersonate
Authorization: Bearer <super_admin_token>
{ "target_tenant_id": "hosp-001" }
```

**How impersonation tokens work:**

1. Super admin obtains a regular JWT via login
2. Calls `/impersonate` with target `tenant_id`
3. Backend creates a **HS256-signed JWT** (signed with the app's `SECRET_KEY`, NOT Keycloak's private key — which is inaccessible)
4. The impersonation token has `kid: "impersonation-key"` so `get_current_tenant()` knows to validate with HS256 instead of RS256
5. Token payload includes:
   - `tenant_id` — the target hospital
   - `scope: "readonly"` — marks this as read-only access
   - `impersonator: true` — identifies this as an impersonation
   - `is_super_admin: true` — preserves super admin identity
6. `ReadOnlyScopeMiddleware` blocks all POST/PUT/PATCH/DELETE requests when `scope == "readonly"`
7. `ImpersonationBannerMiddleware` adds `X-Impersonation-Banner: true` response header
8. TTL: 900 seconds (configurable via `IMPERSONATION_TOKEN_TTL`)

---

## Multi-Tenant System

### How Tenants Are Registered

Hospitals register themselves via the self-service signup endpoint:

```
POST /api/v1/auth/signup
{
  "hospital_name": "City General Hospital",
  "admin_username": "hospitaladmin",
  "admin_password": "SecurePass123!",
  "admin_email": "admin@citygeneral.com"
}
```

**Signup flow step by step:**

1. **Generate tenant ID** — `hosp-{uuid4.hex[:8]}` (e.g., `hosp-a1b2c3d4`)
2. **Create Keycloak realm** — A new realm named `hosp-{tenant_id_short}` is created
3. **Configure realm** — Direct grant flow enabled, user profile configured with `tenant_id` attribute
4. **Create client** — `hospital-api` client created in the new realm with:
   - `directAccessGrantsEnabled: true`
   - `serviceAccountsEnabled: true`
   - Client secret auto-generated
5. **Create roles** — `hospital_user` and `hospital_admin` roles created
6. **Create protocol mapper** — `tenant-id-mapper` maps user `tenant_id` attribute → JWT claim
7. **Create admin user** — Admin user created in Keycloak with `hospital_admin` role and `tenant_id` attribute
8. **Create DB record** — `Tenant` record created in master database with encrypted placeholder DSN
9. **Login** — Admin user is automatically logged in and receives tokens
10. **Response** — Returns `tenant_id`, `hospital_name`, and initial access/refresh tokens

### Realm per Hospital

Each hospital gets its **own Keycloak realm** for full isolation:

| Aspect | Single-Realm | Per-Hospital Realm |
|--------|-------------|-------------------|
| User isolation | Logical (via `tenant_id` attribute) | Complete (different realms can't see each other) |
| JWT issuer | Same for all tenants | Unique per tenant (`iss` claim differs) |
| Key rotation | Affects all tenants | Per-tenant only |
| Login endpoint | Single token endpoint | Resolved per tenant from master DB |
| Complexity | Lower | Higher (dynamic realm resolution) |

The issuer (`iss` claim) in the JWT identifies which realm issued it. The `_decode_token()` function in `tenant_auth.py` reads the `iss` claim to dynamically fetch the correct JWKS for token validation.

### Tenant Database Isolation

Each hospital can have its own PostgreSQL database for clinical data:

1. Tenant DB connection strings are **encrypted at rest** using Fernet symmetric encryption
2. The `TENANT_DB_ENCRYPTION_KEY` environment variable stores the Fernet key
3. Per-tenant async SQLAlchemy engines are cached in a `TTLCache` (64 entries, 1-hour TTL)
4. `get_tenant_session(tenant_id)` decrypts the DSN and creates/returns an async session
5. The `resolve_tenant_db_url()` function queries the master `tenants` table

### Tenant Suspension

A background task (`suspension_loop`) runs every 24 hours to check tenant subscription status:

1. Queries all active tenants where `status != 'suspended'`
2. For each tenant, checks `subscription_end` date
3. If expired 30+ days ago: marks as `suspended` in DB, caches in Redis, revokes all Keycloak sessions
4. The `is_tenant_suspended()` check in `get_current_tenant()` uses Redis (O(1) fast-path)

---

## User Management

### User Storage

Users are stored in **two places**:

**1. Keycloak (Primary)**
- Username, email, password, enabled/disabled state
- Realm roles (`hospital_user`, `hospital_admin`, `super_admin`)
- Custom attributes (`tenant_id`)
- All authentication happens here

**2. Local PostgreSQL Table (`users`)**
- Thin sync table with: `keycloak_sub` (UUID), `username`, `email`, `role`, `hospital_id`
- Used for:
  - Listing users within a hospital (faster than querying Keycloak for every request)
  - Referencing users in clinical data (foreign keys)
  - Audit logging

**Why dual storage?**
- Keycloak is the **identity source of truth** (authentication, passwords, roles)
- The local `users` table is a **read-optimized cache** for listing/scoping users

### Roles and Permissions

| Role | Permissions | Assigned To |
|------|------------|-------------|
| `super_admin` | Full access — create tenants, manage other `super_admin` users. **Cannot see or manage tenant-level users** | Platform administrators |
| `hospital_admin` | Full CRUD on users within **their own hospital only**. Can create users with any hospital role | Hospital IT admins |
| `hospital_user` | Base-level access — view patient data, use clinical modules | General staff |
| `nurse` | Nursing-specific access within their hospital | Nurses |
| `clinician` | Clinician-specific access within their hospital | Clinicians |
| `doctor` | Doctor-specific access within their hospital | Doctors |
| `patient` | Patient-specific access within their hospital | Patients |

### Security Rules (Mandatory)

| Rule | Description |
|------|-------------|
| **Tenant isolation** | Hospital admin can **only** see/manage users within their own tenant. The `tenant_id` comes from the JWT — never from client input |
| **Superadmin scope** | Super admin can **only** create other `super_admin` users and manage tenants. Super admin **cannot** create hospital-level users or bypass tenant boundaries |
| **Role scoping** | Superadmin's `GET /superadmin/users` returns **only** users with `role = super_admin`. Hospital admin's `GET /admin/users` returns **only** users in their hospital |
| **Dynamic role creation** | Superadmin can create new Keycloak realm roles via `POST /superadmin/roles` — these become available for hospital admin to assign |

**Role hierarchy:** `super_admin` bypasses all role checks but is **restricted by code logic** from operating on tenant data. Hospital admin can only manage users in their own tenant.

### User Creation Flow

```
Hospital Admin                    FastAPI                         Keycloak
  │                                 │                              │
  │ POST /api/v1/admin/users        │                              │
  │ {username, password, email,     │                              │
  │  role: "hospital_user"}         │                              │
  │ Authorization: Bearer <token>   │                              │
  │ ───────────────────────────────►│                              │
  │                                 │  GET /admin/realms/{realm}   │
  │                                 │    /roles/{role} (for each)  │
  │                                 │ ────────────────────────────►│
  │                                 │                              │
  │                                 │  POST /admin/realms/{realm}  │
  │                                 │    /users                    │
  │                                 │  {username, email, enabled,  │
  │                                 │   emailVerified, attributes: │
  │                                 │    {tenant_id: ["hosp-001"]}}│
  │                                 │ ────────────────────────────►│
  │                                 │                              │
  │                                 │  PUT /users/{id}             │
  │                                 │    /reset-password           │
  │                                 │ ────────────────────────────►│
  │                                 │                              │
  │                                 │  POST /users/{id}            │
  │                                 │    /role-mappings/realm      │
  │                                 │ ────────────────────────────►│
  │                                 │                              │
  │                                 │  INSERT INTO users           │
  │                                 │  (keycloak_sub, username,    │
  │                                 │   email, role, hospital_id)  │
  │                                 │                              │
  │  ◄── 201 Created ─────────────│                              │
  │      {keycloak_sub, username,  │                              │
  │       email, hospital_id}      │                              │
```

**Step by step:**

1. Hospital admin sends user details + their JWT
2. Backend extracts `tenant_id` from JWT's `TenantContext`
3. `ensure_roles()` creates any missing roles in Keycloak
4. `create_keycloak_user()` creates the user via Keycloak Admin API:
   - POST `/admin/realms/{realm}/users`
   - Sets password via PUT `/users/{id}/reset-password`
   - Assigns roles via POST `/users/{id}/role-mappings/realm`
   - Sets `tenant_id` attribute (merged with full user representation to avoid data loss)
5. `set_user_attribute()` sets `tenant_id` on the Keycloak user
6. `create_local_user()` inserts/updates the local `users` table
7. Returns the created user details

---

## API Reference

### Public Endpoints

| Method | Path | Rate Limit | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/v1/health` | — | Health check |
| `POST` | `/api/v1/auth/login` | 10/min | Login with username + password |
| `POST` | `/api/v1/auth/signup` | 5/min | Register a new hospital |
| `POST` | `/api/v1/auth/refresh` | 20/min | Refresh access token |
| `POST` | `/api/v1/auth/password-reset` | 3/min | Request password reset |
| `POST` | `/api/v1/auth/password-reset/confirm` | 5/min | Confirm password reset |

### Protected Endpoints

| Method | Path | Rate Limit | Auth Required | Description |
|--------|------|-----------|---------------|-------------|
| `GET` | `/api/v1/me` | 30/min | Any | Current user profile |
| `POST` | `/api/v1/auth/logout` | 30/min | Any | Logout (revoke refresh token) |
| `POST` | `/api/v1/auth/logout-all` | 10/min | Any | Revoke all sessions |
| `POST` | `/api/v1/auth/mfa/setup` | 10/min | Any | Generate TOTP secret |
| `POST` | `/api/v1/auth/mfa/verify` | 10/min | Any | Verify TOTP code |
| `POST` | `/api/v1/auth/impersonate` | 10/min | `super_admin` | Impersonate a tenant |

### Admin Endpoints (requires `hospital_admin` role)

| Method | Path | Rate Limit | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/v1/admin/users` | 30/min | List users in your hospital (scoped to JWT tenant_id) |
| `POST` | `/api/v1/admin/users` | 30/min | Create user in your hospital (tenant_id from JWT) |
| `PATCH` | `/api/v1/admin/users/{sub}` | 30/min | Update user email/role in your hospital |
| `DELETE` | `/api/v1/admin/users/{sub}` | 30/min | Delete user from your hospital |

### Super Admin Endpoints (requires `super_admin` role)

| Method | Path | Rate Limit | Description |
|--------|------|-----------|-------------|
| `GET` | `/api/v1/superadmin/users` | 30/min | List **only** `super_admin` users |
| `POST` | `/api/v1/superadmin/users` | 30/min | Create **only** `super_admin` users |
| `PATCH` | `/api/v1/superadmin/users/{sub}` | 30/min | Update any `super_admin` user |
| `DELETE` | `/api/v1/superadmin/users` | 30/min | Delete any user (by username in body) |
| `GET` | `/api/v1/superadmin/tenants` | 30/min | List all tenants |
| `POST` | `/api/v1/superadmin/tenants` | 10/min | Create a new tenant with hospital admin |
| `POST` | `/api/v1/superadmin/roles` | 30/min | Create a new Keycloak realm role |

---

## Streamlit Frontend

The Streamlit app at `streamlit_app/app.py` provides a web UI for:

1. **Login** — Sign in with username + password
2. **Signup** — Register a new hospital (self-service)
3. **Dashboard** — View your profile and manage hospital users
4. **User Management (hospital admin)** — Create/edit/delete users with roles: `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`. All operations scoped to your tenant
5. **User Management (superadmin)** — Read-only listing of `super_admin` users. No tenant access
6. **Tenant Management (superadmin)** — List all tenants and create new hospitals

```powershell
# Terminal 1: FastAPI backend
uvicorn app.main:app --port 8000

# Terminal 2: Streamlit frontend
streamlit run streamlit_app\app.py
```

Opens at `http://localhost:8501`.

---

## Quick Start

### With Docker (recommended)

```powershell
# 1. Start all services
docker-compose up -d --build

# 2. Set up Keycloak (creates initial realm, roles, test users)
venv\Scripts\python.exe setup_keycloak.py

# 3. Create database tables
venv\Scripts\python.exe scripts\init_db.py

# The app runs at http://localhost:8000
```

### Without Docker

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up Keycloak realm
venv\Scripts\python.exe setup_keycloak.py

# 3. Create database tables
venv\Scripts\python.exe scripts\init_db.py

# 4. Start the server
uvicorn app.main:app --port 8000
```

### Streamlit UI

```powershell
streamlit run streamlit_app\app.py
```

---

## Environment Variables

See [.env.example](.env.example) for all values. Critical settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `KEYCLOAK_URL` | `http://localhost:8080` | Keycloak base URL |
| `KEYCLOAK_REALM` | `hospital-realm` | Default super admin realm |
| `KEYCLOAK_CLIENT_SECRET` | — | Must match the client secret in Keycloak |
| `SECRET_KEY` | — | 32+ random chars; used for impersonation token signing |
| `DATABASE_URL` | — | Master PostgreSQL connection string |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis for rate limiting + suspension cache |
| `TENANT_DB_ENCRYPTION_KEY` | — | 32-byte url-safe base64 Fernet key |
| `IMPERSONATION_TOKEN_TTL` | `900` | Impersonation token lifetime (seconds) |
| `SUSPENSION_CHECK_INTERVAL` | `86400` | Background suspension check interval (seconds) |
| `ALLOWED_ORIGINS` | — | CORS origins (comma-separated) |

---

## Project Structure

```
hospital_flow/
├── app/
│   ├── main.py                          # FastAPI app entry point
│   ├── core/
│   │   ├── config.py                    # Pydantic settings (env vars)
│   │   ├── security.py                  # JWT validation, RBAC, hospital ID resolution
│   │   ├── tenant_auth.py               # TenantContext, get_current_tenant dependency
│   │   ├── middleware.py                # AuditLog, ReadOnlyScope, ImpersonationBanner
│   │   ├── database.py                  # Engine init, session management
│   │   ├── limiter.py                   # SlowAPI rate limiter
│   │   └── permissions.py              # Placeholder
│   ├── api/v1/
│   │   ├── router.py                    # Master router — wires all sub-routers
│   │   ├── auth/
│   │   │   ├── router.py                # Login, signup, refresh, logout, MFA, impersonate
│   │   │   └── schemas.py               # Request/response models
│   │   ├── admin/
│   │   │   ├── router.py                # Hospital admin: list/create users
│   │   │   └── schemas.py               # Admin request/response models
│   │   ├── superadmin/
│   │   │   ├── router.py                # Super admin: CRUD users
│   │   │   └── schemas.py
│   │   └── endpoints/
│   │       ├── users.py                 # /me endpoint
│   │       ├── patients.py              # Patient stubs
│   │       └── auth.py                  # Health check
│   ├── services/
│   │   ├── auth.py                      # Keycloak proxy, token storage, MFA, password reset
│   │   ├── keycloak_admin.py            # Keycloak admin API wrapper
│   │   ├── tenant_service.py            # DSN encryption, Redis, subscription checks
│   │   ├── impersonation.py             # HS256 impersonation token creation
│   │   └── suspension_job.py            # Background suspension checker
│   ├── models/
│   │   ├── user.py                      # User ORM (local sync table)
│   │   ├── auth.py                      # RefreshToken, PasswordResetToken
│   │   └── master.py                    # Tenant, GlobalAuditLog
│   └── db/
│       ├── master.py                    # Master DB engine + session
│       ├── tenant.py                    # Per-tenant async engine factory
│       └── base.py                      # SQLAlchemy declarative base
├── streamlit_app/
│   └── app.py                           # Streamlit frontend
├── scripts/
│   ├── create_superuser.py              # CLI to create/delete users
│   ├── init_db.py                       # Create DB tables
│   ├── add_tenant_id_profile.py         # Add tenant_id to Keycloak user profile
│   └── setup_keycloak.py                # Bootstrap Keycloak realm
├── keycloak/
│   └── realm-export.json                # Keycloak realm import template
├── docker-compose.yml                   # 6 services (postgres x3, redis, keycloak, app)
├── Dockerfile
├── requirements.txt
├── .env / .env.example
└── README.md
```

---

## Security Architecture

| Layer | Implementation |
|-------|---------------|
| **OIDC Provider** | Keycloak 26.6.2 — RS256-signed JWTs |
| **Token Validation** | JWKS key fetching (5-min cache), RS256, issuer + expiry verified |
| **Impersonation Tokens** | HS256 signed with `SECRET_KEY`, `kid: "impersonation-key"` distinguishes from KC tokens |
| **RBAC** | Roles: `super_admin`, `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`. Enforced via `require_role()` dependency + tenant-scoped queries |
| **Rate Limiting** | SlowAPI — 5/min on signup, 10/min on login, 30/min on other endpoints |
| **Refresh Token Rotation** | Old tokens revoked on each refresh — prevents replay |
| **Password Hashing** | Handled by Keycloak (bcrypt/PBKDF2) |
| **MFA** | TOTP via `pyotp` — in-memory secret cache |
| **Audit Logging** | All mutating requests logged to `global_audit_logs` table |
| **Tenant Isolation** | Per-tenant databases + JWT `tenant_id` enforcement at middleware level |
| **Suspension** | Redis-based 403 block for suspended tenants; Keycloak session revocation |
| **Security Headers** | HSTS, CSP (`default-src 'none'`), X-Frame-Options, X-Content-Type-Options |
| **CORS** | Configurable via `ALLOWED_ORIGINS` env var |
| **DB Encryption** | Fernet symmetric encryption for per-tenant DSNs at rest |

---

## Keycloak Configuration Notes

### User Profile Requirement

Keycloak 26 has a **User Profile** whitelist for custom attributes. The `tenant_id` attribute must be declared in the realm's User Profile configuration before it can be set on users:

```
PUT /admin/realms/{realm}/users/profile
{
  "attributes": [
    ...standard fields...,
    {
      "name": "tenant_id",
      "displayName": "Tenant ID",
      "permissions": {
        "view": ["admin", "user"],
        "edit": ["admin"]
      },
      "multivalued": false
    }
  ]
}
```

Without this configuration, Keycloak 26 **silently ignores** the `attributes` field during user creation/update.

### Protocol Mapper

The `tenant-id-mapper` protocol mapper on the `hospital-api` client injects the `tenant_id` user attribute into the JWT:

```
Protocol: openid-connect
Mapper type: oidc-usermodel-attribute-mapper
User attribute: tenant_id
Token claim name: tenant_id
Access token: true
ID token: true
Userinfo: true
```

### Client Credentials Grant

Service accounts (`serviceAccountsEnabled: true`) must be enabled on the `hospital-api` client for:
- The `create_user()` function in `keycloak_admin.py` (gets admin tokens)
- The impersonation flow (though we use `SECRET_KEY` HS256 signing now)

---

## Test Users

| Username | Password | Role | Tenant |
|----------|----------|------|--------|
| `superadmin` | `Nassir_05` | `super_admin` | ALL (no tenant_id) |
| `hospitaladmin` | `Nassir_05` | `hospital_admin` | `hosp-001` |
| `nurse2` | `Nassir_05` | `nurse` | `hosp-001` |
| `doctor1` | `Nassir_05` | `doctor` | `hosp-001` |
| `clinician1` | `Nassir_05` | `clinician` | `hosp-001` |

Create test users with:
```powershell
venv\Scripts\python.exe scripts\create_superuser.py --username=superadmin --password=Nassir_05 --role=super_admin
venv\Scripts\python.exe scripts\create_superuser.py --username=hospitaladmin --password=Nassir_05 --email=hospitaladmin@hospital.com --role=hospital_admin --hospital-id=hosp-001
```

---

## Key Technical Decisions

| Decision | Rationale |
|----------|-----------|
| **Single Keycloak realm per hospital** | Full user isolation; each hospital's users/roles/config independent |
| **HS256 for impersonation tokens** | Cannot access Keycloak's RS256 private key; HS256 signed with app's `SECRET_KEY` |
| **Dual user storage (Keycloak + local DB)** | Keycloak for auth, local table for fast scoped queries and foreign keys |
| **Fernet for DSN encryption** | Symmetric, simple, standard; key stored in env var |
| **Redis for suspension cache** | O(1) fast path avoids DB query on every request |
| **TTLCache for per-tenant DB engines** | Avoids reconnecting on every request; 1-hour TTL balances freshness vs reuse |
| **`tenant_id` as user attribute (not role)** | Roles control permissions; attributes control data scoping — separate concerns |
