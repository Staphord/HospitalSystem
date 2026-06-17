import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.master import Tenant, GlobalAuditLog
from app.services.subscription_plans import BillingCycle, SubscriptionPlan, SubscriptionStatus
from app.services.subscription_service import (
    SubscriptionError,
    activate_tenant,
    downgrade_subscription,
    get_subscription_state,
    reactivate_tenant,
    renew_subscription,
    subscribe_tenant,
    suspend_tenant,
    upgrade_subscription,
    _ensure_aware,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    # Only create the tables this test suite actually touches.
    Base.metadata.create_all(bind=engine, tables=[Tenant.__table__, GlobalAuditLog.__table__])
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _make_tenant(db, **overrides):
    defaults = {
        "tenant_id": "hosp-test001",
        "name": "Test Hospital",
        "db_dsn_encrypted": "enc",
        "status": "active",
        "is_active": True,
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

    downgrade_subscription(
        db,
        tenant_id=tenant.tenant_id,
        new_plan=SubscriptionPlan.BASIC,
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
