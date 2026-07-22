# Hospital Flow API Documentation

## Base URL

```
Development: http://localhost:8000/api/v1
Production:  https://<your-domain>/api/v1
```

## Authentication

All protected endpoints require a Bearer token in the `Authorization` header:

```http
Authorization: Bearer <access_token>
```

### Keycloak Realms

The system uses multi-realm Keycloak isolation:
- **Superadmins**: Authenticate against the `master` realm
- **Tenant users**: Authenticate against per-tenant realms named `{tenant_id}` (e.g. `hosp-abc123`)

The API Gateway resolves the correct realm from the JWT `iss` (issuer) claim and fetches JWKS public keys from the appropriate realm's `certs` endpoint.

## Public Endpoints

### Health Check
```http
GET /health
```
Response: `{"status": "ok", "service": "api-gateway"}`

### Login
```http
POST /api/v1/auth/login
Content-Type: application/json

{
  "username": "string",
  "password": "string",
  "realm": "string (optional)"
}
```

- `realm`: Optional Keycloak realm override. If omitted, the service resolves the realm from the user's tenant record. For superadmins, `master` realm is tried first with fallback to the default realm.

Response:
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 300,
  "refresh_expires_in": 1800,
  "token_type": "Bearer",
  "session_id": "uuid",
  "tenant_id": "hosp-xxxxxxxx"
}
```

### Super Admin Login
```http
POST /api/v1/auth/superadmin/login
Content-Type: application/json

{
  "username": "string",
  "password": "string"
}
```

Superadmins authenticate against the Keycloak `master` realm. If authentication fails (e.g. the superadmin user exists in the default realm instead), the service falls back to the default `keycloak_realm`. Token `iss` claim determines which realm is used for JWKS verification downstream.

### Signup (Register New Hospital)
```http
POST /api/v1/auth/signup
Content-Type: application/json

{
  "hospital_name": "string",
  "admin_username": "string",
  "admin_password": "string",
  "admin_email": "user@example.com",
  "admin_full_name": "string"
}
```

Response:
```json
{
  "tenant_id": "hosp-xxxxxxxx",
  "hospital_name": "string",
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "expires_in": 300,
  "refresh_expires_in": 1800,
  "token_type": "Bearer"
}
```

### Refresh Token
```http
POST /api/v1/auth/refresh
Content-Type: application/json

{
  "refresh_token": "eyJ..."
}
```

### Password Reset Request
```http
POST /api/v1/auth/password-reset
Content-Type: application/json

{
  "email": "user@example.com"
}
```

### Password Reset Confirm
```http
POST /api/v1/auth/password-reset/confirm
Content-Type: application/json

{
  "token": "string",
  "new_password": "string"
}
```

## Super Admin Endpoints

All endpoints require `super_admin` role.

### Super Admin Users

#### Create Super Admin
```http
POST /api/v1/superadmin/users
Content-Type: application/json
Authorization: Bearer <token>

{
  "username": "string",
  "password": "string",
  "email": "user@example.com",
  "full_name": "string",
  "role": "super_admin",
  "mfa_secret": ""
}
```

#### List Super Admins
```http
GET /api/v1/superadmin/users
Authorization: Bearer <token>
```

#### Update Super Admin
```http
PATCH /api/v1/superadmin/users/{super_admin_id}
Content-Type: application/json
Authorization: Bearer <token>

{
  "username": "string",
  "email": "user@example.com",
  "full_name": "string",
  "password": "string",
  "role": "super_admin",
  "is_active": true
}
```

#### Delete Super Admin
```http
DELETE /api/v1/superadmin/users
Content-Type: application/json
Authorization: Bearer <token>

{
  "username": "string"
}
```

### Tenant Management

#### List Tenants
```http
GET /api/v1/superadmin/tenants
Authorization: Bearer <token>
```

#### Create Tenant
```http
POST /api/v1/superadmin/tenants
Content-Type: application/json
Authorization: Bearer <token>

