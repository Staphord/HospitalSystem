import sys
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, AsyncMock, patch

# Inject mock for app.db.master to avoid PostgreSQL connection check on import
sys.modules["app.db.master"] = MagicMock()

import pytest
from contextlib import asynccontextmanager
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.core.database import get_db
from app.core.security import get_current_active_user, TokenPayload
from app.models import Tenant, User, SubscriptionPlan, Subscription, Invoice
from app.main import app

@asynccontextmanager
async def dummy_lifespan(app):
    yield

app.router.lifespan_context = dummy_lifespan


@pytest.fixture(name="db_session")
def fixture_db_session():
    engine = create_engine("sqlite:///file:testreqdb?mode=memory&cache=shared", connect_args={"check_same_thread": False, "uri": True})
    connection = engine.connect()
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        connection.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# Mock users helper
async def mock_hospital_admin():
    return TokenPayload(
        sub="00000000-0000-0000-0000-000000000001",
        preferred_username="hospadmin",
        email="hospadmin@example.com",
        realm_access={"roles": ["hospital_admin"]},
        raw={"type": "tenant", "role": "hospital_admin", "tenant_id": "hosp-1"}
    )


async def mock_superadmin():
    return TokenPayload(
        sub="de305d54-75b4-431b-adb2-eb6b9e546014",
        preferred_username="superadmin",
        email="superadmin@example.com",
        realm_access={"roles": ["super_admin"]},
        raw={"type": "superadmin", "role": "super_admin"}
    )


# Test list my invoices
def test_list_my_invoices_api(client, db_session):
    app.dependency_overrides[get_current_active_user] = mock_hospital_admin

    tenant = Tenant(
        tenant_id="hosp-1",
        hospital_name="Hospital 1",
        status="active",
        subscription_plan="basic",
        db_connection_string="dummy",
        created_by=uuid.uuid4(),
    )
    db_session.add(tenant)

    invoice = Invoice(
        invoice_id=uuid.uuid4(),
        tenant_id="hosp-1",
        subscription_id=uuid.uuid4(),
        invoice_number="INV-001",
        billing_period_start=date(2026, 1, 1),
        billing_period_end=date(2026, 1, 31),
        plan_name="basic",
        amount=100,
        due_date=date(2026, 12, 31),
        status="unpaid",
    )
    invoice_other = Invoice(
        invoice_id=uuid.uuid4(),
        tenant_id="hosp-2",
        subscription_id=uuid.uuid4(),
        invoice_number="INV-002",
        billing_period_start=date(2026, 1, 1),
        billing_period_end=date(2026, 1, 31),
        plan_name="basic",
        amount=100,
        due_date=date(2026, 12, 31),
        status="unpaid",
    )
    db_session.add_all([invoice, invoice_other])
    db_session.commit()

    response = client.get("/api/v1/tenant/subscription/invoices")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["invoice_number"] == "INV-001"


# Test toggle auto renew
def test_toggle_auto_renew_api(client, db_session):
    app.dependency_overrides[get_current_active_user] = mock_hospital_admin

    tenant = Tenant(
        tenant_id="hosp-1",
        hospital_name="Hospital 1",
        status="active",
        subscription_plan="basic",
        db_connection_string="dummy",
        created_by=uuid.uuid4(),
        auto_renew=True,
    )
    db_session.add(tenant)

    plan = SubscriptionPlan(
        plan_id=uuid.uuid4(),
        plan_name="basic",
        monthly_price=100,
        annual_price=1000,
    )
    db_session.add(plan)

    sub = Subscription(
        subscription_id=uuid.uuid4(),
        tenant_id="hosp-1",
        plan_id=plan.plan_id,
        billing_cycle="monthly",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status="active",
        auto_renew=True,
    )
    db_session.add(sub)
    db_session.commit()

    response = client.patch("/api/v1/tenant/subscription/auto-renew", json={"auto_renew": False})
    assert response.status_code == 200
    data = response.json()
    assert data["subscription"]["auto_renew"] is False

    db_session.refresh(sub)
    assert sub.auto_renew is False
    db_session.refresh(tenant)
    assert tenant.auto_renew is False


# Test request cancellation and approval
def test_request_cancellation_and_approval_api(client, db_session):
    tenant = Tenant(
        tenant_id="hosp-1",
        hospital_name="Hospital 1",
        status="active",
        subscription_plan="basic",
        db_connection_string="dummy",
        created_by=uuid.uuid4(),
    )
    db_session.add(tenant)
    db_session.commit()

    # Request cancellation as hospital admin
    app.dependency_overrides[get_current_active_user] = mock_hospital_admin
    response = client.post("/api/v1/tenant/subscription/request-cancellation", json={"reason": "Budget cut"})
    assert response.status_code == 200
    data = response.json()
    assert data["pending_action"] == "cancellation"
    assert data["request_reason"] == "Budget cut"

    # Verify list pending requests as superadmin
    app.dependency_overrides[get_current_active_user] = mock_superadmin
    response_list = client.get("/api/v1/superadmin/subscription-requests")
    assert response_list.status_code == 200
    requests_data = response_list.json()
    assert len(requests_data) == 1
    assert requests_data[0]["tenant_id"] == "hosp-1"

    # Approve request
    with patch("app.services.subscription_service.terminate_tenant") as mock_terminate:
        response_approve = client.post("/api/v1/superadmin/subscription-requests/hosp-1/approve", json={"notes": "Approved cancellation"})
        assert response_approve.status_code == 200
        assert mock_terminate.call_count == 1
