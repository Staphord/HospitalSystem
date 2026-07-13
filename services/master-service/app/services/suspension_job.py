"""Background job that expires subscriptions and auto-suspends tenants.

Runs on the interval configured by SUSPENSION_CHECK_INTERVAL (default 24h).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db.master import get_master_db
from app.services.subscription_plans import SubscriptionStatus
from app.services.tenant_service import (
    cache_tenant_suspension,
    check_and_update_tenant_status,
)

logger = logging.getLogger("hospital.suspension")


async def run_suspension_check() -> int:
    checked = 0
    suspended = 0
    db = get_master_db()
    try:
        rows = db.execute(
            text(
                "SELECT tenant_id FROM tenants "
                "WHERE is_active = true AND status != 'suspended'"
            )
        ).fetchall()
        for row in rows:
            checked += 1
            result = await check_and_update_tenant_status(db, row[0])
            if result == "suspended":
                suspended += 1
        db.commit()
    finally:
        db.close()

    if suspended:
        logger.warning("Suspension check: %d tenants suspended out of %d checked", suspended, checked)
    else:
        logger.info("Suspension check: %d tenants checked, none suspended", checked)
    return suspended


async def run_trial_expiry_check() -> int:
    """Move tenants whose trial has ended into past_due/suspended unless renewed."""
    from app.services.subscription_service import _ensure_aware

    expired = 0
    db = get_master_db()
    try:
        now = datetime.now(timezone.utc)
        rows = db.execute(
            text(
                "SELECT id, tenant_id, trial_end, grace_period_end "
                "FROM tenants "
                "WHERE subscription_status = :trial"
            ),
            {"trial": SubscriptionStatus.TRIAL.value},
        ).fetchall()

        for pk_id, tenant_id, trial_end, grace_end in rows:
            trial_end = _ensure_aware(trial_end)
            grace_end = _ensure_aware(grace_end)
            if trial_end and now > trial_end:
                if grace_end and now <= grace_end:
                    db.execute(
                        text(
                            "UPDATE tenants SET subscription_status = :past_due, status = 'active' "
                            "WHERE id = :id"
                        ),
                        {"past_due": SubscriptionStatus.PAST_DUE.value, "id": pk_id},
                    )
                    logger.info("Tenant %s trial ended, moved to past_due", tenant_id)
                else:
                    db.execute(
                        text(
                            "UPDATE tenants SET subscription_status = :suspended, status = 'suspended', "
                            "is_active = false, suspended_at = :now, suspended_reason = :reason "
                            "WHERE id = :id"
                        ),
                        {
                            "suspended": SubscriptionStatus.SUSPENDED.value,
                            "id": pk_id,
                            "now": now,
                            "reason": "Free trial expired",
                        },
                    )
                    await cache_tenant_suspension(tenant_id)
                    expired += 1
                    logger.warning("Tenant %s trial expired beyond grace period, suspended", tenant_id)

        db.commit()
    finally:
        db.close()

    return expired


async def run_renewal_invoices_generation() -> int:
    """Automatically generate renewal invoices 14 days prior to subscription expiry."""
    from datetime import datetime, timezone, timedelta
    from app.db.master import get_master_db
    from app.models.saas import Invoice as InvoiceRecord, Subscription as SubscriptionModel
    from app.services.subscription_service import _ensure_aware, _generate_invoice
    from app.services.subscription_plans import SubscriptionPlan, BillingCycle, get_plan, subscription_duration_days
    from app.models.master import Tenant

    generated = 0
    db = get_master_db()
    try:
        now = datetime.now(timezone.utc)
        warning_horizon = now + timedelta(days=14)

        # Get active tenants expiring within 14 days
        rows = db.query(Tenant).filter(
            Tenant.is_active == True,
            Tenant.status == "active",
            Tenant.subscription_status == "active",
            Tenant.subscription_end <= warning_horizon,
            Tenant.subscription_end > now
        ).all()

        for tenant in rows:
            current_plan = SubscriptionPlan(tenant.subscription_plan)
            cycle = BillingCycle(tenant.subscription_billing_cycle or "monthly")
            sub_end = _ensure_aware(tenant.subscription_end)
            if not sub_end:
                continue

            # Check if a renewal invoice already exists for the next period
            next_period_start = sub_end.date()
            exists = db.query(InvoiceRecord).filter(
                InvoiceRecord.tenant_id == tenant.tenant_id,
                InvoiceRecord.billing_period_start == next_period_start
            ).first()

            if not exists:
                duration_days = subscription_duration_days(cycle)
                next_period_end = sub_end + timedelta(days=duration_days)

                plan_details = get_plan(current_plan, db=db)
                amount = plan_details.annual_price if cycle == BillingCycle.ANNUAL else plan_details.monthly_price

                sub_rec = db.query(SubscriptionModel).filter(
                    SubscriptionModel.tenant_id == tenant.tenant_id,
                    SubscriptionModel.status == "active"
                ).order_by(SubscriptionModel.end_date.desc()).first()
                sub_id = sub_rec.subscription_id if sub_rec else None

                _generate_invoice(
                    db=db,
                    tenant_id=tenant.tenant_id,
                    subscription_id=sub_id,
                    plan_name=current_plan.value,
                    amount=amount,
                    billing_cycle=cycle,
                    subscription_start=sub_end,
                    subscription_end=next_period_end
                )
                generated += 1
                logger.info("Generated automatic renewal invoice for tenant %s", tenant.tenant_id)
        db.commit()
    except Exception as e:
        logger.error("Failed running automatic renewal invoice generation: %s", e)
    finally:
        db.close()

    return generated


async def run_overdue_payment_reminders() -> int:
    """Send automated reminders for unpaid invoices exactly 7 days overdue (FR-80)."""
    from datetime import datetime, timezone, timedelta
    from app.db.master import get_master_db
    from app.models.saas import Invoice as InvoiceRecord
    from app.models.master import Tenant
    import aiosmtplib
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    sent_count = 0
    db = get_master_db()
    try:
        now = datetime.now(timezone.utc)
        target_due_date = (now - timedelta(days=7)).date()

        # Find unpaid invoices due exactly 7 days ago
        invoices = db.query(InvoiceRecord).filter(
            InvoiceRecord.status == "unpaid",
            InvoiceRecord.due_date == target_due_date
        ).all()

        for invoice in invoices:
            tenant = db.query(Tenant).filter(Tenant.tenant_id == invoice.tenant_id).first()
            if not tenant:
                continue

            email = tenant.billing_email if tenant.billing_email else tenant.primary_contact_email
            if not email:
                continue

            # Send reminder email
            subject = f"URGENT: Overdue Payment Notice - Invoice {invoice.invoice_number}"
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
              <h2>Overdue Payment Notification</h2>
              <p>Dear Administrator,</p>
              <p>This is an automated reminder that payment for invoice <strong>{invoice.invoice_number}</strong> of amount <strong>{invoice.amount} {invoice.currency}</strong> was due on <strong>{invoice.due_date}</strong> and remains unpaid.</p>
              <p>Please log in to your portal or contact support to settle the invoice to avoid service interruption.</p>
            </body>
            </html>
            """
            text_body = f"""Overdue Payment Notice
Dear Administrator,
Invoice {invoice.invoice_number} of amount {invoice.amount} {invoice.currency} was due on {invoice.due_date} and is now 7 days overdue.
Please settle this invoice immediately to avoid service interruption.
"""
            if not settings.smtp_user or not settings.smtp_password:
                logger.info(f"[MOCK OVERDUE EMAIL] To: {email} | Invoice: {invoice.invoice_number}")
                sent_count += 1
                continue

            try:
                msg = MIMEMultipart("alternative")
                msg["Subject"] = subject
                msg["From"] = settings.smtp_from
                msg["To"] = email

                part1 = MIMEText(text_body, "plain")
                part2 = MIMEText(html_body, "html")
                msg.attach(part1)
                msg.attach(part2)

                await aiosmtplib.send(
                    msg,
                    hostname=settings.smtp_host,
                    port=settings.smtp_port,
                    username=settings.smtp_user,
                    password=settings.smtp_password,
                    start_tls=True if settings.smtp_port == 587 else False,
                    use_tls=True if settings.smtp_port == 465 else False,
                )
                logger.info("Sent overdue invoice reminder email to %s for invoice %s", email, invoice.invoice_number)
                sent_count += 1
            except Exception as mail_err:
                logger.error("Failed sending overdue reminder email to %s: %s", email, mail_err)
    except Exception as e:
        logger.error("Failed running overdue payment reminders loop: %s", e)
    finally:
        db.close()

    return sent_count


async def suspension_loop() -> None:
    while True:
        try:
            await run_suspension_check()
            await run_trial_expiry_check()
            await run_renewal_invoices_generation()
            await run_overdue_payment_reminders()
        except Exception as e:
            logger.error("Suspension check failed: %s", e)
        await asyncio.sleep(settings.suspension_check_interval)
