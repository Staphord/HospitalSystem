"""Unit tests for superadmin router in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException, BackgroundTasks
from starlette.requests import Request
from datetime import datetime, timezone, date, timedelta
from uuid import uuid4

from app.api.v1.superadmin import router as sa_router
from app.api.v1.superadmin.router import (
    list_users,
    create_user,
    update_user,
    delete_user,
    list_tenants,
    create_tenant,
    get_tenant,
    update_tenant,
    list_plans,
    create_plan,
    update_plan,
    list_super_admin_audit_log,
)
from app.api.v1.superadmin.schemas import (
    SuperAdminCreate,
    SuperAdminUpdate,
    SuperAdminDelete,
    TenantCreate,
    TenantUpdate,
    AnnouncementCreate,
    AnnouncementUpdate,
    IncidentCreate,
    IncidentUpdate,
    InvoiceCreate,
    InvoiceUpdate,
    SaaSPaymentCreate,
    SystemRoleCreate,
    SystemRoleUpdate,
    PlanCreate,
    PlanUpdate,
)
from app.core.security import TokenPayload

def get_handler(fn):
    return getattr(fn, "__wrapped__", fn)

def make_req(method="POST", path="/"):
    return Request(scope={
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

def make_user():
    return TokenPayload(
        sub=str(uuid4()),
        preferred_username="admin",
        email="admin@test.org",
        realm_access={"roles": ["super_admin"]},
        raw={"type": "superadmin", "role": "super_admin"},
    )

def make_fake_tenant(tenant_id="t1", status="active", plan="pro"):
    return MagicMock(
        id=1,
        tenant_id=tenant_id,
        hospital_name="Hospital One",
        status=status,
        subscription_plan=plan,
        subscription_status=status,
        subscription_billing_cycle="monthly",
        subscription_start=datetime.now(timezone.utc),
        subscription_end=datetime.now(timezone.utc),
        trial_start=None,
        trial_end=None,
        trial_ends_at=None,
        has_used_trial=False,
        auto_renew=True,
        grace_period_days=7,
        grace_period_end=None,
        pending_plan=None,
        pending_billing_cycle=None,
        suspended_at=None,
        suspended_reason=None,
        reactivated_at=None,
        cancelled_at=None,
        terminated_at=None,
        termination_reason=None,
        payment_provider_id=None,
        is_active=True,
        created_by=None,
        keycloak_realm="t1",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        primary_contact_email="admin@h1.org",
        billing_email="admin@h1.org",
        primary_contact_name="Admin",
        currency="USD",
        country="US",
        city="New York",
        address="123 Main St",
        primary_contact_phone="+1234567890",
        timezone="UTC",
        date_format="YYYY-MM-DD",
        logo_url=None,
        data_region="us-east-1",
    )

class TestSuperAdminRouterHelpers:
    def test_request_meta(self):
        req = make_req()
        user = TokenPayload(sub="sub-fallback", preferred_username="admin", email="admin@test.org", realm_access={}, raw={})
        sub, ip = sa_router._request_meta(req, user)
        assert sub == "sub-fallback"
        assert ip == "127.0.0.1"

    def test_handle_base64_logo(self):
        with patch("os.makedirs"):
            with patch("builtins.open", MagicMock()):
                url = sa_router._handle_base64_logo("t1", "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
                assert "t1" in url

        res_existing = sa_router._handle_base64_logo("t1", "/api/v1/static/logos/existing.png")
        assert res_existing == "/api/v1/static/logos/existing.png"

@pytest.mark.asyncio
async def test_superadmin_user_management():
    fn_create = get_handler(create_user)
    fn_update = get_handler(update_user)
    fn_delete = get_handler(delete_user)

    req = make_req()
    user = make_user()
    db = MagicMock()

    db.query.return_value.filter.return_value.first.return_value = None
    with patch("app.services.superadmin_auth.create_superadmin") as mock_create_sa, \
         patch("app.api.v1.superadmin.router.ensure_roles", AsyncMock()), \
         patch("app.api.v1.superadmin.router.create_keycloak_user", AsyncMock(return_value="kc-123")):
        fake_sa = MagicMock(super_admin_id=uuid4(), username="new_sa", email="sa@t.org", full_name="SA", role="super_admin", is_active=True, created_at=datetime.now(timezone.utc), last_login_at=None)
        mock_create_sa.return_value = fake_sa
        res = await fn_create(req, SuperAdminCreate(username="new_sa", email="sa@t.org", password="P@ssword123", full_name="SA"), db, user)
        assert res is not None

    existing_sa = MagicMock(super_admin_id=uuid4(), username="ex_sa", email="ex@t.org", full_name="Ex", role="super_admin", is_active=True, created_at=datetime.now(timezone.utc), last_login_at=None)
    db.query.return_value.filter.return_value.first.return_value = existing_sa
    with patch("app.services.superadmin_auth.update_superadmin_password"), \
         patch("app.services.superadmin_auth.update_superadmin_role"), \
         patch("app.services.keycloak_admin.update_keycloak_user", AsyncMock()):
        res_upd = await fn_update(req, existing_sa.super_admin_id, SuperAdminUpdate(full_name="Ex Updated"), db, user)
        assert res_upd is not None

    with patch("app.api.v1.superadmin.router._log_action"), \
         patch("app.api.v1.superadmin.router.delete_keycloak_user", AsyncMock()):
        await fn_delete(req, SuperAdminDelete(username="ex_sa"), db, user)

@pytest.mark.asyncio
async def test_superadmin_tenant_management():
    fn_onboard = get_handler(create_tenant)
    fn_get = get_handler(get_tenant)
    fn_update = get_handler(update_tenant)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_t = make_fake_tenant("t-new")
    db.query.return_value.filter.return_value.first.return_value = None

    def mock_tenant_refresh(t):
        t.id = 1
        t.subscription_plan = "free_trial"
        t.subscription_status = "active"
        t.subscription_billing_cycle = "monthly"
        t.has_used_trial = False
        t.auto_renew = True
        t.created_at = datetime.now(timezone.utc)
        t.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_tenant_refresh

    with patch("app.events.publisher.publish_tenant_created", AsyncMock()), \
         patch("app.api.v1.superadmin.router.setup_tenant_realm", AsyncMock()), \
         patch("app.api.v1.superadmin.router.verify_tenant_realm_exists", AsyncMock(return_value=True)), \
         patch("app.api.v1.superadmin.router.ensure_roles", AsyncMock()), \
         patch("app.api.v1.superadmin.router.create_keycloak_user", AsyncMock(return_value="u1")), \
         patch("app.services.keycloak_admin.assign_user_roles", AsyncMock()), \
         patch("app.services.keycloak_admin.set_user_attribute", AsyncMock()), \
         patch("app.services.subscription_service.subscribe_tenant", MagicMock()), \
         patch("app.services.provision.provision_tenant_database_sync", return_value="postgresql://dsn"), \
         patch("app.services.provision.get_tenant_db_session", return_value=MagicMock()), \
         patch("app.services.provision.drop_tenant_database", MagicMock()), \
         patch("app.api.v1.superadmin.router.create_local_user", return_value=MagicMock()), \
         patch("app.api.v1.superadmin.router._log_action"):

        res_onb = await fn_onboard(
            req,
            TenantCreate(
                hospital_name="New Hospital",
                admin_username="admin_new",
                admin_email="admin@hnew.org",
                admin_password="P@ssword123",
                admin_full_name="Admin New",
            ),
            BackgroundTasks(),
            db,
            user,
        )
        assert res_onb is not None

    db.query.return_value.filter.return_value.first.return_value = fake_t
    res_get = await fn_get(req, "t-new", db, user)
    assert res_get is not None

    with patch("app.api.v1.superadmin.router._log_action"):
        res_upd = await fn_update(req, "t-new", TenantUpdate(hospital_name="Updated Hosp Name"), db, user)
        assert res_upd is not None

@pytest.mark.asyncio
async def test_plans_crud():
    fn_list = get_handler(list_plans)
    fn_create = get_handler(create_plan)
    fn_update = get_handler(update_plan)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_plan = MagicMock(
        plan_id=uuid4(),
        plan_name="enterprise_custom",
        description="Custom Enterprise Plan",
        monthly_price=29900,
        annual_price=299000,
        max_users=500,
        max_patients=50000,
        storage_gb=1000,
        modules_included=["all"],
        annual_discount_pct=15.0,
        uptime_sla_pct=99.99,
        backup_frequency_hours=1,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    db.query.return_value.order_by.return_value.all.return_value = [fake_plan]
    db.query.return_value.filter.return_value.first.return_value = fake_plan

    def mock_plan_refresh(p):
        p.plan_id = uuid4()
        p.created_at = datetime.now(timezone.utc)
        p.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_plan_refresh

    with patch("app.api.v1.superadmin.router._log_action"):
        res_list = await fn_list(req, db, user)
        assert len(res_list) == 1

        res_create = await fn_create(
            req,
            PlanCreate(
                plan_name="enterprise_custom",
                description="Custom Enterprise Plan",
                monthly_price=29900,
                annual_price=299000,
                max_users=500,
            ),
            db,
            user,
        )
        assert res_create is not None

        res_upd = await fn_update(
            req,
            fake_plan.plan_id,
            PlanUpdate(description="Updated Enterprise Description"),
            db,
            user,
        )
        assert res_upd is not None

@pytest.mark.asyncio
async def test_list_super_admin_audit_log_all_actions():
    fn_list = get_handler(list_super_admin_audit_log)

    req = make_req()
    user = make_user()
    db = MagicMock()

    actions = [
        ("super_admin.login", '{"username": "admin"}'),
        ("super_admin.create", '{"username": "new_admin"}'),
        ("super_admin.update", '{"super_admin_id": "123"}'),
        ("super_admin.password_reset", '{"username": "admin"}'),
        ("plan.create", '{"plan_name": "pro", "monthly_price": "99"}'),
        ("plan.update", '{"plan_name": "pro"}'),
        ("subscription.upgrade", '{"new_plan": "pro"}'),
        ("subscription.downgrade", '{"new_plan": "basic"}'),
        ("announcement.create", '{"title": "Maint"}'),
        ("announcement.update", '{"announcement_id": "1"}'),
        ("announcement.delete", '{"announcement_id": "1"}'),
        ("system_role.create", '{"role_name": "doc"}'),
        ("system_role.update", '{"role_id": "1"}'),
        ("system_role.delete", '{"role_name": "doc"}'),
        ("global_role.create", '{"role_name": "g_doc"}'),
        ("tenant.onboard", '{"hospital_name": "City Hosp", "country": "KE", "city": "Nbo"}'),
        ("tenant.update", '{"status": "active"}'),
        ("invoice.create", '{"invoice_number": "INV-1", "amount": "100", "currency": "USD"}'),
        ("payment.record", '{"amount": "100", "currency": "USD", "payment_method": "Card"}'),
        ("incident.create", '{"title": "Outage", "severity": "high"}'),
        ("session.revoke_all", '{}'),
        ("unknown.action", '"plain string detail"'),
    ]

    mock_logs = []
    for idx, (act, detail) in enumerate(actions, start=1):
        mock_l = MagicMock(
            id=idx,
            user_sub=str(uuid4()),
            action=act,
            tenant_id="t1",
            detail=detail,
            ip_address="127.0.0.1",
            created_at=datetime.now(timezone.utc),
        )
        mock_logs.append(mock_l)

    fake_admin = MagicMock(super_admin_id=uuid4(), full_name="Admin User")
    db.query.return_value.all.return_value = [fake_admin]
    db.query.return_value.order_by.return_value.all.return_value = mock_logs

    res = await fn_list(req, db, user)
    assert len(res) == len(actions)


def make_fake_invoice(tenant_id="t1"):
    inv_id = uuid4()
    sub_id = uuid4()
    return MagicMock(
        id=1,
        invoice_id=inv_id,
        subscription_id=sub_id,
        invoice_number="INV-2026-001",
        tenant_id=tenant_id,
        hospital_name="Hospital One",
        amount=150.0,
        tax_amount=0.0,
        amount_paid=0.0,
        total_amount=150.0,
        currency="USD",
        status="unpaid",
        due_date=date(2026, 9, 1),
        billing_period_start=date(2026, 8, 1),
        billing_period_end=date(2026, 8, 31),
        plan_name="standard",
        billing_cycle="monthly",
        payment_method="card",
        reference_number="REF-001",
        pdf_download_url=None,
        line_items=[],
        notes=None,
        created_by=uuid4(),
        issued_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
        paid_at=None,
    )

def make_fake_payment(tenant_id="t1"):
    pay_id = uuid4()
    inv_id = uuid4()
    rec_by = uuid4()
    return MagicMock(
        id=1,
        payment_id=pay_id,
        tenant_id=tenant_id,
        invoice_id=inv_id,
        amount=150.0,
        currency="USD",
        status="succeeded",
        payment_method="card",
        transaction_id="tx_123",
        reference_number="PAY-REF-001",
        recorded_by=rec_by,
        notes=None,
        paid_at=datetime.now(timezone.utc),
        created_at=datetime.now(timezone.utc),
    )

def make_fake_announcement():
    anc_id = uuid4()
    return MagicMock(
        id=1,
        announcement_id=anc_id,
        title="Scheduled Maintenance",
        message="System update tonight",
        body="System update tonight",
        priority="high",
        audience="all",
        target_tenant_id=None,
        is_active=True,
        starts_at=datetime.now(timezone.utc),
        publish_at=datetime.now(timezone.utc),
        ends_at=datetime.now(timezone.utc) + timedelta(hours=4),
        created_by=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

@pytest.mark.asyncio
async def test_superadmin_system_roles_management():
    from app.api.v1.superadmin.router import (
        list_system_roles,
        create_system_role,
        update_system_role,
        delete_system_role,
    )
    fn_list = get_handler(list_system_roles)
    fn_create = get_handler(create_system_role)
    fn_update = get_handler(update_system_role)
    fn_delete = get_handler(delete_system_role)

    req = make_req()
    user = make_user()
    db = MagicMock()

    role_uuid = uuid4()
    creator_uuid = uuid4()
    fake_role = MagicMock(
        system_role_id=role_uuid,
        description="Specialist clinical access",
        scope={},
        is_global=True,
        created_by=creator_uuid,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    fake_role.name = "clinical_specialist"

    db.query.return_value.order_by.return_value.all.return_value = [fake_role]
    db.query.return_value.filter.return_value.all.return_value = []
    db.query.return_value.filter.return_value.first.side_effect = [None, fake_role, fake_role, fake_role, fake_role]

    def mock_role_refresh(r):
        r.system_role_id = role_uuid
        r.name = "clinical_specialist"
        r.description = "Specialist clinical access"
        r.scope = {}
        r.is_global = True
        r.created_by = creator_uuid
        r.created_at = datetime.now(timezone.utc)
        r.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_role_refresh

    with patch("app.api.v1.superadmin.router._log_action"):
        res_list = await fn_list(req, None, db, user)
        assert len(res_list) == 1

        res_create = await fn_create(req, SystemRoleCreate(name="clinical_specialist", description="Specialist clinical access", is_global=True, scope={}), db, user)
        assert res_create is not None

        res_upd = await fn_update(req, fake_role.system_role_id, SystemRoleUpdate(description="Updated description"), db, user)
        assert res_upd is not None

        await fn_delete(req, fake_role.system_role_id, db, user)


@pytest.mark.asyncio
async def test_superadmin_announcements_management():
    from app.api.v1.superadmin.router import (
        list_announcements,
        create_announcement,
        update_announcement,
        delete_announcement,
    )
    fn_list = get_handler(list_announcements)
    fn_create = get_handler(create_announcement)
    fn_update = get_handler(update_announcement)
    fn_delete = get_handler(delete_announcement)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_anc = make_fake_announcement()
    db.query.return_value.order_by.return_value.all.return_value = [fake_anc]
    db.query.return_value.filter.return_value.first.return_value = fake_anc

    def mock_anc_refresh(a):
        a.id = 1
        a.announcement_id = fake_anc.announcement_id
        a.title = "Scheduled Maintenance"
        a.message = "System update tonight"
        a.body = "System update tonight"
        a.priority = "high"
        a.audience = "all"
        a.target_tenant_id = None
        a.is_active = True
        a.starts_at = datetime.now(timezone.utc)
        a.publish_at = datetime.now(timezone.utc)
        a.ends_at = datetime.now(timezone.utc) + timedelta(hours=4)
        a.created_by = fake_anc.created_by
        a.created_at = datetime.now(timezone.utc)
        a.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_anc_refresh

    with patch("app.api.v1.superadmin.router._log_action"), \
         patch("app.events.publisher.publish_announcement_created", AsyncMock()):
        res_list = await fn_list(req, db, user)
        assert len(res_list) == 1

        res_create = await fn_create(req, AnnouncementCreate(title="Maintenance", message="Details"), db, user)
        assert res_create is not None

        res_upd = await fn_update(req, 1, AnnouncementUpdate(message="Updated details"), db, user)
        assert res_upd is not None

        await fn_delete(req, 1, db, user)


@pytest.mark.asyncio
async def test_superadmin_incidents_management():
    from app.api.v1.superadmin.router import (
        list_incidents,
        create_incident,
        update_incident,
    )
    fn_list = get_handler(list_incidents)
    fn_create = get_handler(create_incident)
    fn_update = get_handler(update_incident)

    req = make_req()
    user = make_user()
    db = MagicMock()

    inc_uuid = uuid4()
    fake_inc = MagicMock(
        id=1,
        incident_id=inc_uuid,
        source="system",
        title="Database Latency",
        description="High query execution time",
        severity="medium",
        status="investigating",
        tenant_id="t1",
        affected_services=["master-service"],
        created_by=uuid4(),
        assigned_to=uuid4(),
        resolution_notes="Investigating",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        resolved_at=None,
    )
    db.query.return_value.order_by.return_value.all.return_value = [fake_inc]
    db.query.return_value.filter.return_value.first.return_value = fake_inc

    def mock_inc_refresh(i):
        i.id = 1
        i.incident_id = inc_uuid
        i.source = "system"
        i.title = "Database Latency"
        i.description = "High query execution time"
        i.severity = "medium"
        i.status = "investigating"
        i.tenant_id = "t1"
        i.affected_services = ["master-service"]
        i.created_by = uuid4()
        i.assigned_to = uuid4()
        i.resolution_notes = None
        i.created_at = datetime.now(timezone.utc)
        i.updated_at = datetime.now(timezone.utc)
        i.resolved_at = None

    db.refresh.side_effect = mock_inc_refresh

    fake_admin = MagicMock(super_admin_id=uuid4())
    with patch("app.api.v1.superadmin.router._log_action"):
        res_list = await fn_list(req, db, user)
        assert len(res_list) == 1

        db.query.return_value.filter.return_value.first.return_value = fake_admin
        res_create = await fn_create(req, IncidentCreate(title="Database Latency", description="High query time", severity="medium"), db, user)
        assert res_create is not None

        db.query.return_value.filter.return_value.first.return_value = fake_inc
        res_upd = await fn_update(req, str(inc_uuid), IncidentUpdate(status="resolved"), db, user)
        assert res_upd is not None


@pytest.mark.asyncio
async def test_superadmin_invoices_and_payments_management():
    from app.api.v1.superadmin.router import (
        list_tenant_invoices,
        create_tenant_invoice,
        update_invoice,
        list_tenant_payments,
        create_tenant_payment,
    )
    from fastapi import BackgroundTasks
    fn_list_inv = get_handler(list_tenant_invoices)
    fn_create_inv = get_handler(create_tenant_invoice)
    fn_upd_inv = get_handler(update_invoice)
    fn_list_pay = get_handler(list_tenant_payments)
    fn_create_pay = get_handler(create_tenant_payment)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_inv = make_fake_invoice("t1")
    fake_pay = make_fake_payment("t1")
    fake_admin = MagicMock(super_admin_id=uuid4())

    fake_inv.amount = 150.0
    fake_pay.amount = 150.0

    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_inv]
    db.query.return_value.filter.return_value.first.return_value = fake_inv

    def mock_inv_refresh(i):
        i.id = 1
        i.invoice_id = fake_inv.invoice_id
        i.subscription_id = fake_inv.subscription_id
        i.invoice_number = "INV-2026-001"
        i.tenant_id = "t1"
        i.hospital_name = "Hospital One"
        i.amount = 150.0
        i.tax_amount = 0.0
        i.amount_paid = 0.0
        i.total_amount = 150.0
        i.currency = "USD"
        i.status = "unpaid"
        i.due_date = date(2026, 9, 1)
        i.billing_period_start = date(2026, 8, 1)
        i.billing_period_end = date(2026, 8, 31)
        i.plan_name = "standard"
        i.billing_cycle = "monthly"
        i.payment_method = "card"
        i.reference_number = "REF-001"
        i.pdf_download_url = None
        i.line_items = []
        i.notes = None
        i.created_by = fake_inv.created_by
        i.issued_at = datetime.now(timezone.utc)
        i.created_at = datetime.now(timezone.utc)
        i.paid_at = None

    def mock_pay_refresh(p):
        p.id = 1
        p.payment_id = fake_pay.payment_id
        p.tenant_id = "t1"
        p.invoice_id = fake_inv.invoice_id
        p.amount = 150.0
        p.currency = "USD"
        p.status = "succeeded"
        p.payment_method = "card"
        p.transaction_id = "tx_123"
        p.reference_number = "PAY-REF-001"
        p.recorded_by = fake_pay.recorded_by
        p.notes = None
        p.paid_at = datetime.now(timezone.utc)
        p.created_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_inv_refresh

    fake_t = make_fake_tenant("t1")
    fake_t.amount = 150.0
    with patch("app.api.v1.superadmin.router._log_action"), \
         patch("app.api.v1.superadmin.router._enrich_invoice"):
        invs = await fn_list_inv(req, "t1", db, user)
        assert len(invs) == 1

        db.query.return_value.filter.return_value.first.return_value = fake_t
        new_inv = await fn_create_inv(req, "t1", InvoiceCreate(tenant_id="t1", plan_name="standard", amount=150.0, due_date=date(2026, 9, 1), billing_period_start=date(2026, 8, 1), billing_period_end=date(2026, 8, 31)), BackgroundTasks(), db, user)
        assert new_inv is not None

        db.query.return_value.filter.return_value.first.return_value = fake_inv
        upd_inv = await fn_upd_inv(req, 1, InvoiceUpdate(status="paid"), db, user)
        assert upd_inv is not None

        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_pay]
        pays = await fn_list_pay(req, "t1", db, user)
        assert len(pays) == 1

        db.query.return_value.filter.return_value.first.side_effect = [fake_inv, fake_admin, fake_inv, fake_t]
        db.query.return_value.filter.return_value.all.return_value = [fake_pay]
        db.refresh.side_effect = mock_pay_refresh
        new_pay = await fn_create_pay(req, "t1", SaaSPaymentCreate(tenant_id="t1", invoice_id=fake_inv.invoice_id, amount=150.0, payment_method="card", reference_number="PAY-REF-001"), BackgroundTasks(), db, user)
        assert new_pay is not None


@pytest.mark.asyncio
async def test_superadmin_saas_analytics_and_audit_export():
    from app.api.v1.superadmin.router import (
        get_tenant_analytics,
        export_tenant_data,
    )
    fn_analytics = get_handler(get_tenant_analytics)
    fn_export = get_handler(export_tenant_data)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_t = make_fake_tenant("t1")
    db.query.return_value.filter.return_value.first.return_value = fake_t

    db.query.return_value.count.return_value = 10
    db.query.return_value.filter.return_value.count.side_effect = [8, 1, 1, 0]
    db.query.return_value.order_by.return_value.all.return_value = []

    res_an = await fn_analytics(req, "t1", db, user)
    assert res_an is not None

    with patch("app.services.export_service.export_tenant_data", AsyncMock(return_value={"tenant_id": "t1"})):
        res_exp = await fn_export(req, "t1", db, user)
        assert res_exp is not None


@pytest.mark.asyncio
async def test_superadmin_tenant_suspension_and_reactivation():
    from app.api.v1.superadmin.router import (
        suspend_tenant_endpoint,
        reactivate_tenant_endpoint,
    )
    from app.api.v1.superadmin.schemas import TenantSuspendRequest

    fn_susp = get_handler(suspend_tenant_endpoint)
    fn_react = get_handler(reactivate_tenant_endpoint)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_t = make_fake_tenant("t-susp", status="active")
    db.query.return_value.filter.return_value.first.return_value = fake_t

    with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()), \
         patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()), \
         patch("app.events.publisher.publish_tenant_suspended", AsyncMock()), \
         patch("app.api.v1.superadmin.router._log_action"):
        res_s = await fn_susp(req, "t-susp", TenantSuspendRequest(reason="Payment Overdue"), db, user)
        assert res_s is not None

    with patch("app.services.tenant_service.remove_tenant_suspension_cache", AsyncMock()), \
         patch("app.events.publisher.publish_tenant_reactivated", AsyncMock()), \
         patch("app.api.v1.superadmin.router._log_action"):
        res_r = await fn_react(req, "t-susp", db, user)
        assert res_r is not None


@pytest.mark.asyncio
async def test_superadmin_user_and_email_management():
    from app.api.v1.superadmin.router import (
        send_new_admin_email,
        create_user,
        update_user,
        delete_user,
        list_users,
    )
    from app.api.v1.superadmin.schemas import SuperAdminCreate, SuperAdminUpdate, SuperAdminDelete

    fn_email = get_handler(send_new_admin_email)
    fn_create = get_handler(create_user)
    fn_update = get_handler(update_user)
    fn_delete = get_handler(delete_user)
    fn_list = get_handler(list_users)

    req = make_req()
    user = make_user()
    db = MagicMock()

    sa_id = uuid4()
    fake_admin = MagicMock(
        id=1,
        super_admin_id=sa_id,
        username="sa_user",
        email="sa@hospital.org",
        full_name="Super Admin",
        role="super_admin",
        is_active=True,
        last_login_at=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db.query.return_value.all.return_value = [fake_admin]
    db.query.return_value.filter.return_value.first.return_value = fake_admin

    def mock_admin_refresh(a):
        a.id = 1
        a.super_admin_id = sa_id
        a.username = "sa_user"
        a.email = "sa@hospital.org"
        a.full_name = "Super Admin"
        a.role = "super_admin"
        a.is_active = True
        a.last_login_at = None
        a.created_at = datetime.now(timezone.utc)
        a.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_admin_refresh

    with patch("app.services.superadmin_auth.create_superadmin", return_value=fake_admin), \
         patch("app.api.v1.superadmin.router.ensure_roles", AsyncMock()), \
         patch("app.api.v1.superadmin.router.create_keycloak_user", AsyncMock(return_value=str(uuid4()))), \
         patch("app.api.v1.superadmin.router.delete_keycloak_user", AsyncMock()), \
         patch("app.services.keycloak_admin.update_keycloak_user", AsyncMock()), \
         patch("app.services.keycloak_admin.delete_keycloak_user", AsyncMock()), \
         patch("app.services.keycloak_admin._get_admin_token", AsyncMock(return_value="mock_token")), \
         patch("app.api.v1.superadmin.router.create_local_user", return_value=fake_admin), \
         patch("app.api.v1.superadmin.router.send_new_admin_email", AsyncMock()), \
         patch("app.api.v1.superadmin.router._log_action"):
        await send_new_admin_email("sa@hospital.org", "sa_user", "TempPass123", "MFA123", ["code1"])

        db.query.return_value.filter.return_value.first.return_value = None
        res_create = await fn_create(req, SuperAdminCreate(username="sa_user", email="sa@hospital.org", password="Pass123!", full_name="Super Admin"), db, user)
        assert res_create is not None

        db.query.return_value.filter.return_value.first.return_value = fake_admin
        res_update = await fn_update(req, str(sa_id), SuperAdminUpdate(full_name="Updated Admin"), db, user)
        assert res_update is not None

        db.query.return_value.filter.return_value.first.return_value = fake_admin
        await fn_delete(req, SuperAdminDelete(username="sa_user"), db, user)

        users = await fn_list(req, db, user)
        assert len(users) == 1


@pytest.mark.asyncio
async def test_superadmin_tenant_management():
    from app.api.v1.superadmin.router import (
        create_tenant,
        update_tenant,
        get_tenant,
        terminate_tenant_endpoint,
    )
    from app.api.v1.superadmin.schemas import TenantCreate, TenantUpdate, TenantTerminateRequest
    from fastapi import BackgroundTasks

    fn_create = get_handler(create_tenant)
    fn_update = get_handler(update_tenant)
    fn_get = get_handler(get_tenant)
    fn_term = get_handler(terminate_tenant_endpoint)

    req = make_req()
    user = make_user()
    db = MagicMock()

    fake_t = make_fake_tenant("t-manage")
    fake_plan = MagicMock(plan_name="standard", is_active=True)
    db.query.return_value.filter.return_value.first.return_value = fake_t

    def mock_t_refresh(t):
        t.id = 1
        t.tenant_id = getattr(t, "tenant_id", "t-manage")
        t.hospital_name = getattr(t, "hospital_name", "Hospital Manage")
        t.subscription_plan = "free_trial"
        t.has_used_trial = False
        t.auto_renew = True
        t.status = "active"
        t.is_active = True
        t.created_at = datetime.now(timezone.utc)
        t.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_t_refresh

    with patch("app.services.provision.provision_tenant_database", AsyncMock()), \
         patch("app.services.provision.provision_tenant_database_sync", return_value="postgresql://mock"), \
         patch("app.services.provision.get_tenant_db_session", return_value=MagicMock()), \
         patch("app.api.v1.superadmin.router.setup_tenant_realm", AsyncMock()), \
         patch("app.api.v1.superadmin.router.verify_tenant_realm_exists", AsyncMock(return_value=True)), \
         patch("app.api.v1.superadmin.router.ensure_roles", AsyncMock()), \
         patch("app.api.v1.superadmin.router.create_keycloak_user", AsyncMock(return_value=str(uuid4()))), \
         patch("app.services.keycloak_admin.set_user_attribute", AsyncMock()), \
         patch("app.services.keycloak_admin.update_keycloak_user", AsyncMock()), \
         patch("app.services.keycloak_admin.delete_keycloak_user", AsyncMock()), \
         patch("app.services.keycloak_admin._get_admin_token", AsyncMock(return_value="mock_token")), \
         patch("app.api.v1.superadmin.router.create_local_user", return_value=MagicMock()), \
         patch("app.services.keycloak_admin.create_local_user", return_value=MagicMock()), \
         patch("app.services.subscription_service.subscribe_tenant"), \
         patch("app.api.v1.superadmin.router._send_hospital_admin_welcome_email"), \
         patch("app.events.publisher.publish_tenant_created", AsyncMock()), \
         patch("app.api.v1.superadmin.router._log_action"):
        db.query.return_value.filter.return_value.first.side_effect = [None, fake_plan]
        t_created = await fn_create(req, TenantCreate(tenant_id="t-manage", hospital_name="Hospital Manage", admin_username="admin_manage", admin_email="admin@manage.org", subscription_plan="standard"), BackgroundTasks(), db, user)
        assert t_created is not None

        db.query.return_value.filter.return_value.first.side_effect = None
        db.query.return_value.filter.return_value.first.return_value = fake_t
        t_got = await fn_get(req, "t-manage", db, user)
        assert t_got is not None

        t_upd = await fn_update(req, "t-manage", TenantUpdate(name="Updated Hospital"), db, user)
        assert t_upd is not None

    with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()), \
         patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()), \
         patch("app.api.v1.superadmin.router.delete_keycloak_realm_if_needed", AsyncMock()), \
         patch("app.services.provision.drop_tenant_database"), \
         patch("app.events.publisher.publish_tenant_suspended", AsyncMock()), \
         patch("app.api.v1.superadmin.router._log_action"):
        res_term = await fn_term(req, "t-manage", TenantTerminateRequest(reason="Terminated by admin"), db, user)
        assert res_term is not None


@pytest.mark.asyncio
async def test_superadmin_subscription_endpoints():
    from app.api.v1.superadmin.router import (
        list_plans,
        create_plan,
        update_plan,
        delete_plan,
        get_subscription,
        subscribe_tenant_endpoint,
        upgrade_tenant_endpoint,
        downgrade_tenant_endpoint,
        renew_tenant_endpoint,
        list_subscription_plans,
        list_tenant_subscriptions,
        list_tenant_subscription_audit_log,
        list_all_subscriptions,
    )
    from app.api.v1.superadmin.schemas import (
        PlanCreate,
        PlanUpdate,
        SubscriptionSubscribeRequest,
        SubscriptionPlanChangeRequest,
        SubscriptionRenewRequest,
    )

    fn_list_p = get_handler(list_plans)
    fn_create_p = get_handler(create_plan)
    fn_upd_p = get_handler(update_plan)
    fn_del_p = get_handler(delete_plan)
    fn_get_sub = get_handler(get_subscription)
    fn_sub = get_handler(subscribe_tenant_endpoint)
    fn_upg = get_handler(upgrade_tenant_endpoint)
    fn_down = get_handler(downgrade_tenant_endpoint)
    fn_renew = get_handler(renew_tenant_endpoint)
    fn_list_sp = get_handler(list_subscription_plans)
    fn_list_ts = get_handler(list_tenant_subscriptions)
    fn_list_audit = get_handler(list_tenant_subscription_audit_log)
    fn_list_all_sub = get_handler(list_all_subscriptions)

    req = make_req()
    user = make_user()
    db = MagicMock()

    plan_uuid = uuid4()
    sub_uuid = uuid4()
    fake_p = MagicMock(
        id=1,
        plan_id=plan_uuid,
        plan_name="enterprise",
        description="Enterprise Plan",
        display_name="Enterprise Plan",
        monthly_price=500.0,
        annual_price=5000.0,
        annual_discount_pct=10.0,
        max_users=100,
        max_storage_gb=500,
        storage_gb=500,
        modules_included=["all"],
        uptime_sla_pct=99.9,
        backup_frequency_hours=24,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    fake_sub = MagicMock(
        id=1,
        subscription_id=sub_uuid,
        plan_id=plan_uuid,
        tenant_id="t1",
        plan_name="enterprise",
        billing_cycle="monthly",
        status="active",
        start_date=date.today(),
        end_date=date.today() + timedelta(days=30),
        auto_renew=True,
        max_users=100,
        max_storage_gb=500,
        cancellation_reason=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    fake_t = make_fake_tenant("t1")
    fake_sub_act = MagicMock(
        action_id=uuid4(),
        tenant_id="t1",
        subscription_id=sub_uuid,
        action="subscribe",
        previous_status="active",
        previous_plan="standard",
        plan_name="enterprise",
        performed_by="admin",
        details={},
        created_at=datetime.now(timezone.utc),
        tenant=fake_t,
    )

    db.query.return_value.order_by.return_value.all.return_value = [fake_p]
    db.query.return_value.filter.return_value.first.return_value = fake_p
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_sub_act]

    def mock_p_refresh(p):
        p.id = 1
        p.plan_id = plan_uuid
        p.plan_name = "enterprise"
        p.description = "Enterprise Plan"
        p.display_name = "Enterprise Plan"
        p.monthly_price = 500.0
        p.annual_price = 5000.0
        p.annual_discount_pct = 10.0
        p.max_users = 100
        p.max_storage_gb = 500
        p.storage_gb = 500
        p.modules_included = ["all"]
        p.uptime_sla_pct = 99.9
        p.backup_frequency_hours = 24
        p.is_active = True
        p.created_at = datetime.now(timezone.utc)
        p.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_p_refresh

    with patch("app.api.v1.superadmin.router._log_action"):
        plans = await fn_list_p(req, db, user)
        assert len(plans) == 1

        db.query.return_value.filter.return_value.first.return_value = None
        new_plan = await fn_create_p(req, PlanCreate(plan_name="enterprise", display_name="Enterprise Plan", monthly_price=500.0, annual_price=5000.0, max_users=100, max_storage_gb=500), db, user)
        assert new_plan is not None

        db.query.return_value.filter.return_value.first.return_value = fake_p
        upd_plan = await fn_upd_p(req, "enterprise", PlanUpdate(display_name="Enterprise Plus"), db, user)
        assert upd_plan is not None

        await fn_del_p(req, "enterprise", db, user)

    fake_state = {
        "tenant_id": "t1",
        "name": "Hospital 1",
        "is_active": True,
        "is_trial": False,
        "status": "active",
        "subscription": {
            "plan": "enterprise",
            "plan_name": "enterprise",
            "display_name": "Enterprise Plan",
            "billing_cycle": "monthly",
            "status": "active",
            "start": datetime.now(timezone.utc),
            "end": None,
            "subscription_end": None,
            "grace_period_end": None,
            "auto_renew": True,
            "max_users": 100,
            "max_storage_gb": 500,
            "is_expired": False,
            "in_grace_period": False,
            "has_used_trial": False,
        },
        "suspension": {
            "is_suspended": False,
            "suspended_at": None,
            "suspended_reason": None,
            "reason": None,
            "reactivated_at": None,
        },
        "termination": {
            "is_terminated": False,
            "terminated_at": None,
            "termination_reason": None,
            "reason": None,
        },
        "payment_provider_id": None,
    }
    with patch("app.api.v1.superadmin.router._serialize_state", return_value=fake_state), \
         patch("app.services.subscription_service.subscribe_tenant", return_value=fake_sub_act), \
         patch("app.services.subscription_service.upgrade_subscription", return_value=fake_sub_act), \
         patch("app.services.subscription_service.downgrade_subscription", return_value=fake_sub_act), \
         patch("app.services.subscription_service.renew_subscription", return_value=fake_sub_act), \
         patch("app.api.v1.superadmin.router._trigger_invoice_email_dispatch"), \
         patch("app.api.v1.superadmin.router._log_action"):
        sub_st = await fn_get_sub(req, "t1", db, user)
        assert sub_st is not None

        db.query.return_value.filter.return_value.first.return_value = fake_t
        res_sub = await fn_sub(req, "t1", SubscriptionSubscribeRequest(plan="premium"), db, user)
        assert res_sub is not None

        res_upg = await fn_upg(req, "t1", SubscriptionPlanChangeRequest(plan="premium"), BackgroundTasks(), db, user)
        assert res_upg is not None

        res_down = await fn_down(req, "t1", SubscriptionPlanChangeRequest(plan="standard"), BackgroundTasks(), db, user)
        assert res_down is not None

        res_renew = await fn_renew(req, "t1", SubscriptionRenewRequest(billing_cycle="monthly"), BackgroundTasks(), db, user)
        assert res_renew is not None

        db.query.return_value.all.return_value = [fake_p]
        sp_list = await fn_list_sp(req, db, user)
        assert len(sp_list) == 1

        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_sub]
        ts_list = await fn_list_ts(req, "t1", db, user)
        assert len(ts_list) == 1

        fake_audit_log = MagicMock(
            event_id=uuid4(),
            subscription_id=sub_uuid,
            event_type="subscription_created",
            actor_id=uuid4(),
            actor_type="user",
            tenant_id="t1",
            reason=None,
            details={},
            created_at=datetime.now(timezone.utc),
        )
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_audit_log]
        audit_list = await fn_list_audit(req, "t1", db, user)
        assert len(audit_list) == 1

        db.query.return_value.order_by.return_value.all.return_value = [fake_sub]
        all_subs = await fn_list_all_sub(req, None, db, user)
        assert len(all_subs) == 1


@pytest.mark.asyncio
async def test_superadmin_global_roles_and_keycloak():
    from app.api.v1.superadmin.router import (
        create_global_role,
        list_global_roles,
        get_global_role,
        update_global_role,
        delete_global_role,
        list_all_roles,
        list_keycloak_realms,
        list_all_keycloak_users,
    )
    from app.api.v1.superadmin.schemas import GlobalRoleCreate, GlobalRoleUpdate

    fn_create_g = get_handler(create_global_role)
    fn_list_g = get_handler(list_global_roles)
    fn_get_g = get_handler(get_global_role)
    fn_upd_g = get_handler(update_global_role)
    fn_del_g = get_handler(delete_global_role)
    fn_list_roles = get_handler(list_all_roles)
    fn_realms = get_handler(list_keycloak_realms)
    fn_kc_users = get_handler(list_all_keycloak_users)

    req = make_req()
    user = make_user()
    db = MagicMock()

    g_uuid = uuid4()
    creator_uuid = uuid4()
    fake_g = MagicMock(
        global_role_id=g_uuid,
        system_role_id=g_uuid,
        description="Global Admin Access",
        scope={},
        is_global=True,
        created_by=str(creator_uuid),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    fake_g.name = "global_super_admin"

    db.query.return_value.filter.return_value.first.return_value = fake_g
    db.query.return_value.order_by.return_value.all.return_value = [fake_g]
    db.query.return_value.all.return_value = []

    def mock_g_refresh(g):
        g.global_role_id = g_uuid
        g.system_role_id = g_uuid
        g.name = "global_super_admin"
        g.description = "Global Admin Access"
        g.scope = {}
        g.is_global = True
        g.created_by = str(creator_uuid)
        g.created_at = datetime.now(timezone.utc)
        g.updated_at = datetime.now(timezone.utc)

    db.refresh.side_effect = mock_g_refresh

    with patch("app.api.v1.superadmin.router._log_action"):
        db.query.return_value.filter.return_value.first.return_value = None
        new_g = await fn_create_g(req, GlobalRoleCreate(name="global_super_admin", description="Global Admin Access"), db, user)
        assert new_g is not None

        db.query.return_value.filter.return_value.first.return_value = fake_g
        g_list = await fn_list_g(req, db, user)
        assert len(g_list) == 1

        g_got = await fn_get_g(req, g_uuid, db, user)
        assert g_got is not None

        g_upd = await fn_upd_g(req, g_uuid, GlobalRoleUpdate(description="Updated Global Access"), db, user)
        assert g_upd is not None

        await fn_del_g(req, g_uuid, db, user)

    def mock_query(model):
        m = MagicMock()
        if hasattr(model, "__name__") and model.__name__ == "GlobalRole":
            m.filter.return_value.first.return_value = fake_g
            m.order_by.return_value.all.return_value = [fake_g]
            m.all.return_value = [fake_g]
        elif hasattr(model, "__name__") and model.__name__ == "TenantRole":
            m.order_by.return_value.all.return_value = []
            m.all.return_value = []
        else:
            m.filter.return_value.first.return_value = fake_g
            m.order_by.return_value.all.return_value = [fake_g]
            m.all.return_value = [fake_g]
        return m

    db.query.side_effect = mock_query

    with patch("app.api.v1.superadmin.router.list_all_realms", AsyncMock(return_value=["master", "t1"])), \
         patch("app.api.v1.superadmin.router.get_all_realm_users", AsyncMock(return_value=[{"username": "kc_admin"}])):
        roles_all = await fn_list_roles(req, db, user)
        assert roles_all is not None

        realms = await fn_realms(req, user)
        assert realms == ["master", "t1"]

        kc_users = await fn_kc_users(req, user)
        assert len(kc_users) == 2


@pytest.mark.asyncio
async def test_superadmin_telemetry_and_audit():
    from app.api.v1.superadmin.router import (
        list_super_admin_audit_log,
        get_tenant_usage_stats,
        get_aggregated_usage_telemetry,
    )
    fn_audit = get_handler(list_super_admin_audit_log)
    fn_stats = get_handler(get_tenant_usage_stats)
    fn_telem = get_handler(get_aggregated_usage_telemetry)

    req = make_req()
    user = make_user()
    db = MagicMock()

    sa_id = uuid4()
    fake_log = MagicMock(
        id=1,
        log_id=uuid4(),
        super_admin_id=sa_id,
        user_sub=str(sa_id),
        action="tenant.suspend",
        tenant_id="t1",
        target_tenant_id="t1",
        detail="{}",
        details={},
        ip_address="127.0.0.1",
        created_at=datetime.now(timezone.utc),
    )
    db.query.return_value.order_by.return_value.all.return_value = [fake_log]
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_log]

    fake_t = make_fake_tenant("t1")
    db.query.return_value.filter.return_value.first.return_value = fake_t
    db.query.return_value.count.return_value = 5

    logs = await fn_audit(req, db, user)
    assert len(logs) == 1

    stats = await fn_stats(req, "t1", db, user)
    assert stats is not None

    telem = await fn_telem(req, db, user)
    assert telem is not None

