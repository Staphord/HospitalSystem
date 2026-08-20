import pytest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import JSONB, UUID

# Register JSONB and UUID type compilers for SQLite compatibility
@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "TEXT"

@compiles(UUID, "sqlite")
def compile_uuid_sqlite(type_, compiler, **kw):
    return "CHAR(32)"

from app.db.base import Base
from app.models.master import Tenant, GlobalAuditLog
from app.models.saas import Subscription as SubscriptionRecord, SubscriptionPlan as SubscriptionPlanModel, Invoice, SaaSPayment
from app.services.subscription_plans import BillingCycle, SubscriptionPlan, SubscriptionStatus
from app.services.subscription_service import (
    SubscriptionError,
    activate_tenant,
    apply_pending_plan_changes,
    compute_prorated_amount,
    downgrade_subscription,
    get_subscription_state,
    reactivate_tenant,
    renew_subscription,
    subscribe_tenant,
    suspend_tenant,
    upgrade_subscription,
    _ensure_aware,
    _set_subscription_dates,
)
from app.services.subscription_request_service import (
    create_plan_change_request,
    create_cancellation_request,
    approve_request,
    reject_request,
    get_pending_request,
    list_subscription_requests,
    list_pending_requests,
    _audit_event,
    log_action,
)
from app.api.v1.superadmin.schemas import TenantCreate


@pytest.fixture(autouse=True)
def clear_plan_cache():
    from app.services.subscription_plans import _db_plans_cache
    _db_plans_cache.clear()
    yield
    _db_plans_cache.clear()

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    
    # Register to_jsonb on SQLite connection for compatibility
    from sqlalchemy import event
    @event.listens_for(engine, "connect")
    def register_sqlite_functions(dbapi_connection, connection_record):
        dbapi_connection.create_function("to_jsonb", 1, lambda x: x)

    # Only create the tables this test suite actually touches.
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Tenant.__table__,
            GlobalAuditLog.__table__,
            SubscriptionPlanModel.__table__,
            SubscriptionRecord.__table__,
            Invoice.__table__,
            SaaSPayment.__table__,
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
        "tenant_id": "hosp-test001",
        "hospital_name": "Test Hospital",
        "db_connection_string": "enc",
        "status": "active",
        "is_active": True,
        "created_by": uuid.uuid4(),
        "subscription_plan": "standard",
        "subscription_status": SubscriptionStatus.ACTIVE.value,
        "subscription_billing_cycle": BillingCycle.MONTHLY.value,
        "subscription_start": datetime.now(timezone.utc) - timedelta(days=10),
        "subscription_end": datetime.now(timezone.utc) + timedelta(days=20),
    }
    defaults.update(overrides)
    tenant = Tenant(**defaults)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_subscribe_paid_plan(db):
    tenant = _make_tenant(db)
    result = subscribe_tenant(
        db,
        tenant_id=tenant.tenant_id,
        plan=SubscriptionPlan.PREMIUM,
        billing_cycle=BillingCycle.ANNUAL,
    )
    assert result.tenant.subscription_plan == SubscriptionPlan.PREMIUM.value
    assert result.tenant.subscription_billing_cycle == BillingCycle.ANNUAL.value
    assert result.tenant.subscription_status == SubscriptionStatus.ACTIVE.value
    assert result.tenant.subscription_end > result.tenant.subscription_start
    assert result.tenant.auto_renew is True


def test_subscribe_trial_once_only(db):
    tenant = _make_tenant(db)
    subscribe_tenant(
        db,
        tenant_id=tenant.tenant_id,
        plan=SubscriptionPlan.FREE_TRIAL,
        billing_cycle=BillingCycle.MONTHLY,
        start_trial=True,
    )
    db.commit()
    assert tenant.has_used_trial is True
    assert tenant.subscription_status == SubscriptionStatus.TRIAL.value

    with pytest.raises(SubscriptionError) as exc:
        subscribe_tenant(
            db,
            tenant_id=tenant.tenant_id,
            plan=SubscriptionPlan.FREE_TRIAL,
            billing_cycle=BillingCycle.MONTHLY,
            start_trial=True,
        )
    assert exc.value.detail["code"] == "TRIAL_ALREADY_USED"


