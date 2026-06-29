import os, sys, subprocess
from sqlalchemy import create_engine, text

MASTER_URL = "postgresql://postgres:nasr@localhost:5432/hospital_master"

# 1. Create the tenant database
admin_engine = create_engine("postgresql://postgres:nasr@localhost:5432/postgres", isolation_level="AUTOCOMMIT")
tenant_id = "hosp-d6b79005"
db_name = f"tenant_{tenant_id}"

with admin_engine.connect() as conn:
    result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": db_name})
    if result.scalar():
        print(f"Database '{db_name}' already exists")
    else:
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
        print(f"Created database '{db_name}'")
admin_engine.dispose()

# 2. Run tenant migrations on the new DB
tenant_url = f"postgresql://postgres:nasr@localhost:5432/{db_name}"
env = {**dict(os.environ), "TENANT_DB_URL": tenant_url}
result = subprocess.run(
    [sys.executable, "-m", "alembic", "-c", "migrations/tenant/alembic.ini", "upgrade", "head"],
    env=env, capture_output=True, text=True, cwd=os.getcwd(),
)
if result.returncode != 0:
    print(f"[FAIL] Migration: {result.stderr.strip()}")
else:
    print(f"[OK] Migration completed")
    if result.stdout:
        print(result.stdout.strip())

# 3. Update the tenant record with encrypted DSN (use docker-internal hostname)
from cryptography.fernet import Fernet
key = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="
cipher = Fernet(key.encode())
dsn = f"postgresql://postgres:nasr@postgres-master:5432/{db_name}"
encrypted = cipher.encrypt(dsn.encode()).decode()
print(f"Encrypted DSN for internal host (postgres-master): {encrypted[:40]}...")

# Also compute localhost DSN for potential local use
dsn_local = f"postgresql://postgres:nasr@localhost:5432/{db_name}"
encrypted_local = cipher.encrypt(dsn_local.encode()).decode()
print(f"Encrypted DSN for localhost: {encrypted_local[:40]}...")

master_engine = create_engine(MASTER_URL)
with master_engine.connect() as conn:
    # Check if db_dsn_encrypted column exists
    cols = [r[0] for r in conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'tenants'")).all()]
    dsn_col = "db_dsn_encrypted" if "db_dsn_encrypted" in cols else "db_connection_string"
    conn.execute(
        text(f"UPDATE tenants SET {dsn_col} = :dsn, status = 'active' WHERE tenant_id = :tid"),
        {"dsn": encrypted, "tid": tenant_id},
    )
    conn.commit()
    print(f"Updated tenant {tenant_id} with encrypted DSN in column '{dsn_col}'")
master_engine.dispose()

print(f"\nDone! Tenant database for {tenant_id} is ready.")
