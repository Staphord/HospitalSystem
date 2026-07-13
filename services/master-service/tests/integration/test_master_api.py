import sys
from unittest.mock import MagicMock, AsyncMock, patch

# Inject a mock for app.db.master to avoid PostgreSQL connection check on import
mock_db_master = MagicMock()
sys.modules["app.db.master"] = mock_db_master

import pytest
from contextlib import asynccontextmanager
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB

# Register JSONB type compiler for SQLite compatibility
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

from app.db.base import Base
from app.core.database import get_db
from app.core.security import get_current_active_user, TokenPayload
from app.models import (
    User,
    Tenant,
    GlobalAuditLog,
    SuperAdmin,
    SubscriptionPlan,
    Subscription,
    Invoice,
    SaaSPayment,
    SuperAdminAuditLog,
    Announcement,
    SubscriptionAuditLog,
)
from app.models.auth import RefreshToken


# Override lifespan to bypass external dependencies during testing
@asynccontextmanager
async def dummy_lifespan(app):
    yield

from app.main import app
app.router.lifespan_context = dummy_lifespan


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine("sqlite:///file:testdb?mode=memory&cache=shared", connect_args={"check_same_thread": False, "uri": True})
    connection = engine.connect()
    
    # Create tables used in integration tests
    Base.metadata.create_all(bind=engine)
    
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        connection.close()


async def override_get_current_active_user():
    return TokenPayload(
        sub="test-superadmin-sub",
        preferred_username="superadmin",
        email="superadmin@example.com",
        realm_access={"roles": ["super_admin"]},
        raw={"type": "superadmin", "role": "super_admin"}
    )


@pytest.fixture
def client(db_session):
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_active_user] = override_get_current_active_user

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def mock_external_services():
    with patch("app.services.keycloak_realm.setup_tenant_realm", new_callable=AsyncMock), \
         patch("app.services.keycloak_realm.verify_tenant_realm_exists", new_callable=AsyncMock, return_value=True), \
         patch("app.services.keycloak_admin.ensure_roles", new_callable=AsyncMock), \
         patch("app.services.keycloak_admin.create_keycloak_user", new_callable=AsyncMock, return_value="kc-dummy-sub"), \
         patch("app.services.keycloak_admin.create_local_user") as mock_create_local, \
         patch("app.services.keycloak_admin.set_user_attribute", new_callable=AsyncMock), \
         patch("app.services.provision.provision_tenant_database_sync", return_value="sqlite:///:memory:"), \
         patch("app.services.provision.get_tenant_db_session") as mock_get_sess, \
         patch("app.services.tenant_service.cache_tenant_suspension", new_callable=AsyncMock), \
         patch("app.services.tenant_service.remove_tenant_suspension_cache", new_callable=AsyncMock), \
         patch("app.services.tenant_service._revoke_keycloak_sessions", new_callable=AsyncMock):
        
        # Make the mock tenant DB session return a clean connection to prevent real pg access
        mock_sess = MagicMock()
        mock_get_sess.return_value = mock_sess
        yield


