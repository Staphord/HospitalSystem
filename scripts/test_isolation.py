# Test tenant database isolation
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DATABASE_URL"] = "postgresql://postgres:nasr@localhost:5432/hospital-master"
os.environ["DB_ADMIN_URL"] = "postgresql://postgres:nasr@localhost:5432/postgres"
os.environ["TENANT_DB_TEMPLATE"] = "postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}"
os.environ["TENANT_DB_ENCRYPTION_KEY"] = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="

from sqlalchemy import create_engine, text

# Test: Verify a tenant database exists and has the user
print("=== Testing Tenant Database Isolation ===")
print()

# 1. Check all databases
engine = create_engine("postgresql://postgres:nasr@localhost:5432/postgres")
with engine.connect() as conn:
    result = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'tenant_%' ORDER BY datname"))
    tenant_dbs = [r[0] for r in result]
    
    print(f"Tenant databases found: {len(tenant_dbs)}")
    for db in tenant_dbs:
        print(f"  - {db}")
    
    if not tenant_dbs:
        print("  No tenant databases found. Signup a new hospital first.")
        sys.exit(0)

print()

# 2. Check each tenant database for users
for db_name in tenant_dbs:
    tenant_engine = create_engine(f"postgresql://postgres:nasr@localhost:5432/{db_name}")
    try:
        with tenant_engine.connect() as conn:
            # Check if users table exists
            result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'users'
            """))
            if result.fetchone():
                # Check users
                result = conn.execute(text("SELECT COUNT(*) FROM users"))
                count = result.scalar()
                print(f"  {db_name}: {count} users")
                
                if count > 0:
                    result = conn.execute(text("SELECT username, email, role FROM users"))
                    for row in result:
                        print(f"    - {row.username} ({row.email}) - {row.role}")
            else:
                print(f"  {db_name}: No users table yet")
    except Exception as e:
        print(f"  {db_name}: Error - {e}")
    finally:
        tenant_engine.dispose()

print()

# 3. Check master database for tenant users
print("=== Master Database (should NOT have tenant users) ===")
master_engine = create_engine("postgresql://postgres:nasr@localhost:5432/hospital-master")
with master_engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM users WHERE hospital_id LIKE 'hosp-%'"))
    count = result.scalar()
    print(f"  Tenant users in master DB: {count}")
    
    if count > 0:
        print("  WARNING: These users should be in tenant databases!")
        result = conn.execute(text("SELECT username, hospital_id FROM users WHERE hospital_id LIKE 'hosp-%'"))
        for row in result:
            print(f"    - {row.username} ({row.hospital_id})")
    else:
        print("  [OK] No tenant users in master database")

print()
print("=== Test Complete ===")