{
  "hospital_name": "string",
  "admin_username": "string",
  "admin_password": "string",
  "admin_email": "user@example.com",
  "admin_full_name": "string",
  "subscription_plan": "standard",
  "subscription_billing_cycle": "monthly"
}
```

#### Update Tenant
```http
PATCH /api/v1/superadmin/tenants/{tenant_id}
Content-Type: application/json
Authorization: Bearer <token>

{
  "name": "string",
  "status": "active",
  "is_active": true
}
```

### Subscription Lifecycle

#### Get Subscription State
```http
GET /api/v1/superadmin/tenants/{tenant_id}/subscription
Authorization: Bearer <token>
```

#### Subscribe
```http
POST /api/v1/superadmin/tenants/{tenant_id}/subscribe
Content-Type: application/json
Authorization: Bearer <token>

{
  "plan": "standard",
  "billing_cycle": "monthly",
  "start_trial": false
}
```

#### Upgrade
```http
POST /api/v1/superadmin/tenants/{tenant_id}/upgrade
Content-Type: application/json
Authorization: Bearer <token>

{
  "plan": "premium",
  "billing_cycle": "annual"
}
```

#### Downgrade
```http
POST /api/v1/superadmin/tenants/{tenant_id}/downgrade
Content-Type: application/json
Authorization: Bearer <token>

{
  "plan": "basic",
  "billing_cycle": "monthly",
  "effective_at_end": false
}
```

#### Renew
```http
POST /api/v1/superadmin/tenants/{tenant_id}/renew
Content-Type: application/json
Authorization: Bearer <token>

{
  "billing_cycle": "monthly"
}
```

#### Activate
```http
POST /api/v1/superadmin/tenants/{tenant_id}/activate
Authorization: Bearer <token>
```

#### Suspend
```http
POST /api/v1/superadmin/tenants/{tenant_id}/suspend
Content-Type: application/json
Authorization: Bearer <token>

{
  "reason": "string"
}
```

#### Reactivate
```http
POST /api/v1/superadmin/tenants/{tenant_id}/reactivate
Authorization: Bearer <token>
```

#### Terminate
```http
POST /api/v1/superadmin/tenants/{tenant_id}/terminate
Content-Type: application/json
Authorization: Bearer <token>

{
  "reason": "string"
}
```

### Tenant Usage Statistics

#### Get Tenant Stats
```http
GET /api/v1/superadmin/tenants/{tenant_id}/stats
Authorization: Bearer <token>
```

Returns usage statistics for a specific tenant:

```json
{
  "tenant_id": "hosp-xxxxxxxx",
  "tenant_name": "Hospital Name",
  "user_count": 5,
  "active_user_count": 4,
  "kc_user_count": 5,
  "kc_active_user_count": 4,
  "patient_count": 120,
  "patients_this_month": 15,
  "visit_count": 340,
  "appointment_count": 280,
  "db_size_bytes": 8117271,
  "db_size_mb": 7.74,
  "api_calls_this_month": 1450,
  "subscription_plan": "standard",
  "subscription_status": "active",
  "max_users": 50,
  "usage_pct": 8.0
}
```

Fields:
- `kc_user_count` / `kc_active_user_count`: User counts from the tenant's Keycloak realm
- `db_size_bytes` / `db_size_mb`: Database storage consumed
- `api_calls_this_month`: API call volume from audit logs
- `usage_pct`: Active users as percentage of plan `max_users`

### Subscription Plans

#### List Plans
```http
GET /api/v1/superadmin/plans
Authorization: Bearer <token>
```

## Hospital Admin Endpoints

All endpoints require `hospital_admin` role and operate within the user's tenant.
Tenant ID is always taken from the JWT — never from the request body.

### User Management (FR-53)

#### List Users
```http
GET /api/v1/admin/users?role=&is_active=&department_id=&q=&limit=50&offset=0&sort=username
Authorization: Bearer <token>
```
Response: array of users. Total count in `X-Total-Count` header.

Assignable SRS roles include: `hospital_admin`, `receptionist`, `triage_nurse`, `nurse`, `clinician`, `doctor`, `lab_technician`, `radiographer`, `pharmacist`, `cashier`, `patient`, `hospital_user`.

#### Assignable Roles
```http
GET /api/v1/admin/users/assignable-roles
```

#### Get User
```http
GET /api/v1/admin/users/{keycloak_sub}
```

#### Create User
```http
POST /api/v1/admin/users
Content-Type: application/json
Authorization: Bearer <token>