# Test creating a tenant
def test_create_tenant(client, db_session):
    payload = {
        "hospital_name": "Test General Hospital",
        "admin_username": "test_admin",
        "admin_password": "SecurePassword123!",
        "admin_email": "admin@testgeneral.org",
        "admin_full_name": "Dr. Test Admin",
        "country": "Kenya",
        "city": "Nairobi",
        "timezone": "Africa/Nairobi",
        "currency": "KES"
    }
    response = client.post("/api/v1/superadmin/tenants", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["hospital_name"] == "Test General Hospital"
    assert data["status"] == "trial"
    assert data["is_active"] is True

    # Verify tenant exists in database
    tenant = db_session.query(Tenant).filter(Tenant.tenant_id == data["tenant_id"]).first()
    assert tenant is not None
    assert tenant.country == "Kenya"


# Test listing tenants
def test_list_tenants(client, db_session):
    import uuid
    tenant1 = Tenant(tenant_id="t1", hospital_name="Hosp 1", is_active=True, status="active", subscription_plan="basic", db_connection_string="dummy", created_by=uuid.uuid4())
    tenant2 = Tenant(tenant_id="t2", hospital_name="Hosp 2", is_active=False, status="suspended", subscription_plan="standard", db_connection_string="dummy", created_by=uuid.uuid4())
    db_session.add_all([tenant1, tenant2])
    db_session.commit()

    response = client.get("/api/v1/superadmin/tenants")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert any(t["tenant_id"] == "t1" for t in data)
    assert any(t["tenant_id"] == "t2" for t in data)


# Test fetching tenant details
def test_get_tenant(client, db_session):
    import uuid
    tenant = Tenant(tenant_id="t1", hospital_name="Hosp 1", is_active=True, status="active", subscription_plan="basic", db_connection_string="dummy", created_by=uuid.uuid4())
    db_session.add(tenant)
    db_session.commit()

    response = client.get("/api/v1/superadmin/tenants/t1")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "t1"
    assert data["hospital_name"] == "Hosp 1"


# Test updating tenant status via PATCH
def test_update_tenant_status(client, db_session):
    import uuid
    tenant = Tenant(
        tenant_id="t1",
        hospital_name="Hosp 1",
        is_active=True,
        status="active",
        subscription_plan="basic",
        subscription_status="active",
        subscription_start=None,
        subscription_end=None,
        db_connection_string="dummy",
        created_by=uuid.uuid4(),
    )
    db_session.add(tenant)
    db_session.commit()

    # Suspend tenant
    response = client.patch("/api/v1/superadmin/tenants/t1", json={"status": "suspended"})
    assert response.status_code == 200
    db_session.refresh(tenant)
    assert tenant.status == "suspended"
    assert tenant.is_active is False
    assert tenant.suspended_reason == "Status updated via PATCH update"

    # Verify audit log was written
    logs = db_session.query(GlobalAuditLog).filter(GlobalAuditLog.tenant_id == "t1").all()
    assert len(logs) > 0
    assert any("tenant.suspend" in log.action for log in logs)

    # Reactivate tenant
    response = client.patch("/api/v1/superadmin/tenants/t1", json={"status": "active"})
    assert response.status_code == 200
    db_session.refresh(tenant)
    assert tenant.status == "active"
    assert tenant.is_active is True

    # Terminate tenant
    response = client.patch("/api/v1/superadmin/tenants/t1", json={"status": "terminated"})
    assert response.status_code == 200
    db_session.refresh(tenant)
    assert tenant.status == "terminated"
    assert tenant.is_active is False


# Test fetching tenant subscription state
def test_get_tenant_subscription_state(client, db_session):
    import uuid
    tenant = Tenant(tenant_id="t1", hospital_name="Hosp 1", is_active=True, status="active", subscription_plan="standard", db_connection_string="dummy", created_by=uuid.uuid4())
    db_session.add(tenant)
    db_session.commit()

    response = client.get("/api/v1/superadmin/tenants/t1/subscription")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "t1"
    assert data["subscription"]["plan"] == "standard"


# Test generating an invoice
def test_generate_invoice(client, db_session):
    import uuid
    from datetime import date
    tenant = Tenant(tenant_id="t1", hospital_name="Hosp 1", is_active=True, status="active", subscription_plan="standard", db_connection_string="dummy", created_by=uuid.uuid4())
    db_session.add(tenant)
    
    plan = SubscriptionPlan(
        plan_id=uuid.uuid4(),
        plan_name="standard",
        monthly_price=1500,
        annual_price=15000,
    )
    db_session.add(plan)

    sub = Subscription(
        subscription_id=uuid.uuid4(),
        tenant_id="t1",
        plan_id=plan.plan_id,
        billing_cycle="monthly",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status="active"
    )
    db_session.add(sub)
    db_session.commit()

    payload = {
        "amount": 1500,
        "due_date": "2026-12-31",
        "billing_period_start": "2026-01-01",
        "billing_period_end": "2026-01-31",
        "plan_name": "standard"
    }
    response = client.post("/api/v1/superadmin/tenants/t1/invoices", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert float(data["amount"]) == 1500
    assert data["status"] == "unpaid"

    # Verify invoice in DB
    invoice = db_session.query(Invoice).filter(Invoice.invoice_id == uuid.UUID(data["invoice_id"])).first()
    assert invoice is not None
    assert invoice.tenant_id == "t1"
    
    # Assert generated invoice number format (e.g. ASUPE-T1-yymmdd-RAND)
    assert invoice.invoice_number is not None
    assert invoice.invoice_number.startswith("ASUPE-T1-")
    assert len(invoice.invoice_number) <= 64
    assert len(invoice.invoice_number) >= 15


# Test recording a payment
def test_record_payment(client, db_session):
    import uuid
    from datetime import date, datetime

    admin = SuperAdmin(
        super_admin_id=uuid.UUID("de305d54-75b4-431b-adb2-eb6b9e546014"),
        username="superadmin",
        email="superadmin@example.com",
        password_hash="dummy",
        full_name="Super Admin",
        role="super_admin",
        mfa_secret="dummy",
        mfa_enabled=False,
        created_at=datetime.utcnow(),
    )
    db_session.add(admin)

    tenant = Tenant(
        tenant_id="t1",
        hospital_name="Hosp 1",
        is_active=True,
        status="active",
        subscription_plan="standard",
        db_connection_string="dummy",
        created_by=uuid.UUID("de305d54-75b4-431b-adb2-eb6b9e546014"),
    )
    db_session.add(tenant)

    plan = SubscriptionPlan(
        plan_id=uuid.uuid4(),
        plan_name="standard",
        monthly_price=1500,
        annual_price=15000,
    )
    db_session.add(plan)

    sub = Subscription(
        subscription_id=uuid.uuid4(),
        tenant_id="t1",
        plan_id=plan.plan_id,
        billing_cycle="monthly",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status="active"
    )
    db_session.add(sub)

    invoice = Invoice(
        invoice_id=uuid.uuid4(),
        tenant_id="t1",
        subscription_id=sub.subscription_id,
        invoice_number="INV-2026-001",
        billing_period_start=date(2026, 1, 1),
        billing_period_end=date(2026, 1, 31),
        plan_name="standard",
        amount=1500,
        due_date=date(2026, 12, 31),
        status="unpaid"
    )
    db_session.add(invoice)
    db_session.commit()

    payload = {
        "invoice_id": invoice.id,
        "amount": 1500,
        "payment_method": "card",
        "reference_number": "REF123"
    }
    response = client.post("/api/v1/superadmin/tenants/t1/payments", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["amount"] == 1500

    # Verify payment and invoice state
    payment = db_session.query(SaaSPayment).filter(SaaSPayment.id == data["id"]).first()
    assert payment is not None
    db_session.refresh(invoice)
    assert invoice.status == "paid"


# Test system health endpoint
def test_system_health(client):
    with patch("httpx.AsyncClient.get") as mock_get:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"status": "ok"}
        mock_get.return_value = mock_response

        response = client.get("/api/v1/superadmin/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy" or data["status"] == "unhealthy"


# Test announcements endpoints
def test_announcements(client, db_session):
    # Create announcement
    payload = {
        "title": "System Update",
        "message": "Scheduled maintenance",
        "type": "maintenance",
        "scope": "all",
        "display_format": "banner"
    }
    response = client.post("/api/v1/superadmin/announcements", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "System Update"

    # List announcements
    response = client.get("/api/v1/superadmin/announcements")
    assert response.status_code == 200
    announcements = response.json()
    assert len(announcements) == 1
    assert announcements[0]["title"] == "System Update"


# Test fetching global audit logs
def test_global_audit_logs(client, db_session):
    log = GlobalAuditLog(tenant_id="t1", action="tenant.suspend", detail="{}")
    db_session.add(log)
    db_session.commit()

    response = client.get("/api/v1/superadmin/audit-log")
    assert response.status_code == 200
    logs = response.json()
    assert len(logs) == 1
    assert logs[0]["action"] == "tenant.suspend"
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker
from datetime import datetime, timedelta, timezone

from app.main import app
from app.core.database import get_db
from app.core.security import get_current_active_user, TokenPayload
# Test list super admin sessions
def test_list_super_admin_sessions(client, db_session):
    import uuid
    from datetime import datetime, timezone, timedelta
    db = db_session

    # 1. Create a super admin user
    superadmin_user = User(
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        username="superadmin",
        full_name="Super Admin",
        email="superadmin@example.com",
        role="super_admin",
        is_active=True
    )
    db.add(superadmin_user)

    # 2. Create another standard user (should not appear in superadmin session management)
    normal_user = User(
        keycloak_sub="normal-sub",
        username="normaluser",
        full_name="Normal User",
        email="normal@example.com",
        role="doctor",
        is_active=True
    )
    db.add(normal_user)

    # 3. Create active sessions for superadmin (one normal, one impersonation) and normal user
    now = datetime.now(timezone.utc)
    
    session1 = RefreshToken(
        session_id="session-1",
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        refresh_token_hash="tokenhash1",
        expires_at=now + timedelta(hours=1),
        is_revoked=False,
        created_at=now
    )
    db.add(session1)

    session2 = RefreshToken(
        session_id="session-2",
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        refresh_token_hash="impersonation:tenant-abc",
        expires_at=now + timedelta(hours=2),
        is_revoked=False,
        created_at=now
    )
    db.add(session2)

    session3 = RefreshToken(
        session_id="session-3",
        keycloak_sub="normal-sub",
        refresh_token_hash="tokenhash3",
        expires_at=now + timedelta(hours=1),
        is_revoked=False,
        created_at=now
    )
    db.add(session3)

    admin = SuperAdmin(
        super_admin_id=uuid.UUID("de305d54-75b4-431b-adb2-eb6b9e546014"),
        username="superadmin",
        email="superadmin@example.com",
        password_hash="dummy",
        full_name="Super Admin",
        role="super_admin",
        mfa_secret="dummy",
        mfa_enabled=False,
        created_at=now
    )
    db.add(admin)

    tenant = Tenant(
        tenant_id="tenant-abc",
        hospital_name="Test Tenant Hospital",
        db_connection_string="encrypted-dsn",
        status="active",
        is_active=True,
        created_by=uuid.UUID("de305d54-75b4-431b-adb2-eb6b9e546014"),
    )
    db.add(tenant)

    db.commit()

    # Request the sessions list
    response = client.get("/api/v1/superadmin/sessions")
    assert response.status_code == 200
    data = response.json()
    
    # Check that normal user session is NOT listed, and superadmin's 2 sessions are
    assert len(data) == 2
    
    # Check session-1 details
    s1 = next(s for s in data if s["id"] == "session-1")
    assert s1["username"] == "superadmin"
    assert s1["is_impersonation"] is False
    assert s1["impersonation_tenant_id"] is None
    
    # Check session-2 details (impersonation)
    s2 = next(s for s in data if s["id"] == "session-2")
    assert s2["username"] == "superadmin"
    assert s2["is_impersonation"] is True
    assert s2["impersonation_tenant_id"] == "tenant-abc"
    assert s2["impersonation_tenant_name"] == "Test Tenant Hospital"


def test_revoke_single_session(client, db_session):
    from datetime import datetime, timezone, timedelta
    db = db_session

    superadmin_user = User(
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        username="superadmin",
        role="super_admin",
        is_active=True
    )
    db.add(superadmin_user)

    now = datetime.now(timezone.utc)
    session1 = RefreshToken(
        session_id="session-1",
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        refresh_token_hash="tokenhash1",
        expires_at=now + timedelta(hours=1),
        is_revoked=False,
        created_at=now
    )
    db.add(session1)
    db.commit()

    # Revoke single session
    response = client.delete("/api/v1/superadmin/sessions/session-1")
    assert response.status_code == 204

    # Verify database update
    db.refresh(session1)
    assert session1.is_revoked is True


def test_revoke_all_sessions(client, db_session):
    from datetime import datetime, timezone, timedelta
    db = db_session

    superadmin_user = User(
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        username="superadmin",
        role="super_admin",
        is_active=True
    )
    db.add(superadmin_user)

    now = datetime.now(timezone.utc)
    session1 = RefreshToken(
        session_id="session-1",
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        refresh_token_hash="tokenhash1",
        expires_at=now + timedelta(hours=1),
        is_revoked=False,
        created_at=now
    )
    session2 = RefreshToken(
        session_id="session-2",
        keycloak_sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        refresh_token_hash="tokenhash2",
        expires_at=now + timedelta(hours=1),
        is_revoked=False,
        created_at=now
    )
    db.add(session1)
    db.add(session2)
    db.commit()

    # Revoke all
    response = client.delete("/api/v1/superadmin/sessions")
    assert response.status_code == 204

    # Verify both are revoked
    db.refresh(session1)
    db.refresh(session2)
    assert session1.is_revoked is True
    assert session2.is_revoked is True


def test_create_superadmin_user(client, db_session):
    payload = {
        "username": "new_superadmin_test",
        "email": "new_test_admin@example.com",
        "password": "SecureP@ss123!",
        "full_name": "New Test Admin",
        "role": "super_admin"
    }

    # Patch settings to simulate configured SMTP and patch aiosmtplib.send to verify calling
    with patch("app.api.v1.superadmin.router.settings.smtp_user", "smtp_user"), \
         patch("app.api.v1.superadmin.router.settings.smtp_password", "smtp_pass"), \
         patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send_email:
        
        response = client.post("/api/v1/superadmin/users", json=payload)
        
        assert response.status_code == 201
        data = response.json()
        assert data["username"] == "new_superadmin_test"
        assert data["email"] == "new_test_admin@example.com"
        
        # Verify email dispatch was triggered
        assert mock_send_email.call_count == 1
        sent_msg = mock_send_email.call_args[0][0]
        assert sent_msg["To"] == "new_test_admin@example.com"
        assert "Welcome to HospitalFlow - Platform Administrator Credentials" in sent_msg["Subject"]
