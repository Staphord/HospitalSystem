"""Unit tests for background suspension job, trial expiry checks, and invoice jobs in master-service.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from datetime import datetime, timezone, timedelta, date

from app.services import suspension_job
from app.services.suspension_job import (
    run_suspension_check,
    run_trial_expiry_check,
    _send_invoice_email_direct,
    run_renewal_invoices_generation,
    run_overdue_payment_reminders,
    run_invoice_overdue_suspensions,
    suspension_loop,
)

class TestSuspensionJob:
    @pytest.mark.asyncio
    async def test_run_suspension_check(self):
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [("t1",), ("t2",)]
        with patch("app.services.suspension_job.get_master_db", return_value=mock_db):
            with patch("app.services.suspension_job.check_and_update_tenant_status", AsyncMock(side_effect=["suspended", "active"])):
                res = await run_suspension_check()
                assert res == 1

    @pytest.mark.asyncio
    async def test_run_trial_expiry_check(self):
        mock_db = MagicMock()
        now = datetime.now(timezone.utc)
        past = now - timedelta(days=10)
        future = now + timedelta(days=5)

        mock_db.execute.return_value.fetchall.return_value = [
            (1, "t1", past, future),
            (2, "t2", past, past),
        ]

        with patch("app.services.suspension_job.get_master_db", return_value=mock_db):
            with patch("app.services.suspension_job.cache_tenant_suspension", AsyncMock()):
                res = await run_trial_expiry_check()
                assert res == 1

    @pytest.mark.asyncio
    async def test_send_invoice_email_direct(self):
        with patch("app.config.settings.smtp_user", ""):
            await _send_invoice_email_direct("admin@t1.org", "H1", "INV-1", 100.0, "USD", "2026-09-01", "Plan", "2026-08-01", "2026-08-31")

        with patch("app.config.settings.smtp_user", "smtp_usr"):
            with patch("app.config.settings.smtp_password", "smtp_pwd"):
                with patch("aiosmtplib.send", AsyncMock()):
                    await _send_invoice_email_direct("admin@t1.org", "H1", "INV-1", 100.0, "USD", "2026-09-01", "Plan", "2026-08-01", "2026-08-31")

    @pytest.mark.asyncio
    async def test_run_renewal_invoices_generation(self):
        mock_db = MagicMock()
        fake_t = MagicMock(
            tenant_id="t1",
            subscription_plan="standard",
            subscription_billing_cycle="monthly",
            subscription_end=datetime.now(timezone.utc) + timedelta(days=5),
            billing_email="b@t1.org",
            primary_contact_email="p@t1.org",
            hospital_name="H1",
        )
        mock_db.query.return_value.filter.return_value.all.return_value = [fake_t]
        mock_db.query.return_value.filter.return_value.first.side_effect = [
            None,
            MagicMock(subscription_id=1),
            MagicMock(invoice_number="INV-NEW", amount=100.0, currency="USD", due_date=date(2026, 9, 1), plan_name="standard", billing_period_start=date(2026, 8, 1), billing_period_end=date(2026, 8, 31)),
        ]

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.services.subscription_service._generate_invoice"):
                with patch("app.services.suspension_job._send_invoice_email_direct", AsyncMock()):
                    with patch("app.events.publisher.publish_subscription_invoice_generated", AsyncMock()):
                        res = await run_renewal_invoices_generation()
                        assert res == 1

    @pytest.mark.asyncio
    async def test_run_overdue_payment_reminders(self):
        mock_db = MagicMock()
        fake_inv = MagicMock(
            invoice_number="INV-OVERDUE",
            tenant_id="t1",
            amount=200.0,
            currency="USD",
            due_date=date(2026, 8, 1),
            reminder_sent_at=None,
        )
        fake_t = MagicMock(billing_email="b@t1.org", primary_contact_email="p@t1.org")

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_inv]
        mock_db.query.return_value.filter.return_value.first.return_value = fake_t

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.config.settings.smtp_user", ""):
                res = await run_overdue_payment_reminders()
                assert res == 1

    @pytest.mark.asyncio
    async def test_run_overdue_payment_reminders_smtp(self):
        mock_db = MagicMock()
        fake_inv = MagicMock(
            invoice_number="INV-OVERDUE-SMTP",
            tenant_id="t1",
            amount=200.0,
            currency="USD",
            due_date=date(2026, 8, 1),
            reminder_sent_at=None,
        )
        fake_t = MagicMock(billing_email="b@t1.org", primary_contact_email="p@t1.org")

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_inv]
        mock_db.query.return_value.filter.return_value.first.return_value = fake_t

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.config.settings.smtp_user", "smtp_user"), patch("app.config.settings.smtp_password", "secret"):
                with patch("app.events.publisher.publish_subscription_invoice_overdue", AsyncMock()):
                    with patch("aiosmtplib.send", AsyncMock()):
                        res = await run_overdue_payment_reminders()
                        assert res == 1

    @pytest.mark.asyncio
    async def test_run_invoice_overdue_suspensions(self):
        mock_db = MagicMock()
        fake_t = MagicMock(
            tenant_id="t1",
            grace_period_days=14,
            status="active",
            is_active=True,
            suspended_reason=None,
        )
        fake_inv = MagicMock(invoice_number="INV-999")

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_t]
        mock_db.query.return_value.filter.return_value.first.return_value = fake_inv

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()):
                with patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()):
                    with patch("app.events.publisher.publish_tenant_suspended", AsyncMock()):
                        res = await run_invoice_overdue_suspensions()
                        assert res == 1

    @pytest.mark.asyncio
    async def test_suspension_loop(self):
        with patch("app.services.suspension_job.run_suspension_check", AsyncMock(side_effect=Exception("error"))), \
             patch("app.services.suspension_job.run_trial_expiry_check", AsyncMock()), \
             patch("app.services.suspension_job.run_renewal_invoices_generation", AsyncMock()), \
             patch("app.services.suspension_job.run_overdue_payment_reminders", AsyncMock()), \
             patch("app.services.suspension_job.run_invoice_overdue_suspensions", AsyncMock()), \
             patch("asyncio.sleep", AsyncMock(side_effect=asyncio.CancelledError())):
            with pytest.raises(asyncio.CancelledError):
                await suspension_loop()

    @pytest.mark.asyncio
    async def test_run_overdue_payment_reminders_event_and_mail_error(self):
        mock_db = MagicMock()
        fake_inv = MagicMock(
            invoice_number="INV-ERR",
            tenant_id="t1",
            amount=200.0,
            currency="USD",
            due_date=date(2026, 8, 1),
            reminder_sent_at=None,
        )
        fake_t = MagicMock(billing_email="b@t1.org", primary_contact_email="p@t1.org")

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_inv]
        mock_db.query.return_value.filter.return_value.first.return_value = fake_t

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.config.settings.smtp_user", "smtp_user"), patch("app.config.settings.smtp_password", "secret"):
                with patch("app.events.publisher.publish_subscription_invoice_overdue", AsyncMock(side_effect=Exception("event err"))):
                    with patch("aiosmtplib.send", AsyncMock(side_effect=Exception("smtp err"))):
                        res = await run_overdue_payment_reminders()
                        assert res == 0

    @pytest.mark.asyncio
    async def test_run_invoice_overdue_suspensions_event_error(self):
        mock_db = MagicMock()
        fake_t = MagicMock(
            tenant_id="t1",
            grace_period_days=14,
            status="active",
            is_active=True,
            suspended_reason=None,
        )
        fake_inv = MagicMock(invoice_number="INV-999")

        mock_db.query.return_value.filter.return_value.all.return_value = [fake_t]
        mock_db.query.return_value.filter.return_value.first.return_value = fake_inv

        with patch("app.db.master.get_master_db", return_value=mock_db):
            with patch("app.services.tenant_service.cache_tenant_suspension", AsyncMock()):
                with patch("app.services.tenant_service._revoke_keycloak_sessions", AsyncMock()):
                    with patch("app.events.publisher.publish_tenant_suspended", AsyncMock(side_effect=Exception("evt err"))):
                        res = await run_invoice_overdue_suspensions()
                        assert res == 1

    @pytest.mark.asyncio
    async def test_suspension_job_query_exceptions(self):
        err_db = MagicMock()
        err_db.query.side_effect = Exception("DB Query Error")
        with patch("app.services.suspension_job.get_master_db", return_value=err_db):
            assert await run_suspension_check() == 0
            assert await run_trial_expiry_check() == 0
            assert await run_renewal_invoices_generation() == 0
            assert await run_overdue_payment_reminders() == 0
            assert await run_invoice_overdue_suspensions() == 0

