# Hospital Management System - First-Time Setup Guide

## Architecture Overview

This system uses **multi-tenant database architecture**:

- **Master Database**: Stores global data (tenants, super_admins, global_audit_logs, etc.)
- **Tenant Databases**: Each hospital gets its own isolated database for user data

```
Master DB (hospital-master)
  ├── tenants table (all hospitals)
  ├── super_admins table
  ├── global_audit_logs table
  └── ... (other global tables)

Tenant DB (tenant_hosp-xxx)
  ├── users table (only this hospital's users)
  ├── patients table
  ├── visits table
  └── ... (other clinical tables)
```

## Database Setup

### 1. Create the Master Database (Optional - Auto-Creation Enabled)

The application **automatically creates the master database** if it doesn't exist. However, if you want to create it manually:

```sql
CREATE DATABASE hospital_master;
```

**How auto-creation works:**
- When you run `uvicorn app.main:app --reload`, the app checks if the database in `DATABASE_URL` exists
- If not, it uses the `DB_ADMIN_URL` connection to create the database automatically
- You'll see `[AUTO-CREATE] Database 'hospital-master' created successfully` in the console

**Note**: This is the global database that stores tenant registry and super admin data.

### 2. Configure Environment Variables

Create a `.env` file in the project root with these values:

```env
# === Master Database (Global) ===
DATABASE_URL=postgresql://postgres:nasr@localhost:5432/hospital-master

# === Keycloak ===
KEYCLOAK_URL=http://127.0.0.1:8080
KEYCLOAK_REALM=hospital-realm
KEYCLOAK_CLIENT_ID=hospital-api
KEYCLOAK_CLIENT_SECRET=HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL
KEYCLOAK_ADMIN_USERNAME=admin
KEYCLOAK_ADMIN_PASSWORD=admin
KEYCLOAK_INTROSPECT=false

# === Redis (Caching) ===
REDIS_URL=redis://localhost:6379/0

# === Security ===
SECRET_KEY=6477db2372e99bef59ff6d4fa4edef3f3891daee3807153d4ea09448bec2f6c6
TENANT_DB_ENCRYPTION_KEY=RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=

# === PostgreSQL Admin (for creating new tenant databases) ===
DB_ADMIN_URL=postgresql://postgres:nasr@localhost:5432/postgres
TENANT_DB_TEMPLATE=postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}

# === Application Settings ===
ENVIRONMENT=dev
ALLOWED_ORIGINS=http://localhost:8000,http://localhost:8501
DEFAULT_HOSPITAL_ID=default-hospital

# === Optional ===
PASSWORD_RESET_TOKEN_TTL=3600
IMPERSONATION_TOKEN_TTL=900
SUSPENSION_CHECK_INTERVAL=86400
SUSPENDED_BLOCKLIST_TTL=3600
```

**Important**: The `DB_ADMIN_URL` must connect to a database where the user has **CREATEDB** privilege. We use `postgres` (the default PostgreSQL database) because the admin user needs to create new databases.

### 3. Initialize the Master Database

Run these commands to create all tables in the master database:

```bash
# Using alembic (recommended)
alembic -c migrations/master/alembic.ini upgrade head

# Or using the application (auto-creates tables)
uvicorn app.main:app --reload
```

**What happens when you run uvicorn:**
1. FastAPI starts and loads the app
2. `init_db()` is called automatically
3. It creates all tables (users, tenants, super_admins, etc.) in the **master database** defined in `DATABASE_URL`
4. The application is ready to serve requests

### 4. Verify Master Database

Check that tables were created in pgAdmin:
```sql
-- Run in hospital-master database
SELECT table_name FROM information_schema.tables 
WHERE table_schema = 'public' 
ORDER BY table_name;
```

You should see: `alembic_version`, `global_audit_logs`, `password_reset_tokens`, `refresh_tokens`, `super_admins`, `tenants`, `users`

### 5. Create a Super Admin

Create the first super admin user:

```bash
venv\Scripts\python.exe scripts/create_superuser.py --username=superadmin --password=Nassir_05 --email=admin@hosp.com --role=super_admin
```

Or manually:

```sql
-- In hospital-master database
INSERT INTO super_admins (super_admin_id, username, email, password_hash, full_name, role, mfa_secret, is_active, created_at)
VALUES (
    gen_random_uuid(),
    'superadmin',
    'superadmin@hospital.com',
    '$2b$12$...',  -- bcrypt hash of password
    'Super Admin',
    'super_admin',
    'placeholder',
    true,
    NOW()
);
```

## How Tenant Databases Work

### When a New Hospital Signs Up

**Flow:**
1. User fills signup form (hospital name, admin username, email, password)
2. Backend generates a `tenant_id` (e.g., `hosp-a1b2c3d4`)
3. Backend creates a tenant record in the **master database** with a placeholder DSN
4. Backend **creates a new PostgreSQL database** named `tenant_hosp-a1b2c3d4`
5. Backend runs `alembic upgrade head` on the new database to create tables
6. Backend updates the tenant record with the **real encrypted DSN**
7. Backend creates the admin user in **Keycloak** and sets the `tenant_id` attribute
8. Backend creates the admin user in the **tenant database** (NOT the master database)

**Where is the user stored?**
- The hospital admin is stored in `tenant_hosp-a1b2c3d4.users` table
- The tenant record is stored in `hospital-master.tenants` table
- The user is NOT stored in `hospital-master.users` table

### When Super Admin Creates a Tenant