{
  "username": "string",
  "password": "string",
  "email": "user@example.com",
  "full_name": "string",
  "role": "receptionist",
  "department_id": "uuid-optional",
  "phone": "string-optional"
}
```
New users are created with `force_password_change=true` and a 90-day password expiry.

#### Update User
```http
PATCH /api/v1/admin/users/{keycloak_sub}
```
Role changes **replace** Keycloak realm roles (old roles removed). Deactivating a user revokes Keycloak sessions. Cannot demote/deactivate/delete the last active `hospital_admin`. Cannot assign `super_admin`.

#### Delete User
```http
DELETE /api/v1/admin/users/{keycloak_sub}?hard=false
```
Default is soft-delete (`is_active=false`, `deleted_at` set). `hard=true` removes Keycloak + local row. Self-delete is blocked.

### Permissions (FR-54)

```http
GET /api/v1/admin/permissions
PUT /api/v1/admin/permissions/{role_name}
```
Body for PUT: `{ "modules": ["reception"], "actions": ["create","read"] }`.
System Keycloak roles cannot be deleted/renamed. Tenant roles sync into Keycloak on create.

### Audit Trail (FR-56)

```http
GET /api/v1/admin/audit-logs?user_id=&action=&table_name=&from=&to=&limit=50&offset=0
GET /api/v1/admin/audit-logs/{log_id}
GET /api/v1/admin/audit-logs/export?format=csv|json
```
Writes go to the **tenant** `audit_logs` table (not Master DB).

### System Configuration (FR-55)

```http
GET|PATCH /api/v1/admin/hospital-profile
GET|POST /api/v1/admin/departments
PATCH|DELETE /api/v1/admin/departments/{department_id}
GET|POST /api/v1/admin/fee-schedules
PATCH|DELETE /api/v1/admin/fee-schedules/{fee_id}
GET|POST /api/v1/admin/insurance-providers
PATCH|DELETE /api/v1/admin/insurance-providers/{provider_id}
GET /api/v1/admin/beds
GET /api/v1/admin/beds/summary
POST|PATCH|DELETE /api/v1/admin/beds[/{bed_id}]
```

### Reports (FR-57)

```http
GET /api/v1/admin/reports/patient-census?from=&to=
GET /api/v1/admin/reports/wait-times?from=&to=
GET /api/v1/admin/reports/discharges?from=&to=
GET /api/v1/admin/reports/bed-occupancy
GET /api/v1/admin/reports/dashboard
GET /api/v1/admin/reports/revenue-summary   # returns 501 until billing tables exist
```

### Backups (FR-58)

```http
POST /api/v1/admin/backups
GET /api/v1/admin/backups
GET /api/v1/admin/backups/status
GET /api/v1/admin/backups/{backup_id}/download
```
Uses `pg_dump` into `BACKUP_ROOT/{tenant_id}/`. Hospital admin cannot restore (ops/superadmin). Apply tenant migration `0014_admin_module_tables` (and later heads) via `python scripts/migrate_all_tenants.py` or Docker:

```text
docker cp scripts/migrate_existing_tenants.py hospital-master-service:/tmp/migrate_existing_tenants.py
docker exec -w /app hospital-master-service python /tmp/migrate_existing_tenants.py
```

Or recreate master-service so `/app/scripts` is mounted, then:

```text
docker compose -f infrastructure/docker-compose.yml up -d master-service
docker exec -w /app hospital-master-service python /app/scripts/migrate_existing_tenants.py
```

## Ward / Inpatient (FR-47–FR-52)

Prefix: `/api/v1/ward`. Roles: doctors/clinicians admit & discharge; nurses write notes and manage bed assignment; hospital_admin read-all.

### Beds (FR-47)

```http
GET  /api/v1/ward/beds?ward_name=&bed_type=&is_available=&is_active=true
GET  /api/v1/ward/beds/board
POST /api/v1/ward/beds/{bed_id}/assign   # body: { "admission_id": "..." } optional
POST /api/v1/ward/beds/{bed_id}/release
```

Bed **catalog** CRUD remains under `/api/v1/admin/beds` (FR-55). Ward only assigns/releases availability.

### Admissions (FR-48 / FR-51 / FR-52)

```http
POST /api/v1/ward/admissions
GET  /api/v1/ward/admissions?status=&patient_id=&ward_name=
GET  /api/v1/ward/admissions/{admission_id}
GET  /api/v1/ward/admissions/{admission_id}/los
POST /api/v1/ward/admissions/{admission_id}/discharge
```

Create body: `{ "visit_id", "bed_id", "admitting_diagnosis" }`.  
If a consultation exists for the visit, its `disposition` must be `admission`.  
Discharge publishes `patient.discharged` (includes `length_of_stay_days`, `visit_id`); billing-service adds an idempotent `WARD_DAY` bill line.

### Orders & nursing notes (FR-49 / FR-50)

```http
POST  /api/v1/ward/admissions/{id}/orders
GET   /api/v1/ward/admissions/{id}/orders
PATCH /api/v1/ward/admissions/{id}/orders/{order_id}
POST  /api/v1/ward/admissions/{id}/nursing-notes
GET   /api/v1/ward/admissions/{id}/nursing-notes
```

Order types: `medication|nursing|diet|investigation|procedure|other`.  
Note types: `observation|intervention|progress|medication_given|ward_round`.

### Consultation disposition (FR-18 / FR-21)

```http
POST /api/v1/consultation/encounters/{consultation_id}/diagnoses
  # diagnosis_type: provisional | differential | final

