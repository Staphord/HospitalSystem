import pytest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"

from app.db.base import Base
from app.models.master import Tenant, GlobalAuditLog
from app.models.saas import Subscription as SubscriptionRecord, SubscriptionPlan as SubscriptionPlanModel
from app.services.subscription_plans import BillingCycle, SubscriptionPlan, SubscriptionStatus
from app.services.subscription_request_service import (
    create_plan_change_request,
    create_cancellation_request,
    approve_request,
    reject_request,
    get_pending_request,
)
from app.services.subscription_service import get_subscription_state

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def register_sqlite_functions(dbapi_connection, connection_record):
        dbapi_connection.create_function("to_jsonb", 1, lambda x: x)

    Base.metadata.create_all(
        bind=engine,
        tables=[
            Tenant.__table__,
            GlobalAuditLog.__table__,
            SubscriptionPlanModel.__table__,
            SubscriptionRecord.__table__,
        ],
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_tenant(db, **overrides):
    defaults = {
        "tenant_id": "hosp-test-reqs",
        "hospital_name": "Test Hospital",
        "db_connection_string": "enc",
        "status": "active",
        "is_active": True,
        "created_by": uuid.uuid4(),
        "subscription_plan": "basic",
        "subscription_status": SubscriptionStatus.ACTIVE.value,
        "subscription_billing_cycle": BillingCycle.MONTHLY.value,
        "subscription_start": datetime.now(timezone.utc) - timedelta(days=5),
        "subscription_end": datetime.now(timezone.utc) + timedelta(days=25),
    }
    defaults.update(overrides)
    tenant = Tenant(**defaults)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_create_plan_change_request(db):
    tenant = _make_tenant(db)
    # Upgrade basic -> standard
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="upgrade",
        requested_plan="standard",
        reason="Need more features",
    )
    db.commit()

    assert tenant.pending_action == "upgrade"
    assert tenant.requested_plan == "standard"
    assert tenant.request_reason == "Need more features"
    assert tenant.requested_at is not None


def test_create_cancellation_request(db):
    tenant = _make_tenant(db)
    create_cancellation_request(
        db,
        tenant_id=tenant.tenant_id,
        reason="Closing business",
    )
    db.commit()

    assert tenant.pending_action == "cancellation"
    assert tenant.requested_plan is None
    assert tenant.request_reason == "Closing business"


def test_approve_plan_change_request(db):
    tenant = _make_tenant(db)
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="upgrade",
        requested_plan="standard",
        reason="Need more features",
    )
    db.commit()

    super_admin_id = str(uuid.uuid4())
    approve_request(
        db,
        tenant_id=tenant.tenant_id,
        reviewer_sub=super_admin_id,
        notes="Approved upgrade",
    )
    db.commit()

    # The plan change should now be active
    assert tenant.subscription_plan == "standard"
    assert tenant.pending_action is None
    assert tenant.requested_plan is None
    assert tenant.reviewed_by is not None
    assert str(tenant.reviewed_by) == super_admin_id
    assert tenant.review_notes == "Approved upgrade"


def test_reject_plan_change_request(db):
    tenant = _make_tenant(db)
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="upgrade",
        requested_plan="standard",
        reason="Need more features",
    )
    db.commit()

    super_admin_id = str(uuid.uuid4())
    reject_request(
        db,
        tenant_id=tenant.tenant_id,
        reviewer_sub=super_admin_id,
        notes="Not approved",
    )
    db.commit()

    # Plan should remain basic, pending request cleared
    assert tenant.subscription_plan == "basic"
    assert tenant.pending_action is None
    assert tenant.reviewed_by is not None
    assert str(tenant.reviewed_by) == super_admin_id
    assert tenant.review_notes == "Not approved"


def test_list_subscription_requests_with_history(db):
    tenant = _make_tenant(db)
    
    # Create request 1
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="upgrade",
        requested_plan="standard",
        reason="Test first request",
    )
    db.commit()
    
    # Approve request 1
    approve_request(
        db,
        tenant_id=tenant.tenant_id,
        reviewer_sub="admin-1",
        notes="First request approved",
    )
    db.commit()
    
    # Create request 2 (pending)
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="downgrade",
        requested_plan="basic",
        reason="Test second request",
    )
    db.commit()
    
    from app.services.subscription_request_service import list_subscription_requests
    
    # 1. List all requests
    all_reqs = list_subscription_requests(db)
    assert len(all_reqs) == 2
    
    # 2. Filter by status 'pending'
    pending_reqs = list_subscription_requests(db, status="pending")
    assert len(pending_reqs) == 1
    assert pending_reqs[0]["pending_action"] == "downgrade"
    
    # 3. Filter by status 'approved'
    approved_reqs = list_subscription_requests(db, status="approved")
    assert len(approved_reqs) == 1
    assert approved_reqs[0]["pending_action"] == "upgrade"
    assert approved_reqs[0]["review_notes"] == "First request approved"


def test_create_same_plan_billing_cycle_upgrade_request(db):
    tenant = _make_tenant(db, subscription_plan="basic", subscription_billing_cycle="monthly")
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="upgrade",
        requested_plan="basic",
        requested_billing_cycle="annual",
        reason="Upgrading to annual",
    )
    db.commit()

    assert tenant.pending_action == "upgrade"
    assert tenant.requested_plan == "basic"
    assert tenant.subscription_metadata.get("requested_billing_cycle") == "annual"


def test_create_same_plan_billing_cycle_downgrade_request(db):
    tenant = _make_tenant(db, subscription_plan="basic", subscription_billing_cycle="annual")
    create_plan_change_request(
        db,
        tenant_id=tenant.tenant_id,
        action="downgrade",
        requested_plan="basic",
        requested_billing_cycle="monthly",
        reason="Downgrading to monthly",
    )
    db.commit()

    assert tenant.pending_action == "downgrade"
    assert tenant.requested_plan == "basic"
    assert tenant.subscription_metadata.get("requested_billing_cycle") == "monthly"


def test_create_identical_plan_change_request_fails(db):
    tenant = _make_tenant(db, subscription_plan="basic", subscription_billing_cycle="monthly")
    with pytest.raises(Exception) as exc_info:
        create_plan_change_request(
            db,
            tenant_id=tenant.tenant_id,
            action="upgrade",
            requested_plan="basic",
            requested_billing_cycle="monthly",
            reason="Same plan and cycle",
        )
    assert "identical" in str(exc_info.value.detail).lower()