**Flow:**
1. Super admin calls `POST /api/v1/superadmin/tenants`
2. Backend creates a tenant record in the **master database**
3. Backend **creates a new PostgreSQL database** for the tenant
4. Backend runs migrations on the new database
5. Backend updates the tenant record with the real encrypted DSN
6. Backend creates the hospital admin in **Keycloak** with the tenant_id attribute
7. Backend creates the hospital admin in the **tenant database**

### When Hospital Admin Creates a User

**Flow:**
1. Hospital admin logs in and gets a JWT with `tenant_id` claim
2. Admin calls `POST /api/v1/admin/users` to create Dr. John
3. Backend extracts `tenant_id` from the JWT
4. Backend looks up the tenant's database DSN from the master database
5. Backend connects to the **tenant database** (e.g., `tenant_hosp-a1b2c3d4`)
6. Backend creates the user in the **tenant database** (NOT the master database)
7. Backend also creates the user in **Keycloak** with the tenant_id attribute

**Result:** Dr. John exists in `tenant_hosp-a1b2c3d4.users` table, NOT in `hospital-master.users` table.

## How to Verify It's Working

### Test 1: Sign Up a New Hospital

```bash
curl -X POST http://localhost:8000/api/v1/auth/signup \
  -H "Content-Type: application/json" \
  -d '{
    "hospital_name": "Test Hospital",
    "admin_username": "testadmin",
    "admin_password": "password123",
    "admin_email": "testadmin@hospital.com",
    "admin_full_name": "Test Admin"
  }'
```

**Expected result:**
- A new database `tenant_hosp-xxx` appears in pgAdmin
- The tenant record in `hospital-master.tenants` has a real encrypted DSN
- The user `testadmin` is in `tenant_hosp-xxx.users` table, NOT in `hospital-master.users`

### Test 2: Create a User in the Hospital

```bash
curl -X POST http://localhost:8000/api/v1/admin/users \
  -H "Authorization: Bearer <hospital_admin_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "drjohn",
    "email": "drjohn@hospital.com",
    "password": "password123",
    "role": "doctor",
    "full_name": "Dr John"
  }'
```

**Expected result:**
- User `drjohn` is in `tenant_hosp-xxx.users` table
- User `drjohn` is NOT in `hospital-master.users` table

### Test 3: Check Database Isolation

```sql
-- In master database (hospital-master)
SELECT COUNT(*) FROM users WHERE hospital_id = 'hosp-xxx';
-- Expected: 0 (users are in tenant database, not master)

-- In tenant database (tenant_hosp-xxx)
SELECT * FROM users;
-- Expected: Shows the hospital admin and Dr. John
```

## Important Notes

### Database Connection Flow

```
App (FastAPI)
  ↓
  ├─→ Master Database (hospital-master) - for tenant registry, super admins
  │
  └─→ Tenant Database (tenant_hosp-xxx) - for hospital users, patients, visits
        ↑
        DSN is looked up from master database, decrypted, and cached
```

### Keycloak Integration

- All users are stored in **Keycloak** (single realm: `hospital-realm`)
- Each user has a `tenant_id` attribute in Keycloak
- The `keycloak_sub` in the local database matches the Keycloak UUID

### Migration Files

- `migrations/master/` - For master database tables (tenants, super_admins, etc.)
- `migrations/tenant/` - For tenant database tables (users, patients, visits, etc.)
- Both use `alembic.ini` and `env.py` to read connection URLs from environment variables

### Common Issues

1. **ValidationError for extra fields**: Fixed by adding `extra = "ignore"` to all config files
2. **Database not created**: Check that `DB_ADMIN_URL` user has CREATEDB privilege
3. **Users in wrong database**: Check that the admin router is using `get_tenant_db` not `get_db`
4. **pgAdmin not showing databases**: Refresh the database list in pgAdmin

## Quick Start Commands

```bash
# 1. Start PostgreSQL (make sure it's running)
# 2. Start Redis (make sure it's running)
# 3. Start Keycloak (make sure it's running)

# 4. Configure .env file with your database URLs
# DATABASE_URL=postgresql://postgres:nasr@localhost:5432/hospital-master
# DB_ADMIN_URL=postgresql://postgres:nasr@localhost:5432/postgres

# 5. Start the application (auto-creates master database if needed)
uvicorn app.main:app --reload

# 6. Create super admin
python scripts/create_superuser.py

# 7. Test signup
# Use Swagger UI at http://localhost:8000/docs
```

**Note**: The application automatically:
- Creates the master database if it doesn't exist
- Creates all required tables on startup
- Creates tenant databases when new hospitals sign up
- Runs tenant migrations on new tenant databases

## Migration from Old Single-Database Setup

If you previously had all users in `hospital-db`:

1. The old `hospital-db` users can still login (backward compatible)
2. New tenants get their own database
3. The old `hospital-db` remains as the default for existing users
4. To migrate old users, you would need to move them to their respective tenant databases

## .env Variable Reference

| Variable | Purpose | Example |
|----------|---------|---------|
| `DATABASE_URL` | Master database connection | `postgresql://postgres:nasr@localhost:5432/hospital-master` |
| `DB_ADMIN_URL` | PostgreSQL admin connection (for creating DBs) | `postgresql://postgres:nasr@localhost:5432/postgres` |
| `TENANT_DB_TEMPLATE` | Template for new tenant databases | `postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}` |
| `TENANT_DB_ENCRYPTION_KEY` | Fernet key for encrypting tenant DSNs | `RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0=` |

## Support

If you see users in the master database (`hospital-master.users`) after creating a tenant, that means:
1. The signup/admin endpoint is still using `get_db()` instead of `get_tenant_db()`
2. The tenant database was not created properly
3. Check the logs for errors about database creation or connection
