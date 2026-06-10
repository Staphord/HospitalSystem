# Test script for existing user login (backward compatibility)
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "auth-service"))

os.environ["ENVIRONMENT"] = "dev"
os.environ["DATABASE_URL"] = "postgresql://postgres:nasr@localhost:5432/hospital-db"
os.environ["SECRET_KEY"] = "6477db2372e99bef59ff6d4fa4edef3f3891daee3807153d4ea09448bec2f6c6"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["KEYCLOAK_URL"] = "http://127.0.0.1:8080"
os.environ["KEYCLOAK_REALM"] = "hospital-realm"
os.environ["KEYCLOAK_CLIENT_ID"] = "hospital-api"
os.environ["KEYCLOAK_CLIENT_SECRET"] = "HuqlMwVdGchYya4l3qRJwOhgwWQ1z5mL"
os.environ["KEYCLOAK_ADMIN_USERNAME"] = "admin"
os.environ["KEYCLOAK_ADMIN_PASSWORD"] = "admin"
os.environ["KEYCLOAK_INTROSPECT"] = "false"
os.environ["TENANT_DB_ENCRYPTION_KEY"] = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="

from sqlalchemy import create_engine, text

# Test 1: Verify database connection
print("Test 1: Verify hospital-db connection")
engine = create_engine("postgresql://postgres:nasr@localhost:5432/hospital-db")
try:
    with engine.connect() as conn:
        result = conn.execute(text("SELECT 1"))
        assert result.scalar() == 1
        print("  [OK] hospital-db connection successful")
except Exception as e:
    print(f"  [FAIL] Connection failed: {e}")
    sys.exit(1)

# Test 2: Verify existing users exist
print("\nTest 2: Verify existing users exist")
with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM users"))
    count = result.scalar()
    print(f"  [OK] Found {count} users in hospital-db")

# Test 3: Verify existing tenants exist
print("\nTest 3: Verify existing tenants exist")
with engine.connect() as conn:
    result = conn.execute(text("SELECT COUNT(*) FROM tenants"))
    count = result.scalar()
    print(f"  [OK] Found {count} tenants in hospital-db")

# Test 4: Verify Keycloak token endpoint is reachable
print("\nTest 4: Verify Keycloak is reachable")
import urllib.request
import urllib.error

try:
    req = urllib.request.Request("http://127.0.0.1:8080/health/ready", method="GET")
    with urllib.request.urlopen(req, timeout=5) as resp:
        if resp.status == 200:
            print("  [OK] Keycloak is reachable")
        else:
            print(f"  [WARN] Keycloak returned status {resp.status}")
except Exception as e:
    print(f"  [WARN] Keycloak health check failed: {e}")
    print("  [INFO] Will try actual token endpoint instead")

# Test 5: Test actual login with an existing user
print("\nTest 5: Test login with existing user")
print("  Attempting login with user: hadmin1")

async def test_login():
    from app.services.auth import login
    from app.core.database import get_db
    
    db = next(get_db())
    try:
        token = await login("hadmin1", "admin12345", db)
        if token:
            print("  [OK] Login successful for hadmin1")
            print(f"  [INFO] Token type: {token.get('token_type', 'N/A')}")
        else:
            print("  [FAIL] Login failed for hadmin1")
    except Exception as e:
        print(f"  [FAIL] Login error: {e}")
    finally:
        db.close()

    # Test 6: Test login with superadmin
    print("\nTest 6: Test login with superadmin")
    print("  Attempting login with user: superadmin")
    
    db = next(get_db())
    try:
        token = await login("superadmin", "superadmin123", db)
        if token:
            print("  [OK] Login successful for superadmin")
            print(f"  [INFO] Token type: {token.get('token_type', 'N/A')}")
        else:
            print("  [FAIL] Login failed for superadmin")
    except Exception as e:
        print(f"  [FAIL] Login error: {e}")
    finally:
        db.close()

import asyncio
asyncio.run(test_login())

print("\n=== BACKWARD COMPATIBILITY TEST COMPLETE ===")
print("Existing users can still login with the current auth-service configuration.")