def test_upgrade_and_downgrade(db):
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.BASIC.value)
    upgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.STANDARD,
    )
    assert tenant.subscription_plan == SubscriptionPlan.STANDARD.value

    # Default downgrade is deferred
    downgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
    )
    assert tenant.subscription_plan == SubscriptionPlan.STANDARD.value
    assert tenant.pending_plan == SubscriptionPlan.BASIC.value

    # Immediate downgrade takes effect right away
    downgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
        effective_at_end=False,
    )
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value


def test_upgrade_must_be_higher_plan(db):
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    with pytest.raises(SubscriptionError) as exc:
        upgrade_subscription(
            db,
            tenant_id=tenant.tenant_id,
            new_plan=SubscriptionPlan.BASIC,
        )
    assert exc.value.detail["code"] == "NOT_AN_UPGRADE"


def test_downgrade_must_be_lower_plan(db):
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.BASIC.value)
    with pytest.raises(SubscriptionError) as exc:
        downgrade_subscription(
            db,
            tenant_id=tenant.tenant_id,
            new_plan=SubscriptionPlan.STANDARD,
        )
    assert exc.value.detail["code"] == "NOT_A_DOWNGRADE"


def test_renew_extends_subscription(db):
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    old_end = _ensure_aware(tenant.subscription_end)
    renew_subscription(
        db,
        tenant_id=tenant.tenant_id,
        billing_cycle=BillingCycle.ANNUAL,
    )
    assert _ensure_aware(tenant.subscription_end) > old_end
    assert tenant.subscription_status == SubscriptionStatus.ACTIVE.value


def test_suspend_requires_reason(db):
    tenant = _make_tenant(db)
    with pytest.raises(SubscriptionError) as exc:
        suspend_tenant(db, tenant_id=tenant.tenant_id, reason="   ")
    assert exc.value.detail["code"] == "MISSING_SUSPENSION_REASON"


def test_suspend_and_reactivate(db):
    tenant = _make_tenant(db)
    suspend_tenant(
        db,
        tenant_id=tenant.tenant_id,
        reason="Non-payment",
    )
    assert tenant.status == "suspended"
    assert tenant.is_active is False
    assert tenant.subscription_status == SubscriptionStatus.SUSPENDED.value
    assert tenant.suspended_reason == "Non-payment"

    reactivate_tenant(db, tenant_id=tenant.tenant_id)
    assert tenant.status == "active"
    assert tenant.is_active is True
    assert tenant.subscription_status == SubscriptionStatus.ACTIVE.value
    assert tenant.reactivated_at is not None


def test_reactivate_expired_tenant_fails(db):
    tenant = _make_tenant(
        db,
        subscription_end=datetime.now(timezone.utc) - timedelta(days=40),
        grace_period_end=datetime.now(timezone.utc) - timedelta(days=30),
    )
    with pytest.raises(SubscriptionError) as exc:
        reactivate_tenant(db, tenant_id=tenant.tenant_id)
    assert exc.value.detail["code"] == "SUBSCRIPTION_EXPIRED"


def test_get_subscription_state(db):
    tenant = _make_tenant(db)
    state = get_subscription_state(tenant)
    assert state["tenant_id"] == tenant.tenant_id
    assert state["subscription"]["plan"] == "standard"
    assert state["subscription"]["is_expired"] is False


def test_upgrade_from_free_trial(db):
    """Upgrade from free trial to a paid plan is now allowed."""
    tenant = _make_tenant(
        db,
        subscription_plan=SubscriptionPlan.FREE_TRIAL.value,
        subscription_status=SubscriptionStatus.TRIAL.value,
    )
    result = upgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
    )
    assert result.tenant.subscription_plan == SubscriptionPlan.BASIC.value
    assert result.tenant.subscription_status == SubscriptionStatus.ACTIVE.value


def test_downgrade_deferred_sets_pending_plan(db):
    """Deferred downgrade sets pending_plan instead of changing immediately."""
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    result = downgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
        effective_at_end=True,
    )
    # Plan should NOT have changed yet.
    assert tenant.subscription_plan == SubscriptionPlan.STANDARD.value
    # Pending plan should be set.
    assert tenant.pending_plan == SubscriptionPlan.BASIC.value
    assert tenant.pending_billing_cycle == BillingCycle.MONTHLY.value
    assert result.action == "downgrade_deferred"


def test_downgrade_immediate_changes_plan(db):
    """Immediate downgrade changes the plan right away."""
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    downgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
        effective_at_end=False,
    )
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value
    assert tenant.pending_plan is None


