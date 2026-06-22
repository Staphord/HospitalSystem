# Hospital Flow — Architecture Explain

## 1. Plan Upgrade / Downgrade

### How it works

Plans are ordered by a **numeric rank** defined in `services/master-service/app/services/subscription_plans.py`:

| Plan | Rank |
|------|------|
| `free_trial` | 0 |
| `basic` | 1 |
| `standard` | 2 |
| `premium` | 3 |
| `enterprise` | 4 |

- **Upgrade** (`POST /api/v1/superadmin/tenants/{id}/upgrade`): validates that `rank(new_plan) > rank(current_plan)` and that the transition is valid (cannot go to `free_trial` since trials are one-time only). The change is **immediate** — the tenant's `subscription_plan` column is updated in the master DB, but the end date is **not extended** (effects are immediate but term unchanged).
- **Downgrade** (`POST /api/v1/superadmin/tenants/{id}/downgrade`): validates `rank(new_plan) < rank(current_plan)` and that the target is not `free_trial`. Change is **immediate** by default. The `effective_at_end` parameter exists for future use (scheduled downgrade at next renewal), but currently all downgrades take effect immediately.
- **Cannot** upgrade/downgrade while on a free trial. Must subscribe to a paid plan first.
- **Cannot** upgrade to the same plan or a lower plan (for upgrade) / higher plan (for downgrade).
- Every transition is written to `GlobalAuditLog` and `subscription_audit_log`.

### Who can do it

Only `super_admin` users. Tenant admins can **view** their subscription via `GET /api/v1/tenant/subscription` (self-service) but cannot change plans.

### How to test (via Swagger at `http://localhost:8002/docs`)

1. **Login as superadmin** → `POST /api/v1/auth/superadmin/login` with `superadmin` / `superadmin123`
2. Copy the `access_token` and click **Authorize** (paste as `Bearer <token>`)
3. **Check current subscription**: `GET /api/v1/superadmin/tenants/{tenant_id}/subscription`
4. **Upgrade**: `POST /api/v1/superadmin/tenants/{tenant_id}/upgrade` with body:
   ```json
   { "plan": "standard", "billing_cycle": "monthly" }
   ```
5. **Downgrade**: `POST /api/v1/superadmin/tenants/{tenant_id}/downgrade` with body:
   ```json
   { "plan": "basic", "billing_cycle": "monthly" }
   ```
6. **Verify**: Re-check the subscription state — `subscription.plan` will reflect the new plan.

---

## 2. Subscription Renewal

### How it works

`POST /api/v1/superadmin/tenants/{id}/renew` extends the subscription **from the current end date** by one full billing cycle (30 days monthly, 365 days annual).

- **Allowed** on: `ACTIVE`, `PAST_DUE`, and `SUSPENDED` tenants.
- **Blocked** on: `CANCELLED` (must re-subscribe) and `TRIAL` (must subscribe to a paid plan).
- If the end date is already past, the new term starts from `now` instead.
- The renewal re-activates the tenant: clears `suspended_at`, `suspended_reason`, flips `is_active = True`, and removes the Redis suspension blocklist entry.
- A new `subscriptions` history record is created and an audit event is logged.

### How to test

1. `POST /api/v1/superadmin/tenants/{tenant_id}/renew` with body:
   ```json
   { "billing_cycle": "monthly" }
   ```
2. Verify in the response that `subscription.start` and `subscription.end` have moved forward.
3. Check the tenant is `"is_active": true` and `"status": "active"`.

---

## 3. Impersonate Hospital Admin

### How it works

`POST /api/v1/auth/impersonate` allows a `super_admin` to generate a **signed JWT token** that impersonates a tenant context.

- The super_admin calls the endpoint with `{ "target_tenant_id": "hosp-XXXXXXXX" }`.
- The endpoint creates a **locally-signed JWT** (NOT from Keycloak) using the app's `SECRET_KEY` with:
  - `sub` = the super_admin's Keycloak sub
  - `tenant_id` = the target tenant's ID
  - `is_super_admin = True`, `impersonator = True`
  - `scope = "readonly"` (read-only access)
  - TTL controlled by `IMPERSONATION_TOKEN_TTL` (default 900s)
- The impersonation event is logged to `GlobalAuditLog`.
- The gateway proxy will see `tenant_id` in the token and resolve the tenant's DB, giving the super_admin access to the tenant's data as if they were inside that tenant.

### How to test

1. Login as superadmin → get `access_token`.
2. `POST /api/v1/auth/impersonate` with:
   ```json
   { "target_tenant_id": "hosp-xxxxxxxx" }
   ```
3. Use the returned `access_token` to call tenant-level endpoints (e.g., `GET /api/v1/tenant/subscription` or `GET /api/v1/admin/users`).

---

## 4. Tenant ID Auto-Fill on Login

### The flow in detail

The system uses **per-tenant Keycloak realms**. Here's how `tenant_id` gets injected into the JWT automatically:

#### a) Signup creates a per-tenant Keycloak realm

During signup (`POST /api/v1/auth/signup`), the auth-service:
1. Creates a new Keycloak realm named after the `tenant_id` (e.g., `hosp-a1b2c3d4`) via `create_tenant_realm()`.
2. Creates a `hospital-api` OIDC client inside that realm with **protocol mappers** that map the Keycloak user attribute `tenant_id` into the JWT claims (`access.token.claim`, `id.token.claim`, `userinfo.token.claim`).
3. Creates the admin user in the tenant's realm and sets `tenant_id` as a user attribute via `set_user_attribute()`.
4. Stores `keycloak_realm = tenant_id` on the `Tenant` master DB record.

#### b) Login resolves the correct realm

When a user logs in (`POST /api/v1/auth/login`):
1. If no `realm` is specified in the request body, the system calls `find_user_realm_by_username()` which searches **all** Keycloak realms for the username.
2. Once found, the login is performed against that tenant's realm at `{keycloak_url}/realms/{tenant_id}/protocol/openid-connect/token`.
3. Keycloak issues a JWT that includes `tenant_id` in the token payload because of the protocol mapper.

#### c) How tenant_id flows through the system

- **API Gateway** extracts `tenant_id` from the JWT (from `request.state.token_payload`) and uses it to:
  - Resolve the tenant DB URL via `get_tenant_db_url(tenant_id)` (from Redis cache → Master DB).
  - Inject `X-Tenant-DB` header to downstream services so they connect to the correct tenant database.
  - Check tenant suspension status via `is_tenant_suspended(tenant_id)`.
- **Downstream services** (admin-service, reception-service, etc.) read the `X-Tenant-DB` header and create a DB session scoped to that tenant's database.

#### d) Why the tenant doesn't need to manually enter tenant_id

The tenant_id flows automatically:
1. **Keycloak side**: User attribute → protocol mapper → JWT claim.
2. **Gateway side**: JWT token decode → `request.state.token_payload` → `tenant_id` extracted.
3. **No client input needed**: The user just enters username/password. The backend resolves everything from the JWT + Keycloak.

### Diagram

```
User Login (username + password)
    │
    ▼
auth-service: find_user_realm_by_username() → resolves tenant's Keycloak realm
    │
    ▼
Keycloak: authenticates in tenant's realm
    │
    ▼
JWT issued with tenant_id claim (via protocol mapper)
    │
    ▼
API Gateway: decodes JWT → extracts tenant_id
    ├── Resolves tenant DB URL → X-Tenant-DB header
    ├── Checks suspension via Redis/Master DB
    └── Forwards to downstream services
```
