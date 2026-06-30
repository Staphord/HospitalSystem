# PostgreSQL Database Commands

## Connect & navigate databases

### 1. Jump into the PostgreSQL container

```bash
docker exec -it hospital-postgres-master psql -U postgres
```

Password: `nasr`

> This connects to the default `postgres` database inside the container. From here you can see **all** databases.

### 2. List all databases

```sql
\l
```

You'll see `hospital_master` and one or more `tenant_hosp-xxxxxxxx` databases.

### 3. Connect to the Master DB

```sql
\c hospital_master
```

Now run any master-db query (tenants, users, subscriptions, super_admins, etc.).

### 4. Connect to a specific Tenant DB

First find tenant IDs:

```sql
\c hospital_master
SELECT tenant_id FROM tenants;
```

Then connect:

```sql
\c tenant_hosp-xxxxxxxx
```

### 5. Or connect directly on one shot

```bash
# Direct to master
docker exec -it hospital-postgres-master psql -U postgres -d hospital_master

# Direct to a tenant database
docker exec -it hospital-postgres-master psql -U postgres -d tenant_hosp-xxxxxxxx
```

## Useful queries

### Master DB (hospital_master)

```sql
-- List all tenants
SELECT tenant_id, name, status, subscription_plan, is_active, created_at
FROM tenants
ORDER BY created_at DESC;

-- Count users per tenant
SELECT hospital_id, role, COUNT(*) as count
FROM users
GROUP BY hospital_id, role
ORDER BY hospital_id, role;

-- Find a tenant by ID
SELECT * FROM tenants WHERE tenant_id = 'hosp-xxxxxxxx';

-- Subscription state for a tenant
SELECT t.tenant_id, t.name, t.status, t.subscription_plan,
       s.start_date, s.end_date, s.billing_cycle, s.auto_renew,
       sp.name as plan_name, sp.price
FROM tenants t
LEFT JOIN subscriptions s ON t.tenant_id = s.tenant_id AND s.is_active = true
LEFT JOIN subscription_plans sp ON s.plan_id = sp.plan_id
WHERE t.tenant_id = 'hosp-xxxxxxxx';

-- All super admin users
SELECT id, username, full_name, email, role
FROM super_admins;

-- Audit log for a tenant
SELECT * FROM global_audit_logs
WHERE tenant_id = 'hosp-xxxxxxxx'
ORDER BY created_at DESC
LIMIT 20;

-- Pending subscription/suspension actions
SELECT * FROM global_audit_logs
WHERE action IN ('tenant_suspended', 'tenant_terminated', 'subscription_expired')
ORDER BY created_at DESC
LIMIT 20;
```

### Tenant DB (tenant_hosp-xxxxxxxx)

```sql
-- All visits with payment info
SELECT v.visit_id, v.visit_number, v.payment_type,
       pi.insurer_name, pi.policy_number, pi.verification_status,
       v.verification_flag, v.status, v.visit_date
FROM visits v
LEFT JOIN patient_insurance pi ON v.insurance_id = pi.insurance_id
ORDER BY v.created_at DESC
LIMIT 30;

-- Insurance patients needing manual verification
SELECT v.visit_number, pi.insurer_name, pi.policy_number,
       pi.verification_status, pi.coverage_limit, pi.expiry_date,
       v.verification_flag
FROM visits v
JOIN patient_insurance pi ON v.insurance_id = pi.insurance_id
WHERE v.verification_flag IS NOT NULL
  AND v.verification_flag = 'manual_review_required'
ORDER BY v.created_at DESC;

-- Cash visits today
SELECT COUNT(*) as cash_visits_today
FROM visits
WHERE payment_type = 'cash'
  AND visit_date = CURRENT_DATE;

-- Insurance visits today
SELECT COUNT(*) as insurance_visits_today
FROM visits
WHERE payment_type = 'insurance'
  AND visit_date = CURRENT_DATE;

-- Queue state
SELECT q.queue_id, q.queue_type, q.queue_number, q.status,
       q.priority, v.visit_number, v.payment_type
FROM queues q
JOIN visits v ON q.visit_id = v.visit_id
WHERE q.status = 'waiting'
ORDER BY q.created_at ASC;

-- All patients
SELECT patient_id, full_name, gender, phone, created_at
FROM patients
ORDER BY created_at DESC;

-- Verify a specific insurance policy
SELECT * FROM patient_insurance
WHERE patient_id = 'uuid-here'
  AND is_active = true;

-- Visits today summary by type
SELECT visit_type, payment_type, COUNT(*) as count
FROM visits
WHERE visit_date = CURRENT_DATE
GROUP BY visit_type, payment_type;

-- Cancel/expired insurance policies
SELECT * FROM patient_insurance
WHERE is_active = false OR expiry_date < CURRENT_DATE;
```

## Quick stats

```sql
-- Master DB connection info
\conninfo

-- List tables in current DB
\dt

-- Describe a table
\d+ visits
\d+ patient_insurance
\d+ tenants
\d+ subscriptions
```

## Redis (for cache inspection)

```bash
docker exec -it hospital-redis redis-cli
```

```redis
# List all keys
KEYS *

# Get tenant DB URL
GET tenant:db:hosp-xxxxxxxx

# Check if tenant is suspended
GET tenant:suspended:hosp-xxxxxxxx

# Flush cache (dev only)
FLUSHALL
```