def test_apply_pending_plan_at_renewal(db):
    """Pending plan is applied when renew_subscription runs."""
    tenant = _make_tenant(
        db,
        subscription_plan=SubscriptionPlan.STANDARD.value,
        pending_plan=SubscriptionPlan.BASIC.value,
        pending_billing_cycle=BillingCycle.MONTHLY.value,
    )
    result = renew_subscription(db, tenant_id=tenant.tenant_id)
    # After renewal, the pending plan should have been applied.
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value
    assert tenant.pending_plan is None
    assert tenant.pending_billing_cycle is None
    assert result.previous_plan == SubscriptionPlan.STANDARD.value


def test_apply_pending_plan_changes_function(db):
    """apply_pending_plan_changes applies and clears pending_plan."""
    tenant = _make_tenant(
        db,
        subscription_plan=SubscriptionPlan.PREMIUM.value,
        pending_plan=SubscriptionPlan.STANDARD.value,
        pending_billing_cycle=BillingCycle.ANNUAL.value,
    )
    result = apply_pending_plan_changes(db, tenant)
    assert result is True
    assert tenant.subscription_plan == SubscriptionPlan.STANDARD.value
    assert tenant.pending_plan is None
    assert tenant.pending_billing_cycle is None


def test_apply_pending_plan_changes_noop_when_none(db):
    """apply_pending_plan_changes returns False when no pending plan."""
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.BASIC.value)
    result = apply_pending_plan_changes(db, tenant)
    assert result is False
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value


def test_upgrade_clears_pending_plan(db):
    """Upgrading clears any existing deferred downgrade."""
    tenant = _make_tenant(
        db,
        subscription_plan=SubscriptionPlan.BASIC.value,
        pending_plan=SubscriptionPlan.FREE_TRIAL,  # unlikely, but test the clear
    )
    upgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.STANDARD,
    )
    assert tenant.pending_plan is None
    assert tenant.pending_billing_cycle is None


def test_compute_proration_upgrade(db):
    """Proration computes a positive amount for upgrades."""
    end = datetime.now(timezone.utc) + timedelta(days=15)
    amount = compute_prorated_amount(
        current_plan=SubscriptionPlan.BASIC,
        new_plan=SubscriptionPlan.STANDARD,
        billing_cycle=BillingCycle.MONTHLY,
        subscription_end=end,
    )
    # Standard (299) - Basic (99) = 200 difference for 30-day cycle, ~half remaining.
    # 200 * 15/30 = 100
    assert amount > 0


def test_compute_proration_downgrade(db):
    """Proration computes a negative amount for downgrades (credit)."""
    end = datetime.now(timezone.utc) + timedelta(days=15)
    amount = compute_prorated_amount(
        current_plan=SubscriptionPlan.STANDARD,
        new_plan=SubscriptionPlan.BASIC,
        billing_cycle=BillingCycle.MONTHLY,
        subscription_end=end,
    )
    assert amount < 0


def test_compute_proration_zero_when_expired(db):
    """Proration returns 0 when subscription has expired."""
    end = datetime.now(timezone.utc) - timedelta(days=1)
    amount = compute_prorated_amount(
        current_plan=SubscriptionPlan.BASIC,
        new_plan=SubscriptionPlan.STANDARD,
        billing_cycle=BillingCycle.MONTHLY,
        subscription_end=end,
    )
    assert amount == 0


def test_upgrade_deferred_sets_pending_plan(db):
    """Deferred upgrade sets pending_plan instead of changing immediately."""
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.BASIC.value)
    result = upgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.STANDARD,
        effective_at_end=True,
    )
    # Plan should NOT have changed yet.
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value
    # Pending plan should be set.
    assert tenant.pending_plan == SubscriptionPlan.STANDARD.value
    assert result.action == "upgrade_deferred"


def test_downgrade_seat_limit_blocked(db):
    """Downgrading is blocked if active user count exceeds new plan seat limit."""
    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    from unittest.mock import patch
    with patch("app.services.subscription_service._get_tenant_active_users_count", return_value=25):
        with pytest.raises(SubscriptionError) as exc:
            downgrade_subscription(
                db,
                tenant_id=tenant.tenant_id,
                new_plan=SubscriptionPlan.BASIC,  # Basic limit: 20
                effective_at_end=False,
            )
        assert exc.value.detail["code"] == "LIMIT_EXCEEDED"


