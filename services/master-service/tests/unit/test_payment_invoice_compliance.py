import pytest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pydantic import ValidationError

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.master import Tenant, GlobalAuditLog
from app.models.saas import Invoice, SaaSPayment
from app.services.subscription_plans import BillingCycle, SubscriptionPlan, SubscriptionStatus
from app.services.subscription_service import _set_subscription_dates, renew_subscription
from app.api.v1.superadmin.schemas import TenantCreate, TenantUpdate, InvoiceOut


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    # Create all required tables
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Tenant.__table__,
            GlobalAuditLog.__table__,
            Invoice.__table__,
            SaaSPayment.__table__
        ]
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
        "grace_period_days": 7
    }
    defaults.update(overrides)
    tenant = Tenant(**defaults)
    db.add(tenant)
    db.commit()
    db.refresh(tenant)
    return tenant


def test_tenant_schema_grace_period_validation():
    # Test valid grace period
    valid_create = TenantCreate(
        hospital_name="St. Luke",
        admin_username="admin",
        admin_email="admin@st-luke.org",
        admin_password="password",
        grace_period_days=14
    )
    assert valid_create.grace_period_days == 14

    # Test invalid grace period (negative bounds check)
    with pytest.raises(ValidationError):
        TenantCreate(
            hospital_name="St. Luke",
            admin_username="admin",
            admin_email="admin@st-luke.org",
            admin_password="password",
            grace_period_days=-5
        )


def test_dynamic_grace_period_calculation_free_trial(db):
    tenant = _make_tenant(db, grace_period_days=15)
    _set_subscription_dates(tenant, BillingCycle.MONTHLY, plan=SubscriptionPlan.FREE_TRIAL, db=db)
    
    expected_grace_days = 15
    trial_duration = tenant.trial_end - tenant.trial_start
    grace_duration = tenant.grace_period_end - tenant.trial_end
    
    assert grace_duration.days == expected_grace_days


def test_dynamic_grace_period_calculation_paid_plan(db):
    tenant = _make_tenant(db, grace_period_days=21)
    _set_subscription_dates(tenant, BillingCycle.MONTHLY, plan=SubscriptionPlan.STANDARD, db=db)
    
    expected_grace_days = 21
    grace_duration = tenant.grace_period_end - tenant.subscription_end
    
    assert grace_duration.days == expected_grace_days


def test_renew_subscription_uses_dynamic_grace_period(db):
    tenant = _make_tenant(db, grace_period_days=12)
    # Perform a subscription renewal
    renew_subscription(db, tenant_id=tenant.tenant_id, billing_cycle=BillingCycle.MONTHLY)
    db.commit()
    db.refresh(tenant)
    grace_duration = tenant.grace_period_end - tenant.subscription_end
    assert grace_duration.days == 12


def test_invoice_enrichment_hospital_name_fields(db):
    tenant = _make_tenant(db, hospital_name="Mount Meru Hospital")
    
    invoice = Invoice(
        invoice_id=uuid.uuid4(),
        tenant_id=tenant.tenant_id,
        subscription_id=uuid.uuid4(),
        invoice_number="INV-2026-0001",
        billing_period_start=(datetime.now(timezone.utc) - timedelta(days=30)).date(),
        billing_period_end=datetime.now(timezone.utc).date(),
        plan_name="standard",
        amount=Decimal("150.00"),
        currency="USD",
        due_date=(datetime.now(timezone.utc) + timedelta(days=7)).date(),
        status="unpaid",
        issued_at=datetime.now(timezone.utc)
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)

    # Mock the api enrich helper behavior
    from app.api.v1.superadmin.router import _enrich_invoice
    _enrich_invoice(db, invoice)
    
    assert invoice.hospital_name == "Mount Meru Hospital"
    
    # Serialize with Pydantic InvoiceOut schema to verify compile alignment
    serialized = InvoiceOut.model_validate(invoice)
    assert serialized.hospital_name == "Mount Meru Hospital"
