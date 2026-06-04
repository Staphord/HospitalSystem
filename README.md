# Hospital Flow — FastAPI Backend

Multi-tenant hospital patient flow system with Keycloak OIDC authentication.

---

## Quick Start (Docker)

```powershell
# 1. Start everything (PostgreSQL + Redis + Keycloak + App)
docker-compose up -d --build

# 2. Set up Keycloak realm, roles, and test users
venv\Scripts\python.exe setup_keycloak.py

# 3. Create local database tables
venv\Scripts\python.exe scripts\init_db.py

# App is already running on http://localhost:8000
```

## Quick Start (Local — no Docker, just PostgreSQL + Keycloak)

Run Keycloak and PostgreSQL however you prefer, then:

```powershell
# 1. Install dependencies
pip install -r requirements.txt

# 2. Set up Keycloak realm, roles, OIDC client, and test users
venv\Scripts\python.exe setup_keycloak.py

# 3. Create local database tables
venv\Scripts\python.exe scripts\init_db.py

# 4. Start FastAPI
uvicorn app.main:app --reload
```

---

## Authentication API

All auth endpoints are under `/api/v1/auth/`.

### Login

```
POST /api/v1/auth/login
{
  "username": "testuser",
  "password": "testpassword"
}
```

Returns `access_token`, `refresh_token`, `expires_in`, `session_id`.

### Refresh Token

```
POST /api/v1/auth/refresh
{
  "refresh_token": "<refresh_token>"
}
```

Returns a new token pair. The old refresh token is revoked (rotation).

### Logout

```
POST /api/v1/auth/logout
Authorization: Bearer <access_token>
{
  "refresh_token": "<refresh_token>"
}
```

Revokes the refresh token server-side and invalidates the Keycloak session.

### Logout All Sessions

```
POST /api/v1/auth/logout-all
Authorization: Bearer <access_token>
```

Revokes every active session for the authenticated user.

### Password Reset

```
POST /api/v1/auth/password-reset
{ "email": "user@hospital.com" }

POST /api/v1/auth/password-reset/confirm
{ "token": "<reset_token>", "new_password": "newSecurePass123" }
```

### Multi-Factor Auth (TOTP)

```
POST /api/v1/auth/mfa/setup
Authorization: Bearer <access_token>
→ { "secret": "...", "qr_code_url": "otpauth://..." }

POST /api/v1/auth/mfa/verify
Authorization: Bearer <access_token>
{ "totp_code": "123456" }
```

---

## Testing with Postman

### 1. Login — get a token

```
POST http://localhost:8000/api/v1/auth/login
Body → raw → JSON:

{
  "username": "testuser",
  "password": "testpassword"
}
```

**Response** (200):
```json
{
  "access_token": "eyJhbG...",
  "refresh_token": "eyJhbG...",
  "expires_in": 300,
  "refresh_expires_in": 1800,
  "token_type": "Bearer",
  "session_id": "..."
}
```

### 2. Call a protected endpoint

```
GET http://localhost:8000/api/v1/me
Authorization → Bearer Token → paste the access_token
```

**Response** (200):
```json
{
  "sub": "6a62813b-...",
  "preferred_username": "testuser",
  "email": "testuser@example.com",
  "roles": ["hospital_user"],
  "hospital_id": null
}
```

### 3. Refresh an expired token

```
POST http://localhost:8000/api/v1/auth/refresh
Body → raw → JSON:

{
  "refresh_token": "eyJhbG..."
}
```

### 4. Logout

```
POST http://localhost:8000/api/v1/auth/logout
Authorization → Bearer Token → paste access_token
Body → raw → JSON:

{
  "refresh_token": "eyJhbG..."
}
```
→ `204 No Content`

### 5. Postman environment variables (recommended)

Create a Postman collection with these variables:
- `base_url`: `http://localhost:8000`
- `access_token`: (set from Login response script)
- `refresh_token`: (set from Login response script)

**Test script for Login** (under Scripts → Post-response):

```javascript
const json = pm.response.json();
pm.collectionVariables.set("access_token", json.access_token);
pm.collectionVariables.set("refresh_token", json.refresh_token);
```

---

## Streamlit UI (Login Dashboard)

```powershell
# Terminal 1: FastAPI backend
uvicorn app.main:app --reload

# Terminal 2: Streamlit frontend
streamlit run streamlit_app\app.py
```

Opens at `http://localhost:8501`. Login form calls the FastAPI auth endpoint and shows your profile on success.

---

## User Management CLI

### Creating Users

```powershell
# Super admin (access to all hospitals, bypasses hospital_id check)
venv\Scripts\python.exe scripts\create_superuser.py --username=superadmin --password=Nassir_05 --role=super_admin

# Hospital admin (scoped to one hospital)
venv\Scripts\python.exe scripts\create_superuser.py --username=hospitaladmin --password=Nassir_05 --email=hospitaladmin@hospital.com --role=hospital_admin --hospital-id=hosp-001

# Regular user
venv\Scripts\python.exe scripts\create_superuser.py --username=nurse1 --password=Nassir_05 --email=nurse1@hospital.com --role=hospital_user --hospital-id=hosp-001
```

### Deleting Users

```powershell
venv\Scripts\python.exe scripts\create_superuser.py --username=superadmin --delete
```


### Test Users

