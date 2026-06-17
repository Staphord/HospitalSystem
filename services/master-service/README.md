# Master Service

The **Master Service** is the super-admin portal for the Hospital Flow SaaS platform. It owns the global master database, tenant onboarding, subscription lifecycle, billing metadata, and cross-tenant audit logging.

## Responsibilities

- Super-admin user CRUD
- Tenant (hospital) registration and provisioning
- Subscription plan catalog management
- Subscription lifecycle: subscribe, upgrade, downgrade, renew, suspend, reactivate, activate
- Free-trial management with abuse prevention
- Background suspension job for expired subscriptions and trials
- Cross-tenant audit logging
- System announcements

## Tech Stack

- FastAPI + Uvicorn
- SQLAlchemy (PostgreSQL)
- Alembic migrations
- Keycloak OIDC
- RabbitMQ events
- Redis (caching / suspension blocklist)

## Running Locally (Docker)

From the project root:

```bash
docker compose -p hospital_flow -f infrastructure/docker-compose.yml up -d --build master-service
```

The service runs on port `8002` and is exposed through the API Gateway at `http://localhost:8000/api/v1/superadmin`.

## Database Migrations

Migrations live in `migrations/master/` and are applied automatically on startup.

To run migrations manually from inside a container:

```bash
docker exec -w /app/migrations/master hospital-master-service \
  python -m alembic -c alembic.ini upgrade head
```

## Environment Variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|----------|---------|
| `DATABASE_URL` | Master PostgreSQL DSN |
| `DB_ADMIN_URL` | Admin DSN with CREATEDB privilege |
| `TENANT_DB_TEMPLATE` | Template DSN for tenant DBs (`{tenant_id}` placeholder) |
| `REDIS_URL` | Redis cache |
| `KEYCLOAK_*` | Keycloak realm and client credentials |
| `TENANT_DB_ENCRYPTION_KEY` | Fernet key for encrypting tenant DB DSNs |
| `RABBITMQ_URL` | RabbitMQ connection string |

## API Endpoints

All endpoints require the `super_admin` Keycloak realm role.

### Super-admin users

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/superadmin/users` | Create super-admin |
| PATCH | `/api/v1/superadmin/users/{id}` | Update super-admin |
| DELETE | `/api/v1/superadmin/users` | Delete super-admin |
| GET | `/api/v1/superadmin/users` | List super-admins |

### Tenants

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/superadmin/tenants` | List tenants |
| POST | `/api/v1/superadmin/tenants` | Create tenant + provision DB |
| PATCH | `/api/v1/superadmin/tenants/{tenant_id}` | Update tenant details |

### Subscription lifecycle

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/superadmin/plans` | List canonical plans |
| GET | `/api/v1/superadmin/tenants/{tenant_id}/subscription` | Subscription state |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/subscribe` | Start/replace subscription |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/upgrade` | Upgrade plan |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/downgrade` | Downgrade plan |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/renew` | Renew subscription |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/activate` | Activate account |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/suspend` | Suspend account |
| POST | `/api/v1/superadmin/tenants/{tenant_id}/reactivate` | Reactivate account |

### SaaS catalog / audit

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/superadmin/subscription-plans` | DB-backed plan catalog |
| GET | `/api/v1/superadmin/tenants/{tenant_id}/subscriptions` | Subscription history |
| GET | `/api/v1/superadmin/tenants/{tenant_id}/subscription-audit-log` | Lifecycle audit log |
| GET | `/api/v1/superadmin/announcements` | List announcements |
| POST | `/api/v1/superadmin/announcements` | Create announcement |

## Database Schema

### `tenants`

Central registry of subscribing hospitals.

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | Surrogate key |
| `tenant_id` | VARCHAR(64) UQ | Public tenant identifier (UUID string) |
| `name` | VARCHAR(255) | Hospital name |
| `country` | VARCHAR(100) | Country |
| `city` | VARCHAR(100) | City |
| `address` | TEXT | Physical address |
| `primary_contact_name` | VARCHAR(200) | Contact person |
| `primary_contact_email` | VARCHAR(150) | Contact email |
| `primary_contact_phone` | VARCHAR(20) | Contact phone |
| `billing_email` | VARCHAR(150) | Invoice email |
| `timezone` | VARCHAR(50) | e.g. `Africa/Dar_es_Salaam` |
| `currency` | VARCHAR(5) | e.g. `TZS`, `USD` |
| `date_format` | VARCHAR(20) | Preferred date format |
| `logo_url` | VARCHAR(255) | Hospital logo URL |
| `data_region` | VARCHAR(50) | Hosting region |
| `db_dsn_encrypted` | TEXT | Encrypted tenant DB connection string |
| `status` | VARCHAR(32) | `trial` / `active` / `suspended` / `terminated` |
| `trial_ends_at` | TIMESTAMPTZ | End of trial period |
| `created_by` | UUID FK | → `super_admins.super_admin_id` |
| `created_at` / `updated_at` | TIMESTAMPTZ | Audit timestamps |

### `subscription_plans`

Available subscription tiers.

| Column | Type | Notes |
|--------|------|-------|
| `plan_id` | UUID PK | Unique plan ID |
| `plan_name` | VARCHAR(50) UQ | e.g. `basic`, `standard`, `premium` |
| `description` | TEXT | Plan description |
| `max_users` | INT | Max users (`null` = unlimited) |
| `max_patients` | INT | Max patients (`null` = unlimited) |
| `storage_gb` | INT | Storage limit |
| `modules_included` | JSONB | Included module keys |
| `monthly_price` | NUMERIC(10,2) | Monthly price |
| `annual_price` | NUMERIC(10,2) | Annual price |
| `annual_discount_pct` | NUMERIC(4,1) | Annual discount % |
| `uptime_sla_pct` | NUMERIC(5,2) | Uptime SLA |
| `backup_frequency_hours` | INT | Backup frequency |
| `is_active` | BOOLEAN | Available for new subscriptions |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

