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
)
from app.api.v1.superadmin.schemas import (
    SubscriptionStateOut,
    SubscriptionActionOut,
    AnnouncementOut,
    SubscriptionPlanChangeRequest,
)

router = APIRouter()


@router.get("/subscription", response_model=SubscriptionStateOut)
@limiter.limit("30/minute")
async def get_my_subscription(
    request: Request,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Return the current user's tenant subscription state.

    Any authenticated user associated with a hospital can call this endpoint.
    """
    tenant_id = user.raw.get("tenant_id") if user.raw else None
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a hospital tenant",
        )
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tenant not found",
        )
    return SubscriptionStateOut.model_validate(get_subscription_state(tenant))


@router.post("/subscription/upgrade", response_model=SubscriptionActionOut)
@limiter.limit("10/minute")
async def upgrade_my_subscription(
    request: Request,
    body: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Upgrade the current tenant's subscription plan."""
    tenant_id = user.raw.get("tenant_id") if user.raw else None
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a hospital tenant",
        )

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
    tenant_id = user.raw.get("tenant_id") if user.raw else None
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a hospital tenant",
        )

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
    tenant_id = user.raw.get("tenant_id") if user.raw else None
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with a hospital tenant",
        )

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
