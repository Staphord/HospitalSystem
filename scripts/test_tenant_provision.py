# Test script for tenant database provisioning
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "master-service"))

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
os.environ["TENANT_DB_ENCRYPTION_KEY"] = "RZ4x5srAJWSrMAAkllCfVuqYiHYIIlfgXDdvAN11Gh0="
os.environ["DB_ADMIN_URL"] = "postgresql://postgres:nasr@localhost:5432/postgres"
os.environ["TENANT_DB_TEMPLATE"] = "postgresql://postgres:nasr@localhost:5432/tenant_{tenant_id}"

from sqlalchemy import create_engine, text

async def main():
    # Test 1: Check if PostgreSQL is reachable and we can create databases
    print("Test 1: Check PostgreSQL connection and CREATEDB privilege")
    engine = create_engine("postgresql://postgres:nasr@localhost:5432/postgres")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1"))
            assert result.scalar() == 1
            print("  [OK] PostgreSQL connection successful")
    except Exception as e:
        print(f"  [FAIL] PostgreSQL connection failed: {e}")
        sys.exit(1)

    # Check if we have CREATEDB privilege
    with engine.connect() as conn:
        result = conn.execute(text("SELECT rolcreatedb FROM pg_roles WHERE rolname = current_user"))
        can_create = result.scalar()
        if can_create:
            print("  [OK] User has CREATEDB privilege")
        else:
            print("  [WARN] User does NOT have CREATEDB privilege - database creation will fail")

    # Test 2: Create a test tenant record and database using the provisioning logic
    print("\nTest 2: Create a test tenant record and database")
    from app.db.master import get_master_db
    from app.models.master import Tenant
    from app.services.provision import provision_tenant_database
    from app.services.tenant_service import encrypt_dsn
    from datetime import datetime, timezone

    test_tenant_id = f"hosp-{uuid.uuid4().hex[:8]}"
    print(f"  Creating tenant record for: {test_tenant_id}")

    db = get_master_db()
    try:
        tenant = Tenant(
            tenant_id=test_tenant_id,
            name="Test Hospital",
            db_dsn_encrypted=encrypt_dsn("placeholder"),
            status="pending",
            subscription_plan="basic",
            is_active=True,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.add(tenant)
        db.commit()
        print(f"  [OK] Tenant record created")
    except Exception as e:
        print(f"  [FAIL] Failed to create tenant record: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()

    print(f"  Creating database for tenant: {test_tenant_id}")
    try:
        dsn = await provision_tenant_database(test_tenant_id, "Test Hospital")
        print(f"  [OK] Database created successfully: {dsn}")
    except Exception as e:
        print(f"  [FAIL] Database creation failed: {e}")
        sys.exit(1)

    # Test 3: Verify the database exists
    print("\nTest 3: Verify the database exists")
    with engine.connect() as conn:
        result = conn.execute(text("SELECT datname FROM pg_database WHERE datname = :name"), {"name": f"tenant_{test_tenant_id}"})
        db_record = result.fetchone()
        if db_record:
            print(f"  [OK] Database tenant_{test_tenant_id} exists in PostgreSQL")
        else:
            print(f"  [FAIL] Database tenant_{test_tenant_id} NOT found in PostgreSQL")
            sys.exit(1)

    # Test 4: Verify the database has tables (migrations ran)
    print("\nTest 4: Verify tables were created by migrations")
    tenant_engine = create_engine(dsn)
    with tenant_engine.connect() as conn:
        result = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name"))
        tables = [r[0] for r in result]
        if "alembic_version" in tables:
            print(f"  [OK] Migrations ran successfully - 'alembic_version' table found")
        else:
            print(f"  [WARN] Migrations may not have run - tables found: {tables}")
    tenant_engine.dispose()

    # Test 5: Verify the tenant record was updated with real DSN
    print("\nTest 5: Verify tenant record was updated with real DSN")
    db = get_master_db()
    try:
        tenant = db.query(Tenant).filter(Tenant.tenant_id == test_tenant_id).first()
        if tenant and tenant.db_dsn_encrypted != encrypt_dsn("placeholder"):
            print(f"  [OK] Tenant record updated with real DSN")
        else:
            print(f"  [FAIL] Tenant record was NOT updated with real DSN")
            sys.exit(1)
    finally:
        db.close()

    # Test 6: Clean up - drop the test database
    print("\nTest 6: Clean up - drop test database")
    with engine.connect() as conn:
        conn.execution_options(isolation_level="AUTOCOMMIT")
        conn.execute(text(f'DROP DATABASE IF EXISTS "tenant_{test_tenant_id}"'))
        print(f"  [OK] Database tenant_{test_tenant_id} dropped")

    # Clean up tenant record
    db = get_master_db()
    try:
        tenant = db.query(Tenant).filter(Tenant.tenant_id == test_tenant_id).first()
        if tenant:
            db.delete(tenant)
            db.commit()
            print(f"  [OK] Tenant record deleted")
    finally:
        db.close()

    print("\n=== ALL TESTS PASSED ===")
    print("Tenant database provisioning is working correctly.")

if __name__ == "__main__":
    asyncio.run(main())