| Username | Password | Role | Hospital |
|----------|----------|------|----------|
| `superadmin` | `Nassir_05` | `super_admin` | ALL |
| `hospitaladmin` | `Nassir_05` | `hospital_admin` | `hosp-001` |
| `nurse1` | `Nassir_05` | `hospital_user` | `hosp-001` |

---

## Project Structure (Auth-related)

| File | Purpose |
|------|---------|
| `app/api/v1/auth/router.py` | Login, refresh, logout, password reset, MFA endpoints |
| `app/api/v1/auth/schemas.py` | Pydantic request/response models |
| `app/services/auth.py` | Keycloak proxy, token storage, MFA, password reset |
| `app/core/security.py` | JWT validation via JWKS, RBAC (`require_role`), hospital lookup |
| `app/models/auth.py` | `RefreshToken`, `PasswordResetToken` ORM models |
| `app/exceptions.py` | Typed HTTP exceptions |
| `app/core/limiter.py` | Per-user rate limiting |
| `app/core/middleware.py` | Audit logging middleware |
| `app/core/tenant.py` | Multi-tenant DB URL resolution |
| `app/db/master.py` | Master DB engine (tenants, subscriptions) |
| `app/db/tenant.py` | Per-hospital dynamic DB engine (cached) |
| `setup_keycloak.py` | One-time Keycloak bootstrap (realm, roles, client, test users) |
| `scripts/create_superuser.py` | CLI to create/delete users in Keycloak + local DB |
| `scripts/init_db.py` | Create all database tables |
| `app/services/keycloak_admin.py` | Reusable Keycloak admin service (create/delete users, assign roles, set passwords) |
| `app/api/v1/superadmin/router.py` | Super admin API: create/delete/list users (syncs Keycloak + DB) |
| `app/api/v1/superadmin/schemas.py` | Pydantic schemas for superadmin user management |
| `app/api/v1/admin/router.py` | Hospital admin API: create users scoped to hospital |
| `app/api/v1/admin/schemas.py` | Pydantic schemas for hospital admin user management |
| `streamlit_app/app.py` | Streamlit login UI |

---

## Security Architecture

| Layer | Implementation |
|-------|---------------|
| **OIDC Provider** | Keycloak 26.6.2 (RS256-signed JWTs) |
| **Token Validation** | JWKS key fetching (5min cache), RS256, audience + issuer + expiry verified |
| **Token Introspection** | On by default (`KEYCLOAK_INTROSPECT=true`) — validates token is still active with Keycloak on every request (60s cache) |
| **RBAC** | Three roles: `hospital_user`, `hospital_admin`, `super_admin`. `super_admin` bypasses all role checks |
| **Rate Limiting** | SlowAPI with per-user keys; 10/min on login, 30/min on other endpoints |
| **Refresh Token Rotation** | Old refresh tokens revoked on each refresh |
| **Password Hashing** | Handled by Keycloak via admin API |
| **MFA** | TOTP via `pyotp` — setup generates QR code, verify validates with ±30s window |
| **Audit Logging** | Every mutating request logged to `audit_logs` table with user, path, status, timing |
| **Security Headers** | `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `HSTS`, `CSP: default-src 'none'` |
| **CORS** | Configurable via `ALLOWED_ORIGINS` env var |
| **Multi-Tenant Isolation** | `resolve_tenant_db_url()` queries Master DB → returns per-hospital connection string; engines cached 1h |

---

## Environment Variables

See [.env.example](.env.example) for all values. Critical settings:

| Variable | Default | Notes |
|----------|---------|-------|
| `KEYCLOAK_URL` | `http://localhost:8080` | Keycloak base URL |
| `KEYCLOAK_INTROSPECT` | `true` | Set `false` in dev to skip introspection |
| `KEYCLOAK_CLIENT_SECRET` | — | Must match Keycloak client secret |
| `SECRET_KEY` | — | 32+ random chars for internal use |
| `DATABASE_URL` | — | PostgreSQL connection string |

---

## User Management API (Super Admin)

Super admin endpoints require `super_admin` role. Access via `Bearer <access_token>`.

### Create User (superadmin)

```
POST /api/v1/superadmin/users
Authorization: Bearer <access_token>
{
  "username": "newuser",
  "password": "Nassir_05",
  "email": "newuser@hospital.com",
  "role": "hospital_user",
  "hospital_id": "hosp-001"
}
```

Creates the user in **both Keycloak and local database**. `role` can be `super_admin`, `hospital_admin`, or `hospital_user`. Omit `hospital_id` for super_admin.

### Delete User (superadmin)

```
DELETE /api/v1/superadmin/users
Authorization: Bearer <access_token>
{
  "username": "newuser"
}
```

### List Users (superadmin)

```
GET /api/v1/superadmin/users
Authorization: Bearer <access_token>
```

---

## User Management API (Hospital Admin)

Hospital admin endpoints require `hospital_admin` role.

### Create Hospital User

```
POST /api/v1/admin/users
Authorization: Bearer <access_token>
{
  "username": "nurse2",
  "password": "Nassir_05",
  "email": "nurse2@hospital.com",
  "role": "hospital_user",
  "hospital_id": "hosp-001"
}
```

`role` can be `hospital_user` or `hospital_admin` (but never `super_admin`).

---

## Tests

```powershell
# Mocked unit tests (no Keycloak needed)
venv\Scripts\python.exe -m pytest app/tests/test_auth.py -k "not integration" -v

# Full integration tests (requires running Keycloak + test users)
venv\Scripts\python.exe -m pytest app/tests/test_auth.py -v
```