def test_upgrade_supersedes_previous_subscription(db):
    from app.services.subscription_plans import sync_plans_to_db
    from app.models.saas import Subscription as SubscriptionRecord
    sync_plans_to_db(db)

    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.BASIC.value)
    # 1. Start initial subscription
    subscribe_tenant(db, tenant_id=tenant.tenant_id, plan=SubscriptionPlan.BASIC, billing_cycle=BillingCycle.MONTHLY)
    
    # Verify we have one active subscription
    subs = db.query(SubscriptionRecord).filter(SubscriptionRecord.tenant_id == tenant.tenant_id).all()
    assert len(subs) == 1
    assert subs[0].status == "active"

    # 2. Upgrade immediately
    upgrade_subscription(db, tenant_id=tenant.tenant_id, new_plan=SubscriptionPlan.STANDARD)

    # Verify status transitions
    subs = db.query(SubscriptionRecord).filter(SubscriptionRecord.tenant_id == tenant.tenant_id).order_by(SubscriptionRecord.created_at.asc()).all()
    assert len(subs) == 2
    assert subs[0].status == "superseded"
    assert subs[0].cancelled_at is not None
    assert subs[1].status == "active"


def test_deferred_downgrade_creates_pending_subscription(db):
    from app.services.subscription_plans import sync_plans_to_db
    from app.models.saas import Subscription as SubscriptionRecord
    sync_plans_to_db(db)

    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    # Start initial subscription
    subscribe_tenant(db, tenant_id=tenant.tenant_id, plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.MONTHLY)

    # Downgrade with effective_at_end=True
    downgrade_subscription(db, tenant_id=tenant.tenant_id, new_plan=SubscriptionPlan.BASIC, effective_at_end=True)

    # Verify a pending subscription is created in database
    subs = db.query(SubscriptionRecord).filter(SubscriptionRecord.tenant_id == tenant.tenant_id).order_by(SubscriptionRecord.created_at.asc()).all()
    assert len(subs) == 2
    assert subs[0].status == "active"
    assert subs[1].status == "pending"
    assert subs[1].start_date == subs[0].end_date


def test_renewal_promotes_pending_subscription_to_active(db):
    from app.services.subscription_plans import sync_plans_to_db
    from app.models.saas import Subscription as SubscriptionRecord
    sync_plans_to_db(db)

    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    # Start initial subscription
    subscribe_tenant(db, tenant_id=tenant.tenant_id, plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.MONTHLY)

    # Downgrade with effective_at_end=True (creates pending BASIC subscription)
    downgrade_subscription(db, tenant_id=tenant.tenant_id, new_plan=SubscriptionPlan.BASIC, effective_at_end=True)

    # Renew subscription (should promote pending BASIC to active, and expire STANDARD)
    renew_subscription(db, tenant_id=tenant.tenant_id)

    # Verify status transitions in DB
    subs = db.query(SubscriptionRecord).filter(SubscriptionRecord.tenant_id == tenant.tenant_id).order_by(SubscriptionRecord.created_at.asc()).all()
    assert len(subs) == 2
    assert subs[0].status == "expired"
    assert subs[1].status == "active"
    assert tenant.subscription_plan == SubscriptionPlan.BASIC.value


def test_same_plan_billing_cycle_upgrade(db):
    from app.services.subscription_plans import sync_plans_to_db
    from app.models.saas import Subscription as SubscriptionRecord
    sync_plans_to_db(db)

    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    subscribe_tenant(db, tenant_id=tenant.tenant_id, plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.MONTHLY)
    
    initial_end = tenant.subscription_end
    
    # Upgrade to annual Standard subscription immediately
    upgrade_subscription(db, tenant_id=tenant.tenant_id, new_plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.ANNUAL, effective_at_end=False)
    
    assert tenant.subscription_billing_cycle == "annual"
    assert (tenant.subscription_end - initial_end).days > 300


def test_cross_cycle_downgrade_immediate(db):
    from app.services.subscription_plans import sync_plans_to_db
    sync_plans_to_db(db)

    tenant = _make_tenant(db, subscription_plan=SubscriptionPlan.STANDARD.value)
    subscribe_tenant(db, tenant_id=tenant.tenant_id, plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.ANNUAL)
    
    initial_end = tenant.subscription_end
    
    # Downgrade to monthly Standard subscription immediately
    downgrade_subscription(db, tenant_id=tenant.tenant_id, new_plan=SubscriptionPlan.STANDARD, billing_cycle=BillingCycle.MONTHLY, effective_at_end=False)
    
    assert tenant.subscription_billing_cycle == "monthly"
    assert (initial_end - tenant.subscription_end).days > 300