POST /api/v1/consultation/encounters/{consultation_id}/disposition
  # { "disposition": "outpatient|admission|referral|deceased", "notes": "..." }
```

`admission` and `outpatient` require a `final` diagnosis first.

Tenant migration: `0016_ward_module_tables` (admissions, inpatient_orders, nursing_notes, bills, bill_items, consultation disposition columns, visit status enum values).

## Tenant Self-Service

#### Get Own Subscription
```http
GET /api/v1/tenant/subscription
Authorization: Bearer <token>
```

## Error Codes

| Code | Status | Description |
|------|--------|-------------|
| `TENANT_SUSPENDED` | 403 | Tenant subscription is suspended |
| `TENANT_TERMINATED` | 403 | Tenant account has been terminated |
| `BRUTE_FORCE_BLOCKED` | 429 | Too many failed login attempts |
| `TRIAL_ALREADY_USED` | 400 | Tenant has already used its free trial |
| `INVALID_PLAN` | 400 | Invalid subscription plan selection |
| `UPGRADE_FROM_TRIAL` | 400 | Cannot upgrade while on free trial |
| `READ_ONLY_SCOPE` | 403 | Write operations not allowed in readonly mode |
| `PAYLOAD_TOO_LARGE` | 413 | Request body exceeds size limit |

## Rate Limits

| Endpoint | Limit |
|----------|-------|
| Login | 10/minute |
| Signup | 5/minute |
| Refresh | 20/minute |
| Password Reset | 3/minute |
| Admin CRUD | 30/minute |
| Super Admin | 30/minute |

## Test Users

| Role | Username | Password |
|------|----------|----------|
| Super Admin | `superadmin` | `superadmin123` |
| Hospital Admin | `hadmin1` | `admin12345` |
| Hospital User | `staff1` | `staff1234` |

## OpenAPI / Swagger

In development, interactive API documentation is available at:
```
http://localhost:8000/docs
```

In production, Swagger is disabled for security.
