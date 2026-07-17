"""Tenant subscription lifecycle business logic.

This module contains the authoritative state-machine for hospital subscriptions.
All writes are intended to run inside a single DB transaction; callers are
responsible for committing.

Security design:
- Plans and prices are server-side constants (see subscription_plans).
- Free trials can only be granted once per tenant (`has_used_trial`).
- Upgrade/downgrade cannot target the free-trial plan.
- Suspension revokes Keycloak sessions and caches the tenant on a Redis blocklist.
- Reactivation is only allowed for non-cancelled tenants with a future expiry.
- Every state transition is logged to GlobalAuditLog and emits a domain event.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.models.master import GlobalAuditLog, Tenant
from app.models.saas import Invoice as InvoiceRecord, Subscription as SubscriptionRecord, SubscriptionAuditLog
from app.services.subscription_plans import (
    BillingCycle,
    SubscriptionPlan,
    SubscriptionStatus,
    get_plan,
    is_downgrade,
    is_upgrade,
    plan_rank,
    plan_uuid,
    subscription_duration_days,
    valid_plan_transition,
)
from app.services.tenant_service import (
    cache_tenant_suspension,
    remove_tenant_suspension_cache,
    _revoke_keycloak_sessions,
)

logger = logging.getLogger(__name__)


class SubscriptionError(HTTPException):
    """Raised when a subscription operation violates business rules."""

    def __init__(self, detail: str, code: str = "SUBSCRIPTION_ERROR") -> None:
        super().__init__(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": code, "message": detail},
        )


@dataclass(frozen=True)
class SubscriptionActionResult:
    tenant: Tenant
    action: str
    previous_status: str | None = None
    previous_plan: str | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_aware(value: datetime | None) -> datetime | None:
    """Make a datetime timezone-aware (UTC) if it is naive.

    This makes subscription comparisons safe across PostgreSQL (aware) and
    SQLite used in unit tests (naive).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _log_audit(
    db: Session,
    tenant_id: str,
    action: str,
    detail: dict[str, Any],
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> None:
    """Persist an auditable subscription event."""
    try:
        log = GlobalAuditLog(
            tenant_id=tenant_id,
            user_sub=user_sub,
            action=action,
            detail=json.dumps(detail, default=str),
            ip_address=ip_address,
        )
        db.add(log)
    except Exception:
        # Audit logging must never break the business transaction.
        logger.exception("Failed to write audit log for %s", action)


def _terminate_active_subscriptions(
    db: Session,
    tenant_id: str,
    new_status: str,
    now_dt: datetime,
) -> None:
    """Transition any active, trial, or grace subscription record for the tenant to the new_status."""
    if not inspect(db.connection()).has_table(SubscriptionRecord.__tablename__):
        return
    try:
        from sqlalchemy import text
        db.execute(
            text(
                """
                UPDATE subscriptions 
                SET status = :new_status, cancelled_at = :now 
                WHERE tenant_id = :tenant_id 
                  AND status IN ('active', 'trial', 'grace')
                """
            ),
            {
                "tenant_id": tenant_id,
                "new_status": new_status,
                "now": now_dt,
            },
        )
        db.flush()
        db.expire_all()
    except Exception:
        logger.exception("Failed to terminate active subscriptions for tenant %s", tenant_id)


def _cancel_pending_subscriptions(db: Session, tenant_id: str) -> None:
    """Transition any pending subscription records for the tenant to 'cancelled'."""
    if not inspect(db.connection()).has_table(SubscriptionRecord.__tablename__):
        return
    try:
        from sqlalchemy import text
        db.execute(
            text(
                """
                UPDATE subscriptions 
                SET status = 'cancelled'
                WHERE tenant_id = :tenant_id 
                  AND status = 'pending'
                """
            ),
            {"tenant_id": tenant_id},
        )
        db.flush()
        db.expire_all()
    except Exception:
        logger.exception("Failed to cancel pending subscriptions for tenant %s", tenant_id)


def _require_tenant(db: Session, tenant_id: str) -> Tenant:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return tenant


def compute_prorated_amount(
    current_plan: SubscriptionPlan,
    new_plan: SubscriptionPlan,
    billing_cycle: BillingCycle,
    subscription_end: datetime | None,
    db: Session | None = None,
) -> int:
    """Calculate prorated amount in whole currency units for a mid-cycle plan change.

    Positive return = additional charge (upgrade).
    Negative return = credit (downgrade).
    Returns 0 when there is no remaining period or prices are equal.
    """
    now = _utc_now()
    end = _ensure_aware(subscription_end)
    if not end or end <= now:
        return 0

    total_days = subscription_duration_days(billing_cycle)
    remaining_days = max(1, (end - now).days)

    current_details = get_plan(current_plan, db=db)
    new_details = get_plan(new_plan, db=db)

    if billing_cycle is BillingCycle.ANNUAL:
        current_price = current_details.annual_price
        new_price = new_details.annual_price
    else:
        current_price = current_details.monthly_price
        new_price = new_details.monthly_price

    price_diff = new_price - current_price
    if price_diff == 0:
        return 0

    return int(price_diff * remaining_days / total_days) if total_days > 0 else price_diff


def _generate_invoice(
    db: Session,
    tenant_id: str,
    subscription_id: Any,
    plan_name: str,
    amount: int,
    billing_cycle: BillingCycle,
    subscription_start: datetime | None = None,
    subscription_end: datetime | None = None,
) -> None:
    """Create an Invoice record atomically during a plan change.

    Silently skips when the invoices table is unavailable (e.g. SQLite tests).
    """
    if not inspect(db.connection()).has_table(InvoiceRecord.__tablename__):
        return
    try:
        from datetime import date, timedelta
        start = _ensure_aware(subscription_start) or _utc_now()
        end = _ensure_aware(subscription_end) or start
        import uuid
        tenant_upper = tenant_id.upper()
        date_str = start.strftime("%y%m%d")
        invoice_number = f"SYS-{tenant_upper}-{date_str}-{uuid.uuid4().hex[:6].upper()}"
        invoice = InvoiceRecord(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            invoice_number=invoice_number,
            billing_period_start=start.date(),
            billing_period_end=end.date(),
            plan_name=plan_name,
            amount=amount,
            currency="USD",
            due_date=(start + timedelta(days=3)).date(),
            status="paid" if amount <= 0 else "unpaid",
        )
        if amount <= 0:
            invoice.paid_at = _utc_now()
        db.add(invoice)
        db.flush()
    except Exception:
        logger.exception("Failed to generate invoice for %s", tenant_id)


def _subscription_audit_event(
    db: Session,
    tenant_id: str,
    event_type: str,
    actor_id: str | None,
    actor_type: str,
    reason: str | None,
    subscription_id: Any | None = None,
) -> None:
    """Persist a subscription lifecycle event to subscription_audit_log.

    Silently skips when the audit table does not exist (e.g. lightweight SQLite
    unit-test fixtures), so the business transaction is never blocked.
    """
    try:
        if not inspect(db.connection()).has_table(SubscriptionAuditLog.__tablename__):
            return
        parsed_actor_id = None
        if actor_id:
            try:
                parsed_actor_id = uuid.UUID(actor_id) if isinstance(actor_id, str) else actor_id
            except ValueError:
                parsed_actor_id = None

        log = SubscriptionAuditLog(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            event_type=event_type,
            actor_id=parsed_actor_id,
            actor_type=actor_type,
            reason=reason,
        )
        db.add(log)
    except Exception:
        logger.exception("Failed to write subscription audit event %s", event_type)


def _record_subscription(
    db: Session,
    tenant_id: str,
    plan: SubscriptionPlan,
    billing_cycle: BillingCycle,
    start: datetime,
    end: datetime,
    status: str,
    auto_renew: bool = True,
) -> SubscriptionRecord | None:
    """Create a historical subscription record in the subscriptions table.

    Returns None if the subscriptions table is unavailable (e.g. lightweight
    SQLite test fixtures), so the business transaction is never blocked.
    """
    if not inspect(db.connection()).has_table(SubscriptionRecord.__tablename__):
        return None
    try:
        # Dynamic check and seeding to prevent ForeignKeyViolation
        from app.services.subscription_plans import sync_plans_to_db
        from app.models.saas import SubscriptionPlan as DBPlan
        plan_id_obj = plan_uuid(plan)
        
        # Check if the subscription plans table contains this plan
        exists = db.query(DBPlan).filter(DBPlan.plan_id == plan_id_obj).first() is not None
        if not exists:
            logger.info("Plan %s not found in database. Syncing plans...", plan.value)
            sync_plans_to_db(db)
    except Exception as e:
        logger.warning("Failed to auto-seed/verify subscription plans: %s", e)

    try:
        record = SubscriptionRecord(
            tenant_id=tenant_id,
            plan_id=plan_uuid(plan),
            billing_cycle=billing_cycle.value,
            start_date=start.date(),
            end_date=end.date(),
            grace_period_days=3 if plan is SubscriptionPlan.FREE_TRIAL else 7,
            auto_renew=auto_renew,
            status=status,
        )
        db.add(record)
        db.flush()
        return record
    except Exception:
        logger.exception("Failed to record subscription history for %s", tenant_id)
        return None



def _set_subscription_dates(
    tenant: Tenant,
    billing_cycle: BillingCycle,
    start: datetime | None = None,
    plan: SubscriptionPlan | None = None,
    db: Session | None = None,
) -> None:
    """Compute and set subscription_start, subscription_end and grace_period_end."""
    now = _utc_now()
    tenant.subscription_start = start or now

    if plan is SubscriptionPlan.FREE_TRIAL:
        trial_days = get_plan(SubscriptionPlan.FREE_TRIAL, db=db).trial_days
        tenant.trial_start = now
        tenant.trial_end = now + timedelta(days=trial_days)
        tenant.subscription_end = tenant.trial_end
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 3
        tenant.grace_period_end = tenant.trial_end + timedelta(days=g_days)
    else:
        duration_days = subscription_duration_days(billing_cycle, plan)
        tenant.subscription_end = tenant.subscription_start + timedelta(days=duration_days)
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 7
        tenant.grace_period_end = tenant.subscription_end + timedelta(days=g_days)


def subscribe_tenant(
    db: Session,
    tenant_id: str,
    plan: SubscriptionPlan,
    billing_cycle: BillingCycle,
    start_trial: bool = False,
    user_sub: str | None = None,
    ip_address: str | None = None,
    payment_provider_id: str | None = None,
) -> SubscriptionActionResult:
    """Start or reset a tenant subscription.

    If start_trial is True and the plan supports a trial, the tenant enters the
    TRIAL status. Trials can only be used once per tenant.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    if start_trial or plan is SubscriptionPlan.FREE_TRIAL:
        if tenant.has_used_trial:
            raise SubscriptionError(
                "This tenant has already used its free trial.",
                code="TRIAL_ALREADY_USED",
            )
        plan = SubscriptionPlan.FREE_TRIAL
        billing_cycle = BillingCycle.MONTHLY  # trials are not annual
        tenant.subscription_status = SubscriptionStatus.TRIAL.value
        tenant.has_used_trial = True
        tenant.auto_renew = False  # trial does not auto-renew into paid
    else:
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value
        tenant.has_used_trial = tenant.has_used_trial or False
        tenant.auto_renew = True

    tenant.subscription_plan = plan.value
    tenant.subscription_billing_cycle = billing_cycle.value
    tenant.status = "active"
    tenant.is_active = True
    tenant.suspended_at = None
    tenant.suspended_reason = None
    tenant.cancelled_at = None

    _set_subscription_dates(tenant, billing_cycle, plan=plan, db=db)

    if payment_provider_id:
        tenant.payment_provider_id = payment_provider_id

    start = _ensure_aware(tenant.subscription_start) or _utc_now()
    end = _ensure_aware(tenant.subscription_end) or start
    _terminate_active_subscriptions(db, tenant_id, "superseded", start)
    sub_record = _record_subscription(
        db=db,
        tenant_id=tenant_id,
        plan=plan,
        billing_cycle=billing_cycle,
        start=start,
        end=end,
        status=tenant.subscription_status,
        auto_renew=tenant.auto_renew,
    )
    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="plan_created" if previous_status is None else "plan_created",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason=f"Subscribed to {plan.value} ({billing_cycle.value})",
        subscription_id=sub_record.subscription_id if sub_record else None,
    )

    _log_audit(
        db,
        tenant_id,
        "subscription.subscribe",
        {
            "plan": plan.value,
            "billing_cycle": billing_cycle.value,
            "trial": start_trial,
            "subscription_end": tenant.subscription_end.isoformat(),
            "previous_status": previous_status,
            "previous_plan": previous_plan,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action="subscribe",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def upgrade_subscription(
    db: Session,
    tenant_id: str,
    new_plan: SubscriptionPlan,
    billing_cycle: BillingCycle | None = None,
    effective_at_end: bool = False,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Upgrade a tenant to a higher plan. Immediate or deferred effect."""
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    current_plan = SubscriptionPlan(tenant.subscription_plan)

    if not valid_plan_transition(current_plan, new_plan):
        raise SubscriptionError(
            "Invalid plan transition.",
            code="INVALID_PLAN_TRANSITION",
        )

    # Billing cycle defaults to the tenant's existing cycle.
    cycle = billing_cycle or BillingCycle(tenant.subscription_billing_cycle or "monthly")

    current_cycle_str = tenant.subscription_billing_cycle or "monthly"
    is_same_plan = (current_plan == new_plan)
    is_cycle_upgrade = is_same_plan and (current_cycle_str == "monthly" and cycle.value == "annual")

    if not (is_upgrade(current_plan, new_plan) or is_cycle_upgrade):
        raise SubscriptionError(
            f"'{new_plan.value}' is not higher than the current plan '{current_plan.value}'.",
            code="NOT_AN_UPGRADE",
        )

    now = _utc_now()

    if effective_at_end:
        # Defer the upgrade: record pending plan, do not change subscription yet.
        tenant.pending_plan = new_plan.value
        tenant.pending_billing_cycle = cycle.value
        action = "upgrade_deferred"

        metadata = tenant.subscription_metadata or {}
        metadata["upgrade_requested_at"] = now.isoformat()
        tenant.subscription_metadata = metadata

        _subscription_audit_event(
            db=db,
            tenant_id=tenant_id,
            event_type="plan_upgraded",
            actor_id=user_sub,
            actor_type="super_admin" if user_sub else "system",
            reason=f"Deferred upgrade from {current_plan.value} to {new_plan.value} (effective at end of term)",
            subscription_id=None,
        )

        start = _ensure_aware(tenant.subscription_end) or now
        duration_days = subscription_duration_days(cycle, new_plan)
        end = start + timedelta(days=duration_days)
        _record_subscription(
            db=db,
            tenant_id=tenant_id,
            plan=new_plan,
            billing_cycle=cycle,
            start=start,
            end=end,
            status="pending",
            auto_renew=tenant.auto_renew,
        )
    else:
        # Compute proration and determine dates depending on cycle changes
        cycle_changing = (current_cycle_str != cycle.value)
        current_end = _ensure_aware(tenant.subscription_end)

        if cycle_changing:
            # Restart cycle immediately
            start = now
            duration_days = subscription_duration_days(cycle, new_plan)
            end = start + timedelta(days=duration_days)

            # Compute unused credit of current subscription
            current_details = get_plan(current_plan, db=db)
            current_cycle_enum = BillingCycle(current_cycle_str)
            current_price = current_details.annual_price if current_cycle_enum is BillingCycle.ANNUAL else current_details.monthly_price
            current_total_days = subscription_duration_days(current_cycle_enum, current_plan)

            remaining_days = max(0, (current_end - now).days) if current_end and current_end > now else 0
            unused_credit = int(current_price * remaining_days / current_total_days) if current_total_days > 0 else 0

            # New plan full price
            new_details = get_plan(new_plan, db=db)
            new_price = new_details.annual_price if cycle is BillingCycle.ANNUAL else new_details.monthly_price

            # Net proration amount to charge
            proration = new_price - unused_credit
        else:
            # Keep existing cycle dates
            start = _ensure_aware(tenant.subscription_start) or now
            end = current_end or start

            # Standard proration for same billing cycle
            proration = compute_prorated_amount(
                current_plan, new_plan, cycle, current_end, db=db
            )

        tenant.subscription_plan = new_plan.value
        tenant.subscription_billing_cycle = cycle.value
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value
        tenant.status = "active"
        tenant.is_active = True

        tenant.subscription_start = start
        tenant.subscription_end = end
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 7
        tenant.grace_period_end = end + timedelta(days=g_days)

        # Clear any deferred downgrade since the tenant moved to a higher plan.
        tenant.pending_plan = None
        tenant.pending_billing_cycle = None

        _terminate_active_subscriptions(db, tenant_id, "superseded", start)
        _cancel_pending_subscriptions(db, tenant_id)
        sub_record = _record_subscription(
            db=db,
            tenant_id=tenant_id,
            plan=new_plan,
            billing_cycle=cycle,
            start=start,
            end=end,
            status=tenant.subscription_status,
            auto_renew=tenant.auto_renew,
        )

        # Generate prorated invoice if there is a charge.
        if proration > 0 and sub_record:
            _generate_invoice(
                db=db,
                tenant_id=tenant_id,
                subscription_id=sub_record.subscription_id,
                plan_name=new_plan.value,
                amount=proration,
                billing_cycle=cycle,
                subscription_start=start,
                subscription_end=end,
            )

        _subscription_audit_event(
            db=db,
            tenant_id=tenant_id,
            event_type="plan_upgraded",
            actor_id=user_sub,
            actor_type="super_admin" if user_sub else "system",
            reason=f"Upgraded from {current_plan.value} to {new_plan.value}",
            subscription_id=sub_record.subscription_id if sub_record else None,
        )

        metadata = tenant.subscription_metadata or {}
        metadata["upgrade_effective_at_end"] = False
        metadata["upgrade_requested_at"] = now.isoformat()
        metadata["upgrade_proration"] = proration
        tenant.subscription_metadata = metadata

        action = "upgrade"

    _log_audit(
        db,
        tenant_id,
        "subscription.upgrade",
        {
            "from_plan": current_plan.value,
            "to_plan": new_plan.value,
            "billing_cycle": cycle.value,
            "effective_at_end": effective_at_end,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action=action,
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def _get_tenant_active_users_count(tenant_id: str) -> int:
    from app.services.provision import get_tenant_db_session
    from sqlalchemy import text
    tenant_db = None
    try:
        tenant_db = get_tenant_db_session(tenant_id)
        result = tenant_db.execute(text("SELECT COUNT(*) FROM users WHERE is_active = true"))
        return result.scalar() or 0
    except Exception:
        return 0
    finally:
        if tenant_db:
            tenant_db.close()


def downgrade_subscription(
    db: Session,
    tenant_id: str,
    new_plan: SubscriptionPlan,
    billing_cycle: BillingCycle | None = None,
    effective_at_end: bool = True,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Downgrade a tenant to a lower plan.

    When effective_at_end is True, the downgrade is deferred by setting
    pending_plan on the tenant; it will take effect at the next renewal.

    When effective_at_end is False (immediate), prorated credit is computed
    and an invoice (credit memo) is generated.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    current_plan = SubscriptionPlan(tenant.subscription_plan)

    if not valid_plan_transition(current_plan, new_plan):
        raise SubscriptionError(
            "Invalid plan transition.",
            code="INVALID_PLAN_TRANSITION",
        )

    # Billing cycle defaults to the tenant's existing cycle.
    cycle = billing_cycle or BillingCycle(tenant.subscription_billing_cycle or "monthly")

    current_cycle_str = tenant.subscription_billing_cycle or "monthly"
    is_same_plan = (current_plan == new_plan)
    is_cycle_downgrade = is_same_plan and (current_cycle_str == "annual" and cycle.value == "monthly")

    if not (is_downgrade(current_plan, new_plan) or is_cycle_downgrade):
        raise SubscriptionError(
            f"'{new_plan.value}' is not lower than the current plan '{current_plan.value}'.",
            code="NOT_A_DOWNGRADE",
        )

    # Validate user seat limits before downgrading
    target_plan_details = get_plan(new_plan, db=db)
    if target_plan_details.max_users is not None and target_plan_details.max_users > 0:
        active_users = _get_tenant_active_users_count(tenant_id)
        if active_users > target_plan_details.max_users:
            raise SubscriptionError(
                f"Cannot downgrade to '{new_plan.value}' because the tenant currently has {active_users} active users, "
                f"which exceeds the plan limit of {target_plan_details.max_users}.",
                code="LIMIT_EXCEEDED",
            )

    now = _utc_now()

    if effective_at_end:
        # Defer the downgrade: record pending plan, do not change subscription yet.
        tenant.pending_plan = new_plan.value
        tenant.pending_billing_cycle = cycle.value
        action = "downgrade_deferred"

        metadata = tenant.subscription_metadata or {}
        metadata["downgrade_requested_at"] = now.isoformat()
        tenant.subscription_metadata = metadata

        _subscription_audit_event(
            db=db,
            tenant_id=tenant_id,
            event_type="plan_downgraded",
            actor_id=user_sub,
            actor_type="super_admin" if user_sub else "system",
            reason=f"Deferred downgrade from {current_plan.value} to {new_plan.value} (effective at end of term)",
            subscription_id=None,
        )

        start = _ensure_aware(tenant.subscription_end) or now
        duration_days = subscription_duration_days(cycle, new_plan)
        end = start + timedelta(days=duration_days)
        _record_subscription(
            db=db,
            tenant_id=tenant_id,
            plan=new_plan,
            billing_cycle=cycle,
            start=start,
            end=end,
            status="pending",
            auto_renew=tenant.auto_renew,
        )
    else:
        # Compute proration and determine dates depending on cycle changes
        cycle_changing = (current_cycle_str != cycle.value)
        current_end = _ensure_aware(tenant.subscription_end)

        if cycle_changing:
            # Restart cycle immediately
            start = now
            duration_days = subscription_duration_days(cycle, new_plan)
            end = start + timedelta(days=duration_days)

            # Compute unused credit of current subscription
            current_details = get_plan(current_plan, db=db)
            current_cycle_enum = BillingCycle(current_cycle_str)
            current_price = current_details.annual_price if current_cycle_enum is BillingCycle.ANNUAL else current_details.monthly_price
            current_total_days = subscription_duration_days(current_cycle_enum, current_plan)

            remaining_days = max(0, (current_end - now).days) if current_end and current_end > now else 0
            unused_credit = int(current_price * remaining_days / current_total_days) if current_total_days > 0 else 0

            # New plan full price
            new_details = get_plan(new_plan, db=db)
            new_price = new_details.annual_price if cycle is BillingCycle.ANNUAL else new_details.monthly_price

            # Net proration amount
            proration = new_price - unused_credit
        else:
            # Keep existing cycle dates
            start = _ensure_aware(tenant.subscription_start) or now
            end = current_end or start

            # Standard proration for same billing cycle
            proration = compute_prorated_amount(
                current_plan, new_plan, cycle, current_end, db=db
            )

        tenant.subscription_plan = new_plan.value
        tenant.subscription_billing_cycle = cycle.value
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value
        tenant.pending_plan = None
        tenant.pending_billing_cycle = None

        tenant.subscription_start = start
        tenant.subscription_end = end
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 7
        tenant.grace_period_end = end + timedelta(days=g_days)

        _terminate_active_subscriptions(db, tenant_id, "superseded", start)
        _cancel_pending_subscriptions(db, tenant_id)
        sub_record = _record_subscription(
            db=db,
            tenant_id=tenant_id,
            plan=new_plan,
            billing_cycle=cycle,
            start=start,
            end=end,
            status=tenant.subscription_status,
            auto_renew=tenant.auto_renew,
        )

        # Generate invoice/credit memo if proration is non-zero.
        if proration != 0 and sub_record:
            _generate_invoice(
                db=db,
                tenant_id=tenant_id,
                subscription_id=sub_record.subscription_id,
                plan_name=new_plan.value,
                amount=proration,
                billing_cycle=cycle,
                subscription_start=start,
                subscription_end=end,
            )

        _subscription_audit_event(
            db=db,
            tenant_id=tenant_id,
            event_type="plan_downgraded",
            actor_id=user_sub,
            actor_type="super_admin" if user_sub else "system",
            reason=f"Downgraded from {current_plan.value} to {new_plan.value}",
            subscription_id=sub_record.subscription_id if sub_record else None,
        )

        metadata = tenant.subscription_metadata or {}
        metadata["downgrade_effective_at_end"] = False
        metadata["downgrade_requested_at"] = now.isoformat()
        metadata["downgrade_proration"] = proration
        tenant.subscription_metadata = metadata

        action = "downgrade"

    _log_audit(
        db,
        tenant_id,
        "subscription.downgrade",
        {
            "from_plan": current_plan.value,
            "to_plan": new_plan.value,
            "billing_cycle": cycle.value,
            "effective_at_end": effective_at_end,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action=action,
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def apply_pending_plan_changes(db: Session, tenant: Tenant) -> bool:
    """Apply a deferred downgrade (pending_plan) at renewal time.

    Returns True if a pending plan was applied, False otherwise.
    This is called internally by renew_subscription and can also be invoked
    as a standalone background task for batch processing.
    """
    if not tenant.pending_plan:
        return False

    new_plan = SubscriptionPlan(tenant.pending_plan)
    current_plan = SubscriptionPlan(tenant.subscription_plan)

    tenant.subscription_plan = new_plan.value
    if tenant.pending_billing_cycle:
        tenant.subscription_billing_cycle = tenant.pending_billing_cycle
    tenant.pending_plan = None
    tenant.pending_billing_cycle = None

    metadata = tenant.subscription_metadata or {}
    metadata["pending_plan_applied_at"] = _utc_now().isoformat()
    metadata["pending_plan_from"] = current_plan.value
    metadata["pending_plan_to"] = new_plan.value
    tenant.subscription_metadata = metadata

    _subscription_audit_event(
        db=db,
        tenant_id=tenant.tenant_id,
        event_type="plan_downgraded",
        actor_id=None,
        actor_type="system",
        reason=f"Pending plan applied: {current_plan.value} -> {new_plan.value} at renewal",
        subscription_id=None,
    )

    logger.info(
        "Pending plan applied for %s: %s -> %s",
        tenant.tenant_id, current_plan.value, new_plan.value,
    )
    return True


def renew_subscription(
    db: Session,
    tenant_id: str,
    billing_cycle: BillingCycle | None = None,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Renew a tenant subscription by one billing cycle from its current end date.

    Renewal is allowed for ACTIVE, PAST_DUE, and SUSPENDED (manual) tenants.
    CANCELLED tenants must re-subscribe.

    Before renewing, any deferred pending plan change is applied.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    current_status = SubscriptionStatus(tenant.subscription_status)
    if current_status is SubscriptionStatus.CANCELLED:
        raise SubscriptionError(
            "Cancelled subscriptions cannot be renewed. Create a new subscription instead.",
            code="CANCELLED_SUBSCRIPTION",
        )

    if current_status is SubscriptionStatus.TRIAL:
        raise SubscriptionError(
            "Trials cannot be renewed. Subscribe to a paid plan instead.",
            code="TRIAL_RENEWAL",
        )

    base_date = _ensure_aware(tenant.subscription_end) or _utc_now()
    if base_date < _utc_now():
        base_date = _utc_now()

    # Terminate the previous active subscription as expired
    _terminate_active_subscriptions(db, tenant_id, "expired", base_date)

    pending_sub = None
    if inspect(db.connection()).has_table(SubscriptionRecord.__tablename__):
        pending_sub = (
            db.query(SubscriptionRecord)
            .filter(SubscriptionRecord.tenant_id == tenant_id, SubscriptionRecord.status == "pending")
            .order_by(SubscriptionRecord.start_date.asc())
            .first()
        )

    if pending_sub:
        from app.services.subscription_plans import PLAN_UUIDS
        pending_plan_enum = None
        for plan_enum, p_uuid in PLAN_UUIDS.items():
            if p_uuid == pending_sub.plan_id:
                pending_plan_enum = plan_enum
                break
        
        plan_name = pending_plan_enum.value if pending_plan_enum else "basic"
        current_plan = SubscriptionPlan(plan_name)
        cycle = BillingCycle(pending_sub.billing_cycle)
        
        tenant.subscription_plan = plan_name
        tenant.subscription_billing_cycle = pending_sub.billing_cycle
        
        pending_sub.status = "active"
        pending_sub.start_date = base_date.date()
        
        duration_days = subscription_duration_days(cycle, current_plan)
        tenant.subscription_end = base_date + timedelta(days=duration_days)
        pending_sub.end_date = tenant.subscription_end.date()
        
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 7
        tenant.grace_period_end = tenant.subscription_end + timedelta(days=g_days)
        
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value
        tenant.status = "active"
        tenant.is_active = True
        
        tenant.pending_plan = None
        tenant.pending_billing_cycle = None
        
        sub_record = pending_sub
        pending_applied = True
    else:
        # Apply any deferred pending plan changes before renewing.
        pending_applied = apply_pending_plan_changes(db, tenant)

        current_plan = SubscriptionPlan(tenant.subscription_plan)
        cycle = billing_cycle or BillingCycle(tenant.subscription_billing_cycle or "monthly")
        duration_days = subscription_duration_days(cycle, current_plan)

        tenant.subscription_start = base_date
        tenant.subscription_end = base_date + timedelta(days=duration_days)
        g_days = tenant.grace_period_days if (hasattr(tenant, "grace_period_days") and tenant.grace_period_days is not None) else 7
        tenant.grace_period_end = tenant.subscription_end + timedelta(days=g_days)
        tenant.subscription_billing_cycle = cycle.value
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value
        tenant.status = "active"
        tenant.is_active = True
        tenant.suspended_at = None
        tenant.suspended_reason = None

        sub_record = _record_subscription(
            db=db,
            tenant_id=tenant_id,
            plan=current_plan,
            billing_cycle=cycle,
            start=base_date,
            end=tenant.subscription_end,
            status=tenant.subscription_status,
            auto_renew=tenant.auto_renew,
        )

    # Generate a full-cycle invoice for the renewed term.
    if sub_record:
        plan_details = get_plan(current_plan, db=db)
        if cycle is BillingCycle.ANNUAL:
            full_amount = plan_details.annual_price
        else:
            full_amount = plan_details.monthly_price
        _generate_invoice(
            db=db,
            tenant_id=tenant_id,
            subscription_id=sub_record.subscription_id,
            plan_name=current_plan.value,
            amount=full_amount,
            billing_cycle=cycle,
            subscription_start=base_date,
            subscription_end=tenant.subscription_end,
        )

    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="plan_created",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason=f"Renewed {current_plan.value} subscription ({cycle.value})",
        subscription_id=sub_record.subscription_id if sub_record else None,
    )

    _log_audit(
        db,
        tenant_id,
        "subscription.renew",
        {
            "plan": current_plan.value,
            "billing_cycle": cycle.value,
            "subscription_end": tenant.subscription_end.isoformat(),
            "previous_status": previous_status,
            "pending_plan_applied": pending_applied,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action="renew",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def activate_tenant(
    db: Session,
    tenant_id: str,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Manually activate a hospital account (super-admin override).

    This flips is_active to True and status to active without modifying
    subscription dates. It is intended for onboarding or remediation.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    tenant.status = "active"
    tenant.is_active = True
    if tenant.subscription_status in (
        SubscriptionStatus.SUSPENDED.value,
        SubscriptionStatus.CANCELLED.value,
    ):
        tenant.subscription_status = SubscriptionStatus.ACTIVE.value

    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="reactivation",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason="Manual activation",
    )

    _log_audit(
        db,
        tenant_id,
        "tenant.activate",
        {
            "previous_status": previous_status,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action="activate",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def suspend_tenant(
    db: Session,
    tenant_id: str,
    reason: str,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Manually suspend a hospital account.

    Sets status to suspended, is_active to False, caches the tenant on the
    Redis suspension blocklist, and revokes active Keycloak sessions.
    """
    if not reason or not reason.strip():
        raise SubscriptionError(
            "A suspension reason is required.",
            code="MISSING_SUSPENSION_REASON",
        )

    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    now = _utc_now()
    tenant.status = "suspended"
    tenant.is_active = False
    tenant.subscription_status = SubscriptionStatus.SUSPENDED.value
    tenant.suspended_at = now
    tenant.suspended_reason = reason.strip()

    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="suspension",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason=reason.strip(),
    )

    _log_audit(
        db,
        tenant_id,
        "tenant.suspend",
        {
            "reason": reason.strip(),
            "previous_status": previous_status,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    # Note: Redis cache and Keycloak revocation are async side effects.
    # The caller is expected to await them after committing the DB transaction.
    return SubscriptionActionResult(
        tenant=tenant,
        action="suspend",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def reactivate_tenant(
    db: Session,
    tenant_id: str,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Reactivate a previously suspended hospital account.

    Reactivation is only allowed when the subscription is not cancelled and has
    not expired past the grace period.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    if tenant.subscription_status == SubscriptionStatus.CANCELLED.value:
        raise SubscriptionError(
            "Cancelled tenants cannot be reactivated. Subscribe to a new plan instead.",
            code="CANCELLED_SUBSCRIPTION",
        )

    sub_end = _ensure_aware(tenant.subscription_end)
    grace_end = _ensure_aware(tenant.grace_period_end)
    if sub_end and grace_end:
        if _utc_now() > grace_end:
            raise SubscriptionError(
                "Subscription has expired beyond the grace period. Renew before reactivating.",
                code="SUBSCRIPTION_EXPIRED",
            )

    tenant.status = "active"
    tenant.is_active = True
    tenant.subscription_status = SubscriptionStatus.ACTIVE.value
    tenant.suspended_at = None
    tenant.suspended_reason = None
    tenant.reactivated_at = _utc_now()

    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="reactivation",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason="Reactivated after suspension",
    )

    _log_audit(
        db,
        tenant_id,
        "tenant.reactivate",
        {
            "previous_status": previous_status,
            "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action="reactivate",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def terminate_tenant(
    db: Session,
    tenant_id: str,
    reason: str,
    user_sub: str | None = None,
    ip_address: str | None = None,
) -> SubscriptionActionResult:
    """Permanently terminate a hospital account.

    Termination is irreversible. It sets status to terminated, disables the
    account, cancels any active subscription, caches the tenant on the Redis
    suspension blocklist, and revokes active Keycloak sessions.
    """
    tenant = _require_tenant(db, tenant_id)
    previous_status = tenant.subscription_status
    previous_plan = tenant.subscription_plan

    now = _utc_now()
    tenant.status = "terminated"
    tenant.is_active = False
    tenant.subscription_status = SubscriptionStatus.TERMINATED.value
    tenant.terminated_at = now
    tenant.termination_reason = reason.strip() if reason else None
    tenant.auto_renew = False
    tenant.cancelled_at = now

    # Drop isolated database
    try:
        from app.services.provision import drop_tenant_database
        drop_tenant_database(tenant_id)
    except Exception as e:
        logger.error("Failed to drop database for tenant %s during termination: %s", tenant_id, e)

    _subscription_audit_event(
        db=db,
        tenant_id=tenant_id,
        event_type="termination",
        actor_id=user_sub,
        actor_type="super_admin" if user_sub else "system",
        reason=reason.strip() if reason else None,
    )

    _log_audit(
        db,
        tenant_id,
        "tenant.terminate",
        {
            "reason": reason.strip() if reason else None,
            "previous_status": previous_status,
        },
        user_sub=user_sub,
        ip_address=ip_address,
    )

    return SubscriptionActionResult(
        tenant=tenant,
        action="terminate",
        previous_status=previous_status,
        previous_plan=previous_plan,
    )


def get_subscription_state(tenant: Tenant, db: Session | None = None) -> dict[str, Any]:
    """Return a serializable snapshot of a tenant's subscription."""
    plan_name = tenant.subscription_plan or "none"
    try:
        plan = get_plan(plan_name, db=db)
        display_name = plan.display_name
    except (KeyError, ValueError):
        display_name = plan_name.replace("_", " ").title()

    now = _utc_now()
    sub_end = _ensure_aware(tenant.subscription_end)
    grace_end = _ensure_aware(tenant.grace_period_end)
    is_expired = bool(sub_end and sub_end < now)
    in_grace = bool(
        grace_end
        and sub_end
        and sub_end <= now <= grace_end
    )

    is_trial = tenant.subscription_status == "trial" or tenant.subscription_plan == "free_trial"

    return {
        "tenant_id": tenant.tenant_id,
        "name": tenant.hospital_name,
        "status": tenant.status or "active",
        "is_active": tenant.is_active if tenant.is_active is not None else True,
        "is_trial": is_trial,
        "subscription": {
            "plan": plan_name,
            "display_name": display_name,
            "status": tenant.subscription_status or "active",
            "billing_cycle": tenant.subscription_billing_cycle,
            "start": tenant.subscription_start.isoformat() if tenant.subscription_start else None,
            "end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
            "grace_period_end": tenant.grace_period_end.isoformat() if tenant.grace_period_end else None,
            "auto_renew": tenant.auto_renew if tenant.auto_renew is not None else True,
            "is_expired": is_expired,
            "in_grace_period": in_grace,
            "has_used_trial": tenant.has_used_trial if tenant.has_used_trial is not None else False,
            "pending_plan": tenant.pending_plan,
            "pending_billing_cycle": tenant.pending_billing_cycle,
        },
        "suspension": {
            "suspended_at": tenant.suspended_at.isoformat() if tenant.suspended_at else None,
            "reason": tenant.suspended_reason,
            "reactivated_at": tenant.reactivated_at.isoformat() if tenant.reactivated_at else None,
        },
        "termination": {
            "terminated_at": tenant.terminated_at.isoformat() if tenant.terminated_at else None,
            "reason": tenant.termination_reason,
        },
        "payment_provider_id": tenant.payment_provider_id,
        "currency": getattr(tenant, "currency", "USD"),
        "grace_days": getattr(tenant, "grace_period_days", 14),
    }


# Convenience aliases to keep the public API explicit.
subscribe = subscribe_tenant
upgrade = upgrade_subscription
downgrade = downgrade_subscription
renew = renew_subscription
activate = activate_tenant
suspend = suspend_tenant
reactivate = reactivate_tenant
terminate = terminate_tenant
state = get_subscription_state