def test_activate_tenant_manual_override(db):
    tenant = _make_tenant(db, status="suspended", is_active=False, subscription_status=SubscriptionStatus.SUSPENDED.value)
    res = activate_tenant(db, tenant_id=tenant.tenant_id, user_sub="admin-sub", ip_address="127.0.0.1")
    assert res.action == "activate"
    assert tenant.status == "active"
    assert tenant.is_active is True
    assert tenant.subscription_status == SubscriptionStatus.ACTIVE.value


def test_create_plan_change_invoice(db):
    from unittest.mock import MagicMock, patch
    from app.services.subscription_service import _generate_invoice

    tenant = _make_tenant(db)
    with patch("app.services.subscription_service.inspect") as mock_inspect:
        mock_inspect.return_value.has_table.return_value = True
        with patch.object(db, "add") as mock_add:
            _generate_invoice(
                db=db,
                tenant_id=tenant.tenant_id,
                subscription_id=uuid.uuid4(),
                plan_name="standard",
                amount=99.0,
                billing_cycle=BillingCycle.MONTHLY,
                subscription_start=datetime.now(timezone.utc),
                subscription_end=datetime.now(timezone.utc) + timedelta(days=30),
            )
            assert mock_add.called


# ---------------------------------------------------------------------------
# Subscription Requests & Payment Compliance Tests
# ---------------------------------------------------------------------------

def test_tenant_schema_grace_period_validation():
    from pydantic import ValidationError
    valid_create = TenantCreate(
        hospital_name="St. Luke",
        admin_username="admin",
        admin_email="admin@st-luke.org",
        admin_password="password",
        grace_period_days=14,
    )
    assert valid_create.grace_period_days == 14

    with pytest.raises(ValidationError):
        TenantCreate(
            hospital_name="St. Luke",
            admin_username="admin",
            admin_email="admin@st-luke.org",
            admin_password="password",
            grace_period_days=-5,
        )

def test_dynamic_grace_period_calculation_free_trial(db):
    tenant = _make_tenant(db, grace_period_days=15)
    _set_subscription_dates(tenant, BillingCycle.MONTHLY, plan=SubscriptionPlan.FREE_TRIAL, db=db)
    expected_grace_days = 15
    grace_duration = tenant.grace_period_end - tenant.trial_end
    assert grace_duration.days == expected_grace_days

def test_dynamic_grace_period_calculation_paid_plan(db):
    tenant = _make_tenant(db, grace_period_days=21)
    _set_subscription_dates(tenant, BillingCycle.MONTHLY, plan=SubscriptionPlan.STANDARD, db=db)
    expected_grace_days = 21
    grace_duration = tenant.grace_period_end - tenant.subscription_end
    assert grace_duration.days == expected_grace_days

def test_create_plan_change_request(db):
    tenant = _make_tenant(db, subscription_plan="basic")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="upgrade", requested_plan="standard", reason="Need more features")
    db.commit()
    assert tenant.pending_action == "upgrade"
    assert tenant.requested_plan == "standard"
    assert tenant.request_reason == "Need more features"
    assert tenant.requested_at is not None

def test_create_cancellation_request(db):
    tenant = _make_tenant(db)
    create_cancellation_request(db, tenant_id=tenant.tenant_id, reason="Closing business")
    db.commit()
    assert tenant.pending_action == "cancellation"
    assert tenant.requested_plan is None
    assert tenant.request_reason == "Closing business"

def test_approve_plan_change_request(db):
    tenant = _make_tenant(db, subscription_plan="basic")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="upgrade", requested_plan="standard", reason="Need more features")
    db.commit()

    super_admin_id = str(uuid.uuid4())
    approve_request(db, tenant_id=tenant.tenant_id, reviewer_sub=super_admin_id, notes="Approved upgrade")
    db.commit()

    assert tenant.subscription_plan == "standard"
    assert tenant.pending_action is None
    assert tenant.requested_plan is None
    assert tenant.reviewed_by is not None
    assert str(tenant.reviewed_by) == super_admin_id
    assert tenant.review_notes == "Approved upgrade"

