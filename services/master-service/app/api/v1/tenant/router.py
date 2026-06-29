from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.limiter import limiter
from app.core.security import get_current_active_user, TokenPayload
from app.models.master import Tenant
from app.services.subscription_service import get_subscription_state
from app.services import subscription_service as subscription_service_module
from app.services.subscription_plans import (
    BillingCycle,
    SubscriptionPlan,
    plan_to_json,
)
from app.api.v1.superadmin.schemas import (
    InvoiceOut,
    PlanCatalogOut,
    SubscriptionStateOut,
    SubscriptionActionOut,
    AnnouncementOut,
    SubscriptionAuditLogOut,
    SubscriptionPlanChangeRequest,
    SubscriptionRenewRequest,
    SubscriptionSubscribeRequest,
)

router = APIRouter()


def _get_tenant_id(user: TokenPayload) -> str:
    tenant_id = user.raw.get("tenant_id") if user.raw else None
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a hospital tenant",
        )
    return tenant_id


@router.get("/subscription", response_model=SubscriptionStateOut)
@limiter.limit("30/minute")
async def get_my_subscription(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Return the current user's tenant subscription state."""
    tenant_id = _get_tenant_id(user)
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return SubscriptionStateOut.model_validate(get_subscription_state(tenant))


@router.post("/subscription/subscribe", response_model=SubscriptionActionOut)
@limiter.limit("10/minute")
async def subscribe_my_subscription(
    request: Request,
    body: SubscriptionSubscribeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Subscribe the current tenant to a plan (from trial/cancelled to paid)."""
    tenant_id = _get_tenant_id(user)
    try:
        plan = SubscriptionPlan(body.plan)
        billing_cycle = BillingCycle(body.billing_cycle)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    result = subscription_service_module.subscribe_tenant(
        db=db,
        tenant_id=tenant_id,
        plan=plan,
        billing_cycle=billing_cycle,
        start_trial=body.start_trial,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
        payment_provider_id=body.payment_provider_id,
    )
    db.commit()

    from app.services.tenant_service import remove_tenant_suspension_cache
    await remove_tenant_suspension_cache(tenant_id)

    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **get_subscription_state(result.tenant),
        }
    )


@router.post("/subscription/upgrade", response_model=SubscriptionActionOut)
@limiter.limit("10/minute")
async def upgrade_my_subscription(
    request: Request,
    body: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Upgrade the current tenant's subscription plan."""
    tenant_id = _get_tenant_id(user)

    try:
        new_plan = SubscriptionPlan(body.plan)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service_module.upgrade_subscription(
        db=db,
        tenant_id=tenant_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    from app.services.tenant_service import remove_tenant_suspension_cache
    await remove_tenant_suspension_cache(tenant_id)

    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **get_subscription_state(result.tenant),
        }
    )


@router.post("/subscription/downgrade", response_model=SubscriptionActionOut)
@limiter.limit("10/minute")
async def downgrade_my_subscription(
    request: Request,
    body: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Downgrade the current tenant's subscription plan."""
    tenant_id = _get_tenant_id(user)

    try:
        new_plan = SubscriptionPlan(body.plan)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service_module.downgrade_subscription(
        db=db,
        tenant_id=tenant_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        effective_at_end=body.effective_at_end,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **get_subscription_state(result.tenant),
        }
    )


@router.post("/subscription/renew", response_model=SubscriptionActionOut)
@limiter.limit("10/minute")
async def renew_my_subscription(
    request: Request,
    body: SubscriptionRenewRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Renew the current tenant's subscription."""
    tenant_id = _get_tenant_id(user)
    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service_module.renew_subscription(
        db=db,
        tenant_id=tenant_id,
        billing_cycle=billing_cycle,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

    from app.services.tenant_service import remove_tenant_suspension_cache
    await remove_tenant_suspension_cache(tenant_id)

    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **get_subscription_state(result.tenant),
        }
    )


@router.get("/plans", response_model=list[PlanCatalogOut])
@limiter.limit("60/minute")
async def list_plans(
    request: Request,
    _: TokenPayload = Depends(get_current_active_user),
):
    """List available subscription plans."""
    return [PlanCatalogOut.model_validate(plan_to_json(p)) for p in SubscriptionPlan]


@router.get("/invoices", response_model=list[InvoiceOut])
@limiter.limit("30/minute")
async def list_my_invoices(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """List invoices for the current tenant."""
    tenant_id = _get_tenant_id(user)
    from app.models.saas import Invoice as InvoiceModel
    invoices = (
        db.query(InvoiceModel)
        .filter(InvoiceModel.tenant_id == tenant_id)
        .order_by(InvoiceModel.issued_at.desc())
        .all()
    )
    return [InvoiceOut.model_validate(inv) for inv in invoices]


@router.get("/audit-log", response_model=list[SubscriptionAuditLogOut])
@limiter.limit("30/minute")
async def list_my_audit_log(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """List subscription audit log for the current tenant."""
    tenant_id = _get_tenant_id(user)
    from app.models.saas import SubscriptionAuditLog as SubscriptionAuditLogModel
    logs = (
        db.query(SubscriptionAuditLogModel)
        .filter(SubscriptionAuditLogModel.tenant_id == tenant_id)
        .order_by(SubscriptionAuditLogModel.created_at.desc())
        .all()
    )
    return [SubscriptionAuditLogOut.model_validate(l) for l in logs]


@router.get("/stats", response_model=dict)
@limiter.limit("30/minute")
async def get_my_tenant_stats(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Return usage statistics for the current tenant.

    Includes: active user count, subscription details, plan limits.
    """
    tenant_id = _get_tenant_id(user)
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    from app.models.saas import Invoice as InvoiceModel, SubscriptionAuditLog as SubscriptionAuditLogModel
    from app.services.subscription_plans import get_plan

    plan_details = get_plan(SubscriptionPlan(tenant.subscription_plan))
    now = datetime.now(timezone.utc)
    invoice_count = (
        db.query(InvoiceModel)
        .filter(InvoiceModel.tenant_id == tenant_id)
        .count()
    )
    audit_count = (
        db.query(SubscriptionAuditLogModel)
        .filter(SubscriptionAuditLogModel.tenant_id == tenant_id)
        .count()
    )

    return {
        "tenant_id": tenant_id,
        "name": tenant.hospital_name,
        "plan": tenant.subscription_plan,
        "plan_display_name": plan_details.display_name,
        "plan_max_users": plan_details.max_users,
        "status": tenant.status,
        "subscription_status": tenant.subscription_status,
        "subscription_end": tenant.subscription_end.isoformat() if tenant.subscription_end else None,
        "is_expired": bool(tenant.subscription_end and tenant.subscription_end < now),
        "invoice_count": invoice_count,
        "audit_event_count": audit_count,
        "pending_plan": tenant.pending_plan,
    }


@router.get("/announcements", response_model=list[AnnouncementOut])
@limiter.limit("30/minute")
async def get_my_announcements(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Return announcements visible to the current user's tenant.

    Shows announcements where audience is 'all' or where the tenant is in the
    target_tenant_ids list.
    """
    tenant_id = _get_tenant_id(user)

    from app.models.saas import Announcement as AnnouncementModel
    now = datetime.now(timezone.utc)

    announcements = (
        db.query(AnnouncementModel)
        .filter(
            (AnnouncementModel.audience == "all")
            | (
                (AnnouncementModel.audience == "selected")
                & (AnnouncementModel.target_tenant_ids.contains([tenant_id]))
            )
        )
        .filter(AnnouncementModel.publish_at <= now)
        .filter(
            (AnnouncementModel.expires_at == None)
            | (AnnouncementModel.expires_at > now)
        )
        .order_by(AnnouncementModel.publish_at.desc())
        .all()
    )
    return [AnnouncementOut.model_validate(a) for a in announcements]