### `subscriptions`

Active or historical subscriptions per tenant.

| Column | Type | Notes |
|--------|------|-------|
| `subscription_id` | UUID PK | Unique subscription ID |
| `tenant_id` | VARCHAR(64) FK | → `tenants.tenant_id` |
| `plan_id` | UUID FK | → `subscription_plans.plan_id` |
| `billing_cycle` | VARCHAR(16) | `monthly` / `annual` |
| `start_date` | DATE | Term start |
| `end_date` | DATE | Term end |
| `grace_period_days` | INT | Days before suspension after expiry |
| `auto_renew` | BOOLEAN | Auto-renew flag |
| `status` | VARCHAR(32) | `trial` / `active` / `grace` / `suspended` / `cancelled` / `terminated` |
| `suspended_at` | TIMESTAMPTZ | Suspension timestamp |
| `cancelled_at` | TIMESTAMPTZ | Cancellation timestamp |
| `cancellation_reason` | TEXT | Cancellation reason |
| `created_at` | TIMESTAMPTZ | Creation timestamp |

### `invoices`

Subscription invoices per billing cycle.

| Column | Type | Notes |
|--------|------|-------|
| `invoice_id` | UUID PK | Unique invoice ID |
| `tenant_id` | VARCHAR(64) FK | → `tenants.tenant_id` |
| `subscription_id` | UUID FK | → `subscriptions.subscription_id` |
| `invoice_number` | VARCHAR(30) UQ | Auto-generated |
| `billing_period_start` / `end` | DATE | Billing period |
| `plan_name` | VARCHAR(50) | Plan name at invoice time |
| `amount` | NUMERIC(10,2) | Invoice amount |
| `currency` | VARCHAR(5) | Currency |
| `due_date` | DATE | Payment due date |
| `status` | VARCHAR(32) | `unpaid` / `paid` / `overdue` / `void` |
| `issued_at` / `paid_at` | TIMESTAMPTZ | Issue/payment timestamps |

### `saas_payments`

Payment receipts from hospitals.

| Column | Type | Notes |
|--------|------|-------|
| `payment_id` | UUID PK | Unique payment ID |
| `invoice_id` | UUID FK | → `invoices.invoice_id` |
| `tenant_id` | VARCHAR(64) FK | → `tenants.tenant_id` |
| `amount` | NUMERIC(10,2) | Amount received |
| `currency` | VARCHAR(5) | Currency |
| `payment_method` | VARCHAR(50) | e.g. bank transfer, card |
| `reference_number` | VARCHAR(100) | Bank/gateway reference |
| `recorded_by` | UUID FK | → `super_admins.super_admin_id` |
| `receipt_sent_at` | TIMESTAMPTZ | Receipt email timestamp |
| `paid_at` | TIMESTAMPTZ | Payment date |

### `super_admin_audit_log`

Tamper-proof log of super-admin actions.

| Column | Type | Notes |
|--------|------|-------|
| `log_id` | UUID PK | Unique log entry |
| `super_admin_id` | UUID FK | → `super_admins.super_admin_id` |
| `action` | VARCHAR(100) | Action key |
| `tenant_id` | VARCHAR(64) FK | Affected tenant (optional) |
| `action_detail` | JSONB | Detailed parameters |
| `is_impersonation` | BOOLEAN | Impersonation flag |
| `ip_address` | VARCHAR(45) | Actor IP |
| `created_at` | TIMESTAMPTZ | Timestamp |

### `announcements`

System-wide or targeted announcements.

| Column | Type | Notes |
|--------|------|-------|
| `announcement_id` | UUID PK | Unique announcement ID |
| `title` | VARCHAR(200) | Title |
| `body` | TEXT | Content |
| `audience` | VARCHAR(16) | `all` / `selected` |
| `target_tenant_ids` | JSONB | Selected tenants |
| `publish_at` / `expires_at` | TIMESTAMPTZ | Visibility window |
| `created_by` | UUID FK | → `super_admins.super_admin_id` |
| `created_at` | TIMESTAMPTZ | Timestamp |

### `subscription_audit_log`

Log of subscription lifecycle events.

| Column | Type | Notes |
|--------|------|-------|
| `event_id` | UUID PK | Unique event ID |
| `tenant_id` | VARCHAR(64) FK | → `tenants.tenant_id` |
| `subscription_id` | UUID FK | → `subscriptions.subscription_id` |
| `event_type` | VARCHAR(64) | e.g. `plan_upgraded`, `suspension` |
| `actor_id` | UUID | Actor super-admin (optional) |
| `actor_type` | VARCHAR(32) | `super_admin` / `system` / `hospital_admin` |
| `reason` | TEXT | Reason or notes |
| `created_at` | TIMESTAMPTZ | Timestamp |

## Security Notes

- Plan names and prices are server-side constants; clients cannot invent plans.
- Free trials are limited to one per tenant via `has_used_trial`.
- Suspension requires a reason, sets a Redis blocklist entry, and revokes Keycloak sessions.
- Reactivation is blocked for cancelled tenants or tenants past the grace period.
- All subscription and tenant-account mutations are written to both `global_audit_logs` and `subscription_audit_log`.

## Tests

Run unit tests inside the container:

```bash
docker exec hospital-master-service python -m pytest tests/unit -v
```