def test_reject_plan_change_request(db):
    tenant = _make_tenant(db, subscription_plan="basic")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="upgrade", requested_plan="standard", reason="Need more features")
    db.commit()

    super_admin_id = str(uuid.uuid4())
    reject_request(db, tenant_id=tenant.tenant_id, reviewer_sub=super_admin_id, notes="Not approved")
    db.commit()

    assert tenant.subscription_plan == "basic"
    assert tenant.pending_action is None
    assert tenant.reviewed_by is not None
    assert str(tenant.reviewed_by) == super_admin_id
    assert tenant.review_notes == "Not approved"

def test_list_subscription_requests_with_history(db):
    tenant = _make_tenant(db, subscription_plan="basic")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="upgrade", requested_plan="standard", reason="Test first request")
    db.commit()

    approve_request(db, tenant_id=tenant.tenant_id, reviewer_sub="admin-1", notes="First request approved")
    db.commit()

    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="downgrade", requested_plan="basic", reason="Test second request")
    db.commit()

    all_reqs = list_subscription_requests(db)
    assert len(all_reqs) == 2

    pending_reqs = list_subscription_requests(db, status="pending")
    assert len(pending_reqs) == 1
    assert pending_reqs[0]["pending_action"] == "downgrade"

    approved_reqs = list_subscription_requests(db, status="approved")
    assert len(approved_reqs) == 1
    assert approved_reqs[0]["pending_action"] == "upgrade"
    assert approved_reqs[0]["review_notes"] == "First request approved"

def test_create_same_plan_billing_cycle_upgrade_request(db):
    tenant = _make_tenant(db, subscription_plan="basic", subscription_billing_cycle="monthly")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="upgrade", requested_plan="basic", requested_billing_cycle="annual", reason="Upgrading to annual")
    db.commit()

    assert tenant.pending_action == "upgrade"
    assert tenant.requested_plan == "basic"
    assert tenant.subscription_metadata.get("requested_billing_cycle") == "annual"

def test_create_same_plan_billing_cycle_downgrade_request(db):
    tenant = _make_tenant(db, subscription_plan="basic", subscription_billing_cycle="annual")
    create_plan_change_request(db, tenant_id=tenant.tenant_id, action="downgrade", requested_plan="basic", requested_billing_cycle="monthly", reason="Downgrading to monthly")
    db.commit()

    assert tenant.pending_action == "downgrade"
    assert tenant.requested_plan == "basic"
    assert tenant.subscription_metadata.get("requested_billing_cycle") == "monthly"

def test_subscription_requests_edge_cases(db):
    tenant = _make_tenant(db, subscription_plan="basic")

    with pytest.raises(Exception):
        create_plan_change_request(db, tenant.tenant_id, "upgrade", "invalid_plan", "reason")

    with pytest.raises(Exception):
        create_plan_change_request(db, tenant.tenant_id, "upgrade", "free_trial", "reason")

    create_plan_change_request(db, tenant.tenant_id, "upgrade", "standard", "reason")
    db.commit()

    pending = list_pending_requests(db, tenant_id=tenant.tenant_id)
    assert len(pending) == 1

    with pytest.raises(Exception):
        create_plan_change_request(db, tenant.tenant_id, "upgrade", "premium", "reason2")

    create_cancellation_request(db, tenant.tenant_id, "Closing branch")
    db.commit()
    assert tenant.pending_action == "cancellation"

    with pytest.raises(Exception):
        create_cancellation_request(db, tenant.tenant_id, "Closing branch again")

    reject_request(db, tenant.tenant_id, reviewer_sub="admin-1", notes="Rejected cancellation")
    db.commit()
    assert tenant.pending_action is None

def test_audit_event_with_table(db):
    from unittest.mock import patch
    with patch("sqlalchemy.inspect") as mock_inspect:
        mock_inspect.return_value.has_table.return_value = True
        with patch.object(db, "add") as mock_add:
            _audit_event(db=db, tenant_id="t1", event_type="request_created", actor_id=str(uuid.uuid4()), actor_type="user", reason="Testing audit")
            assert mock_add.called

def test_log_action_error(db):
    from unittest.mock import patch
    with patch.object(db, "add", side_effect=Exception("DB Error")):
        log_action(db, "t1", "action.test", {"data": "test"})

