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

# Override lifespan to bypass external dependencies during testing
@asynccontextmanager
async def dummy_lifespan(app):
    yield

from app.main import app
app.router.lifespan_context = dummy_lifespan


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine("sqlite:///file:testdb?mode=memory&cache=shared", uri=True, connect_args={"check_same_thread": False})
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
    assert data["name"] == "Test General Hospital"
    assert data["status"] == "trial"
    assert data["is_active"] is True

    # Verify tenant exists in database
    tenant = db_session.query(Tenant).filter(Tenant.tenant_id == data["tenant_id"]).first()
    assert tenant is not None
    assert tenant.country == "Kenya"


# Test listing tenants
def test_list_tenants(client, db_session):
    tenant1 = Tenant(tenant_id="t1", name="Hosp 1", is_active=True, status="active", subscription_plan="basic")
    tenant2 = Tenant(tenant_id="t2", name="Hosp 2", is_active=False, status="suspended", subscription_plan="standard")
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
    tenant = Tenant(tenant_id="t1", name="Hosp 1", is_active=True, status="active", subscription_plan="basic")
    db_session.add(tenant)
    db_session.commit()

    response = client.get("/api/v1/superadmin/tenants/t1")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "t1"
    assert data["name"] == "Hosp 1"


# Test updating tenant status via PATCH
def test_update_tenant_status(client, db_session):
    tenant = Tenant(
        tenant_id="t1",
        name="Hosp 1",
        is_active=True,
        status="active",
        subscription_plan="basic",
        subscription_status="active",
        subscription_start=None,
        subscription_end=None,
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
    tenant = Tenant(tenant_id="t1", name="Hosp 1", is_active=True, status="active", subscription_plan="standard")
    db_session.add(tenant)
    db_session.commit()

    response = client.get("/api/v1/superadmin/tenants/t1/subscription")
    assert response.status_code == 200
    data = response.json()
    assert data["tenant_id"] == "t1"
    assert data["subscription"]["plan"] == "standard"


# Test generating an invoice
def test_generate_invoice(client, db_session):
    tenant = Tenant(tenant_id="t1", name="Hosp 1", is_active=True, status="active", subscription_plan="standard")
    db_session.add(tenant)
    db_session.commit()

    payload = {
        "amount": 1500,
        "due_date": "2026-12-31",
        "description": "Standard monthly sub"
    }
    response = client.post("/api/v1/superadmin/tenants/t1/invoices", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["amount"] == 1500
    assert data["status"] == "unpaid"

    # Verify invoice in DB
    invoice = db_session.query(Invoice).filter(Invoice.id == data["id"]).first()
    assert invoice is not None
    assert invoice.tenant_id == "t1"


# Test recording a payment
def test_record_payment(client, db_session):
    tenant = Tenant(tenant_id="t1", name="Hosp 1", is_active=True, status="active", subscription_plan="standard")
    invoice = Invoice(tenant_id="t1", amount=1500, due_date="2026-12-31", status="unpaid")
    db_session.add_all([tenant, invoice])
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
