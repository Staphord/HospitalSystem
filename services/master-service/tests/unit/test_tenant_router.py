"""Unit tests for tenant router handlers in master-service (subscription self-service, plan change requests, invoices, stats, announcements).
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi import HTTPException
from starlette.requests import Request
from datetime import datetime, timezone
from uuid import uuid4

from app.api.v1.tenant import router as tenant_router
from app.api.v1.superadmin.schemas import (
    ToggleAutoRenewRequest,
    SubscriptionSubscribeRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionRenewRequest,
    PlanChangeRequestCreate,
    CancellationRequestCreate,
)
from app.core.security import TokenPayload

def get_handler(fn):
    return getattr(fn, "__wrapped__", fn)

def make_req(method="GET", path="/"):
    return Request(scope={
        "type": "http",
        "method": method,
        "path": path,
        "headers": [],
        "client": ("127.0.0.1", 12345),
    })

def make_user(tenant_id="t1"):
    return TokenPayload(
        sub="user-sub-1",
        preferred_username="tenant_admin",
        email="admin@tenant.org",
        realm_access={},
        raw={"tenant_id": tenant_id},
    )

def make_fake_tenant(tenant_id="t1", status="active", plan="standard"):
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

def make_fake_sub_state(tenant_id="t1", plan="standard"):
    return {
        "tenant_id": tenant_id,
        "name": "Hospital One",
        "status": "active",
        "is_active": True,
        "is_trial": False,
        "subscription": {
            "plan": plan,
            "display_name": plan.title(),
            "status": "active",
            "billing_cycle": "monthly",
            "start": datetime.now(timezone.utc).isoformat(),
            "end": datetime.now(timezone.utc).isoformat(),
            "grace_period_end": None,
            "auto_renew": True,
            "is_expired": False,
            "in_grace_period": False,
            "has_used_trial": False,
            "pending_plan": None,
            "pending_billing_cycle": None,
        },
        "suspension": {
            "suspended_at": None,
            "reason": None,
            "reactivated_at": None,
        },
        "termination": {
            "terminated_at": None,
            "reason": None,
        },
        "payment_provider_id": None,
        "currency": "USD",
        "grace_days": 14,
    }

class TestTenantRouterSelfService:
    def test_get_tenant_id_error_case(self):
        user_no_tenant = TokenPayload(sub="sub1", preferred_username="usr", email="a@b.com", realm_access={}, raw={})
        with pytest.raises(HTTPException) as exc:
            tenant_router._get_tenant_id(user_no_tenant)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_my_subscription_and_toggle_auto_renew(self):
        req = make_req()
        db = MagicMock()
        user = make_user("t1")
        fake_t = make_fake_tenant("t1")
        db.query.return_value.filter.return_value.first.return_value = fake_t

        fn_get = get_handler(tenant_router.get_my_subscription)
        with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1")):
            res = await fn_get(req, db, user)
            assert res is not None

        fn_toggle = get_handler(tenant_router.toggle_my_auto_renew)
        fake_sub = MagicMock(auto_renew=True)
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = fake_sub
        with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1")):
            with patch("app.services.subscription_request_service.log_action"):
                res_t = await fn_toggle(req, ToggleAutoRenewRequest(auto_renew=False), db, user)
                assert res_t is not None

    @pytest.mark.asyncio
    async def test_subscribe_upgrade_downgrade_renew(self):
        req = make_req("POST")
        db = MagicMock()
        user = make_user("t1")

        fake_res = MagicMock()
        fake_res.action = "subscribe"
        fake_res.previous_status = "trial"
        fake_res.previous_plan = "free_trial"
        fake_res.tenant = make_fake_tenant("t1")

        # Subscribe
        fn_sub = get_handler(tenant_router.subscribe_my_subscription)
        with patch("app.api.v1.tenant.router.subscription_service_module.subscribe_tenant", return_value=fake_res):
            with patch("app.services.tenant_service.remove_tenant_suspension_cache", AsyncMock()):
                with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1", "standard")):
                    res_s = await fn_sub(req, SubscriptionSubscribeRequest(plan="standard", billing_cycle="monthly"), db, user)
                    assert res_s.action == "subscribe"

        # Upgrade
        fake_res.action = "upgrade"
        fn_upg = get_handler(tenant_router.upgrade_my_subscription)
        with patch("app.api.v1.tenant.router.subscription_service_module.upgrade_subscription", return_value=fake_res):
            with patch("app.services.tenant_service.remove_tenant_suspension_cache", AsyncMock()):
                with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1", "premium")):
                    res_u = await fn_upg(req, SubscriptionPlanChangeRequest(plan="premium", billing_cycle="annual"), db, user)
                    assert res_u.action == "upgrade"

        # Downgrade
        fake_res.action = "downgrade"
        fn_down = get_handler(tenant_router.downgrade_my_subscription)
        with patch("app.api.v1.tenant.router.subscription_service_module.downgrade_subscription", return_value=fake_res):
            with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1", "basic")):
                res_d = await fn_down(req, SubscriptionPlanChangeRequest(plan="basic", billing_cycle="monthly"), db, user)
                assert res_d.action == "downgrade"

        # Renew
        fake_res.action = "renew"
        fn_renew = get_handler(tenant_router.renew_my_subscription)
        with patch("app.api.v1.tenant.router.subscription_service_module.renew_subscription", return_value=fake_res):
            with patch("app.services.tenant_service.remove_tenant_suspension_cache", AsyncMock()):
                with patch("app.api.v1.tenant.router.get_subscription_state", return_value=make_fake_sub_state("t1", "basic")):
                    res_r = await fn_renew(req, SubscriptionRenewRequest(billing_cycle="monthly"), db, user)
                    assert res_r.action == "renew"

    @pytest.mark.asyncio
    async def test_requests_and_pdfs_and_stats(self):
        req = make_req()
        db = MagicMock()
        user = make_user("t1")
        fake_t = make_fake_tenant("t1")
        db.query.return_value.filter.return_value.first.return_value = fake_t

        # Request plan change
        fn_change = get_handler(tenant_router.request_plan_change)
        with patch("app.api.v1.tenant.router.create_plan_change_request", return_value=fake_t):
            with patch("app.api.v1.tenant.router.get_pending_request", return_value={"request_id": str(uuid4()), "action": "upgrade", "requested_plan": "premium", "status": "pending"}):
                res_c = await fn_change(req, PlanChangeRequestCreate(plan="premium", reason="Expansion"), db, user)
                assert res_c.tenant_id == "t1"

        # Request cancellation
        fn_cancel = get_handler(tenant_router.request_cancellation)
        with patch("app.api.v1.tenant.router.create_cancellation_request", return_value=fake_t):
            with patch("app.api.v1.tenant.router.get_pending_request", return_value={"request_id": str(uuid4()), "action": "cancellation", "reason": "Closing branch", "status": "pending"}):
                res_can = await fn_cancel(req, CancellationRequestCreate(reason="Closing branch"), db, user)
                assert res_can.tenant_id == "t1"

        # Invoice PDF download
        fake_inv = MagicMock(invoice_id=uuid4(), invoice_number="INV-001", amount=100.0, currency="USD", due_date=None, billing_period_start=None, billing_period_end=None, status="paid", plan_name="standard")
        db.query.return_value.filter.return_value.first.return_value = fake_inv
        fn_pdf = get_handler(tenant_router.download_invoice_pdf)
        with patch("app.services.pdf_generator.generate_invoice_pdf", return_value=b"%PDF-test"):
            res_pdf = await fn_pdf(req, fake_inv.invoice_id, db, user)
            assert res_pdf.media_type == "application/pdf"

        # Receipt PDF download
        fake_pay = MagicMock(amount=100.0, payment_method="stripe", reference_number="ref1", paid_at=datetime.now(timezone.utc))
        db.query.return_value.filter.return_value.order_by.return_value.first.return_value = fake_pay
        fn_rec = get_handler(tenant_router.download_receipt_pdf)
        with patch("app.services.pdf_generator.generate_receipt_pdf", return_value=b"%PDF-receipt"):
            res_rec = await fn_rec(req, fake_inv.invoice_id, db, user)
            assert res_rec.media_type == "application/pdf"

        # List plans
        fn_plans = get_handler(tenant_router.list_plans)
        fake_plan_db = MagicMock(plan_id=uuid4(), plan_name="standard", monthly_price=99.0, annual_price=990.0, max_users=50, storage_gb=100, modules_included=["billing"], uptime_sla_pct=99.9, backup_frequency_hours=24)
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_plan_db]
        res_plans = await fn_plans(req, db, user)
        assert len(res_plans) == 1

        # Stats
        db.query.return_value.filter.return_value.first.return_value = fake_t
        fn_stats = get_handler(tenant_router.get_my_tenant_stats)
        
        mock_tdb = MagicMock()
        mock_tdb.execute.side_effect = [
            MagicMock(scalar=lambda: 10), # total users
            MagicMock(scalar=lambda: 8),  # active users
            MagicMock(scalar=lambda: 100), # patients
            MagicMock(scalar=lambda: 104857600), # db size
        ]
        mock_tdb.get_bind.return_value.url.database = "hosp_t1"

        with patch("app.services.provision.get_tenant_db_session", return_value=mock_tdb):
            res_stats = await fn_stats(req, db, user)
            assert res_stats["tenant_id"] == "t1"
            assert res_stats["user_count"] == 10

    @pytest.mark.asyncio
    async def test_tenant_router_more_coverage(self):
        req = make_req()
        db = MagicMock()
        user = make_user("t1")
        fake_t = make_fake_tenant("t1")

        # get_my_request_status with all=True
        db.query.return_value.filter.return_value.first.return_value = fake_t
        fn_req_status = get_handler(tenant_router.get_my_request_status)
        with patch("app.services.subscription_request_service.list_subscription_requests", return_value=[]):
            res_all_reqs = await fn_req_status(req, all=True, status=None, db=db, user=user)
            assert isinstance(res_all_reqs, list)

        # list_my_invoices
        fake_inv = MagicMock(
            invoice_id=uuid4(),
            tenant_id="t1",
            invoice_number="INV-001",
            subscription_id=uuid4(),
            hospital_name="Hospital One",
            amount=100.0,
            amount_paid=100.0,
            currency="USD",
            due_date=datetime.now(timezone.utc).date(),
            billing_period_start=datetime.now(timezone.utc).date(),
            billing_period_end=datetime.now(timezone.utc).date(),
            status="paid",
            issued_at=datetime.now(timezone.utc),
            plan_name="standard",
            payment_method="stripe",
            reference_number="ref1",
            notes=None,
            created_at=datetime.now(timezone.utc),
        )
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_inv]
        fn_invoices = get_handler(tenant_router.list_my_invoices)
        invoices = await fn_invoices(req, db, user)
        assert len(invoices) == 1

        # list_my_audit_log
        fake_log = MagicMock(
            log_id=uuid4(),
            event_id=uuid4(),
            subscription_id=uuid4(),
            tenant_id="t1",
            event_type="subscription_subscribed",
            action="subscription.subscribe",
            actor="admin",
            actor_id=uuid4(),
            actor_type="user",
            reason="User subscribed",
            details={},
            ip_address="127.0.0.1",
            created_at=datetime.now(timezone.utc),
        )
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_log]
        fn_audit = get_handler(tenant_router.list_my_audit_log)
        logs = await fn_audit(req, db, user)
        assert len(logs) == 1

        # get_my_announcements
        fake_ann = MagicMock(
            announcement_id=uuid4(),
            title="Update",
            body="System upgrade scheduled",
            audience="all",
            target_tenant_ids=[],
            publish_at=datetime.now(timezone.utc),
            expires_at=None,
            created_by=uuid4(),
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        db.query.return_value.filter.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_ann]
        fn_ann = get_handler(tenant_router.get_my_announcements)
        anns = await fn_ann(req, db, user)
        assert len(anns) == 1

    @pytest.mark.asyncio
    async def test_tenant_router_error_branches(self):
        req = make_req()
        db = MagicMock()
        user = make_user("t1")

        # Missing tenant error
        db.query.return_value.filter.return_value.first.return_value = None
        fn_get = get_handler(tenant_router.get_my_subscription)
        with pytest.raises(HTTPException) as exc:
            await fn_get(req, db, user)
        assert exc.value.status_code == 404

        # Toggle auto renew missing tenant / sub
        fn_toggle = get_handler(tenant_router.toggle_my_auto_renew)
        with pytest.raises(HTTPException) as exc:
            await fn_toggle(req, ToggleAutoRenewRequest(auto_renew=True), db, user)
        assert exc.value.status_code == 404

        # Invalid plan subscribe
        fn_sub = get_handler(tenant_router.subscribe_my_subscription)
        with pytest.raises(HTTPException) as exc:
            await fn_sub(req, SubscriptionSubscribeRequest(plan="invalid_plan", billing_cycle="monthly"), db, user)
        assert exc.value.status_code == 400

        # Download invoice PDF missing invoice
        fn_pdf = get_handler(tenant_router.download_invoice_pdf)
        with pytest.raises(HTTPException) as exc:
            await fn_pdf(req, uuid4(), db, user)
        assert exc.value.status_code == 404
