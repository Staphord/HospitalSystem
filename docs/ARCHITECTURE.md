# Hospital Flow Architecture

## Overview

Hospital Flow is a multi-tenant hospital management system built on a microservices architecture. It provides role-based access control, tenant isolation, subscription lifecycle management, and a Streamlit-based frontend portal.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Client Layer                                   │
│  ┌─────────────┐  ┌─────────────────┐  ┌─────────────────────────────────┐ │
│  │  Web App    │  │  Streamlit      │  │  Swagger / OpenAPI (dev only)   │ │
│  │  (Future)   │  │  (Port 8501)    │  │  (Port 8000/docs)               │ │
│  └─────────────┘  └─────────────────┘  └─────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                           API Gateway (Port 8000)                             │
│  - JWT Verification                                                           │
│  - Tenant Resolution & DB URL Injection                                       │
│  - Rate Limiting                                                              │
│  - Request Routing & Reverse Proxy                                            │
│  - Security Headers (CSP, HSTS, X-Frame-Options, etc.)                        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                       ▼
┌─────────────────┐  ┌─────────────────────────┐  ┌─────────────────────────┐
│ Auth Service    │  │ Master Service          │  │ Admin Service           │
│ (Port 8001)     │  │ (Port 8002)             │  │ (Port 8018)             │
│                 │  │                         │  │                         │
│ - Login/Signup  │  │ - Super Admin Mgmt      │  │ - Hospital Admin Mgmt   │
│ - Token Refresh │  │ - Tenant Management     │  │ - User CRUD             │
│ - Password Reset│  │ - Subscription Lifecycle│  │ - Role Assignment       │
│ - MFA           │  │ - Billing & Invoicing   │  │ - Account Status        │
│ - Impersonation │  │ - Audit Logging         │  │ - Force Password Change │
└─────────────────┘  └─────────────────────────┘  └─────────────────────────┘
              │                       │                       │
              ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Infrastructure Layer                                │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐ │
│  │ PostgreSQL  │  │    Redis    │  │   RabbitMQ  │  │     Keycloak        │ │
│  │ Master DB   │  │  Cache /    │  │   Events    │  │  Identity Provider  │ │
│  │ Tenant DBs  │  │  Blocklist  │  │   Messaging │  │  (Port 8080)        │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Microservices

### 1. API Gateway (Port 8000)
- **Purpose**: Single entry point for all client requests
- **Key Features**:
  - JWT token verification using Keycloak JWKS (realm resolved from token `iss` claim)
  - Tenant resolution with Redis caching
  - Reverse proxying to downstream services
  - Rate limiting via `slowapi`
  - Security headers injection
  - Suspended tenant lockout

### 2. Auth Service (Port 8001)
- **Purpose**: Authentication, authorization, and identity management
- **Key Features**:
  - Login / Signup / Refresh / Logout
  - Super Admin login portal (authenticates against `master` Keycloak realm)
  - Password reset via Keycloak
  - Multi-Factor Authentication (MFA)
  - Impersonation tokens for read-only tenant access
  - Brute-force protection via Redis
  - Automatic tenant database provisioning on signup
  - Per-tenant Keycloak realm creation on signup (client, roles, protocol mappers)

### 3. Master Service (Port 8002)
- **Purpose**: Super Admin portal and global tenant management
- **Key Features**:
  - Super Admin user CRUD
  - Tenant lifecycle management (create, activate, suspend, terminate)
  - Subscription plan catalog and ranking
  - Subscription state machine (subscribe, upgrade, downgrade, renew)
  - Invoice and SaaS payment tracking
  - Audit logging
  - Background suspension job for expired subscriptions

### 4. Admin Service (Port 8018)
- **Purpose**: Hospital-level user management
- **Key Features**:
  - CRUD users within a tenant
  - Role assignment (hospital_admin, nurse, clinician, doctor, patient)
  - Account activation/suspension
  - Force password change on first login
  - Keycloak user synchronization

### 5. Clinical Services (Ports 8010-8017)
- **Reception** (8010): Patient registration and check-in
- **Triage** (8011): Emergency triage and priority assessment
- **Consultation** (8012): Doctor consultations and prescriptions
- **Laboratory** (8013): Lab tests and results
- **Radiology** (8014): Imaging and radiology reports
- **Pharmacy** (8015): Medication dispensing
- **Billing** (8016): Invoicing and payments
- **Ward** (8017): Bed management and inpatient care

### 6. Support Services (Ports 8019-8020)
- **Notification** (8019): Email, SMS, and push notifications
- **Report** (8020): Analytics and reporting

## Data Isolation

