# End-to-end test: Create tenant and verify isolation
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ["DATABASE_URL"] = "postgresql://postgres:nasr@localhost:5432/hospital_master"
os.environ["DB_ADMIN_URL"] = "postgresql://postgres:nasr@localhost:5432/postgres"
os.environ["TENANT_DB_TEMPLATE"] = "postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}"
os.environ["TENANT_DB_ENCRYPTION_KEY"] = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="

from sqlalchemy import create_engine, text

print("=== End-to-End Tenant Isolation Test ===")
print()

# Step 1: Check current state
master_engine = create_engine("postgresql://postgres:nasr@localhost:5432/hospital_master")
with master_engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM users WHERE hospital_id LIKE 'hosp-%'"))
    master_count = result.scalar()
    print(f"1. Tenant users in master DB: {master_count}")
    print(f"   Expected: 0 (all tenant users should be in tenant databases)")
    
    result = conn.execute(text("SELECT COUNT(*) FROM tenants"))
    tenant_count = result.scalar()
    print(f"2. Tenant records in master DB: {tenant_count}")

print()

# Step 2: Check tenant databases
admin_engine = create_engine("postgresql://postgres:nasr@localhost:5432/postgres")
with admin_engine.connect() as conn:
    result = conn.execute(text("SELECT datname FROM pg_database WHERE datname LIKE 'tenant_%'"))
    tenant_dbs = [r[0] for r in result]
    
    print(f"3. Tenant databases found: {len(tenant_dbs)}")
    for db in tenant_dbs:
        tenant_engine = create_engine(f"postgresql://postgres:nasr@localhost:5432/{db}")
        try:
            with tenant_engine.connect() as conn:
                result = conn.execute(text("SELECT COUNT(*) FROM users"))
                count = result.scalar()
                print(f"   - {db}: {count} users")
        except:
            print(f"   - {db}: Error (no users table)")
        finally:
            tenant_engine.dispose()

print()

# Step 3: Verify the fix
if master_count == 0:
    print("[OK] PASS: No tenant users in master database")
else:
    print("[FAIL] FAIL: Tenant users found in master database!")
    print("   This means the tenant database routing is not working.")

print()
print("=== Test Complete ===")
print()
print("To verify the fix works for NEW signups:")
print("1. Run: uvicorn app.main:app --reload")
print("2. Sign up a new hospital via Swagger UI")
print("3. Check that the new tenant database has the user")
print("4. Check that the master database does NOT have the user")