### Multi-Tenancy Model
- **Master Database**: Shared metadata (tenants, super admins, subscription plans, audit logs)
- **Tenant Databases**: Isolated per-hospital databases for clinical data
- **Keycloak Realms**: Superadmins authenticate against the `master` realm; each tenant gets its own Keycloak realm (named `{tenant_id}`) for full identity isolation
- **Provisioning**: Automatic creation via `auth-service` on signup, triggered by RabbitMQ events
- **Encryption**: Tenant DB DSNs are encrypted at rest using Fernet

### Database Per Tenant
```
postgres-master
├── hospital_master          (global metadata)
├── tenant_hosp-0e63578b     (Qualitas Hospital)
├── tenant_hosp-bb88a4bc     (Muhimbili)
└── tenant_hosp-e716f2cd     (Muhimbili)
```

## Keycloak Multi-Realm Architecture

Superadmins and tenant users live in separate Keycloak realms for maximum isolation:

| User Type | Keycloak Realm | Created By |
|-----------|---------------|-----------|
| `super_admin` | `master` (built-in) | Keycloak admin UI or bootstrap |
| Tenant users (hospital_admin, nurse, etc.) | `{tenant_id}` (e.g. `hosp-abc123`) | Auto-created on signup |

### Realm Provisioning (on signup or superadmin tenant creation)
1. Create realm named `{tenant_id}` in Keycloak
2. Create `hospital-api` client with `directAccessGrantsEnabled` and `standardFlowEnabled`
3. Add protocol mappers for `tenant_id` (user attribute) and `email` to the client
4. Register `tenant_id` in realm user profile (required for Keycloak 26+)
5. Create realm roles: `hospital_admin`, `hospital_user`, `nurse`, `clinician`, `doctor`, `patient`

### Token Validation
- The API Gateway and Auth Service extract the realm from the JWT `iss` (issuer) claim
- JWKS public keys are fetched from the correct realm's `certs` endpoint
- Realm is cached per token for 5 minutes

## Authentication Flow

```
┌─────────┐     ┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│  Client │────▶│ API Gateway │────▶│  Keycloak        │────▶│ Auth Service│
│         │     │             │     │  (resolves realm  │     │             │
│         │     │  JWT Verify │     │   from `iss`,     │     │  Login:     │
│         │     │  (per-realm │◀────│   fetches JWKS    │◀────│  POST to    │
│         │     │   JWKS)     │     │   per realm)      │     │  realm/token│
└─────────┘     └─────────────┘     └──────────────────┘     └─────────────┘
```

### Login Flow
1. Client sends `POST /api/v1/auth/login` with `username` and `password`
2. Auth service resolves realm:
   - For regular users: looks up `tenant.keycloak_realm` from the Tenant record
   - For superadmins: tries `master` realm first, falls back to default realm
3. Auth service calls Keycloak token endpoint for the resolved realm
4. On success, returns JWT with tenant context

### Signup Flow
1. Client sends `POST /api/v1/auth/signup`
2. Auth service creates tenant record in Master DB
3. Auth service creates a new Keycloak realm (`{tenant_id}`) with client, roles, and user profile
4. Auth service creates the admin user in the new realm
5. Auth service publishes `tenant.created` event → Master Service provisions the tenant DB

### Role Hierarchy
```
super_admin
    └── hospital_admin
            ├── nurse
            ├── clinician
            ├── doctor
            ├── patient
            └── hospital_user
```

## Event-Driven Architecture

### RabbitMQ Events
- `tenant.created` → Published by auth-service on signup
- `tenant.provisioned` → Published by master-service after DB creation
- `tenant.suspended` → Published by master-service on suspension
- `user.created` → Published by admin-service on user creation

### Event Flow
```
Auth Service ──[tenant.created]──▶ Master Service ──[tenant.provisioned]──▶ All Services
```

## Security Architecture

See [SECURITY.md](SECURITY.md) for detailed security documentation.

Key principles:
- **Zero Trust**: Every request is verified at the gateway
- **Defense in Depth**: Multiple layers of protection (rate limiting, brute-force protection, input validation)
- **Least Privilege**: Users only have access to their tenant's data
- **Encryption**: Encrypted DSNs, HTTPS, secure headers
- **Audit**: All actions logged to `global_audit_logs`

## Technology Stack

| Layer | Technology |
|-------|-----------|
| API Framework | FastAPI (Python 3.11) |
| Frontend | Streamlit |
| Identity | Keycloak (OIDC/OAuth2) |
| Database | PostgreSQL 16 |
| Cache | Redis 7 |
| Messaging | RabbitMQ 3.13 |
| Container | Docker & Docker Compose |
| Orchestration | Kubernetes (k8s manifests included) |
| Migrations | Alembic |

## Deployment

See [DEPLOYMENT.md](DEPLOYMENT.md) for deployment instructions.

## Development

See [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for local development setup.
