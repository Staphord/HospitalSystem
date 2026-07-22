from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    PlanChangeRequestCreate,
    CancellationRequestCreate,
    SubscriptionRequestOut,
    ToggleAutoRenewRequest,
)
from app.services.subscription_request_service import (
    create_plan_change_request,
    create_cancellation_request,
    get_pending_request,
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


@router.patch("/subscription/auto-renew", response_model=SubscriptionStateOut)
@limiter.limit("10/minute")
async def toggle_my_auto_renew(
    request: Request,
    body: ToggleAutoRenewRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Toggle auto-renewal for the current tenant's subscription."""
    tenant_id = _get_tenant_id(user)
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    from app.models.saas import Subscription as SubscriptionModel
    sub = db.query(SubscriptionModel).filter(
        SubscriptionModel.tenant_id == tenant_id,
        SubscriptionModel.status != "superseded",
        SubscriptionModel.status != "expired",
    ).order_by(SubscriptionModel.created_at.desc()).first()

    if not sub:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Active subscription not found")

    tenant.auto_renew = body.auto_renew
    sub.auto_renew = body.auto_renew
    db.commit()

    from app.services.subscription_request_service import log_action
    log_action(
        db=db,
        tenant_id=tenant_id,
        action="subscription.auto_renew_toggled",
        detail={"auto_renew": body.auto_renew},
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()

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
        effective_at_end=body.effective_at_end if body.effective_at_end is not None else False,
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
        effective_at_end=body.effective_at_end if body.effective_at_end is not None else True,
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


@router.post("/subscription/request-plan-change", response_model=SubscriptionRequestOut)
@limiter.limit("5/minute")
async def request_plan_change(
    request: Request,
    body: PlanChangeRequestCreate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Submit a plan upgrade or downgrade request for super admin approval."""
    tenant_id = _get_tenant_id(user)

    current_plan = SubscriptionPlan(body.plan)
    action = "upgrade"

    try:
        from app.services.subscription_plans import plan_rank
        tenant_obj = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
        current = SubscriptionPlan(tenant_obj.subscription_plan)
        current_cycle = tenant_obj.subscription_billing_cycle or "monthly"
        target_cycle = body.billing_cycle or current_cycle

        is_same_plan = (current == current_plan)
        is_cycle_upgrade = is_same_plan and (current_cycle == "monthly" and target_cycle == "annual")

        if plan_rank(current) > plan_rank(current_plan) or (is_same_plan and not is_cycle_upgrade):
            action = "downgrade"
    except Exception:
        pass

    tenant = create_plan_change_request(
        db=db,
        tenant_id=tenant_id,
        action=action,
        requested_plan=body.plan,
        reason=body.reason,
        requested_billing_cycle=body.billing_cycle,
        requested_effective_at_end=body.effective_at_end,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return SubscriptionRequestOut.model_validate(
        {"tenant_id": tenant.tenant_id, "hospital_name": tenant.hospital_name, **get_pending_request(tenant)}
    )


@router.post("/subscription/request-cancellation", response_model=SubscriptionRequestOut)
@limiter.limit("5/minute")
async def request_cancellation(
    request: Request,
    body: CancellationRequestCreate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Submit a cancellation request for super admin approval."""
    tenant_id = _get_tenant_id(user)
    tenant = create_cancellation_request(
        db=db,
        tenant_id=tenant_id,
        reason=body.reason,
        user_sub=user.sub,
        ip_address=request.client.host if request.client else None,
    )
    db.commit()
    return SubscriptionRequestOut.model_validate(
        {"tenant_id": tenant.tenant_id, "hospital_name": tenant.hospital_name, **get_pending_request(tenant)}
    )


@router.get("/subscription/requests", response_model=list[SubscriptionRequestOut] | SubscriptionRequestOut | dict)
@limiter.limit("30/minute")
async def get_my_request_status(
    request: Request,
    all: bool = False,
    status: str | None = None,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """View the current tenant's pending request status or full request history."""
    tenant_id = _get_tenant_id(user)
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if all:
        from app.services.subscription_request_service import list_subscription_requests
        requests_data = list_subscription_requests(db, tenant_id=tenant_id, status=status)
        return [SubscriptionRequestOut.model_validate(r) for r in requests_data]

    pending = get_pending_request(tenant)
    if not pending:
        return {}
    return SubscriptionRequestOut.model_validate(
        {"tenant_id": tenant.tenant_id, "hospital_name": tenant.hospital_name, **pending}
    )


@router.get("/subscription/invoices/{invoice_id}/download")
@limiter.limit("20/minute")
async def download_invoice_pdf(
    request: Request,
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Download a PDF version of an invoice."""
    tenant_id = _get_tenant_id(user)
    from app.models.saas import Invoice as InvoiceModel

    invoice = db.query(InvoiceModel).filter(
        InvoiceModel.invoice_id == invoice_id,
        InvoiceModel.tenant_id == tenant_id,
    ).first()
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    from app.models.master import Tenant as TenantModel
    tenant = db.query(TenantModel).filter(TenantModel.tenant_id == tenant_id).first()
    hospital_name = tenant.hospital_name if tenant else tenant_id

    from app.services.pdf_generator import generate_invoice_pdf

    pdf_bytes = generate_invoice_pdf(
        invoice_number=invoice.invoice_number or str(invoice.invoice_id),
        hospital_name=hospital_name,
        plan_name=invoice.plan_name,
        amount=invoice.amount,
        currency=invoice.currency,
        due_date=invoice.due_date,
        billing_period_start=invoice.billing_period_start,
        billing_period_end=invoice.billing_period_end,
        status=invoice.status,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="invoice_{invoice.invoice_number or invoice.invoice_id}.pdf"',
            "Content-Type": "application/pdf",
        },
    )


@router.get("/subscription/invoices/{invoice_id}/receipt")
@limiter.limit("20/minute")
async def download_receipt_pdf(
    request: Request,
    invoice_id: UUID,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
):
    """Download a PDF receipt for a paid invoice."""
    tenant_id = _get_tenant_id(user)
    from app.models.saas import Invoice as InvoiceModel, SaaSPayment as SaaSPaymentModel

    invoice = db.query(InvoiceModel).filter(
        InvoiceModel.invoice_id == invoice_id,
        InvoiceModel.tenant_id == tenant_id,
    ).first()
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found")

    payment = db.query(SaaSPaymentModel).filter(
        SaaSPaymentModel.invoice_id == invoice_id,
    ).order_by(SaaSPaymentModel.paid_at.desc()).first()

    from app.models.master import Tenant as TenantModel
    tenant = db.query(TenantModel).filter(TenantModel.tenant_id == tenant_id).first()
    hospital_name = tenant.hospital_name if tenant else tenant_id

    from app.services.pdf_generator import generate_receipt_pdf

    pdf_bytes = generate_receipt_pdf(
        invoice_number=invoice.invoice_number or str(invoice.invoice_id),
        hospital_name=hospital_name,
        amount=payment.amount if payment else invoice.amount,
        currency=invoice.currency,
        payment_method=payment.payment_method if payment else "N/A",
        reference_number=payment.reference_number if payment else None,
        paid_at=payment.paid_at if payment else invoice.paid_at,
    )

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="receipt_{invoice.invoice_number or invoice.invoice_id}.pdf"',
            "Content-Type": "application/pdf",
        },
    )


@router.get("/subscription/plans", response_model=list[PlanCatalogOut])
@limiter.limit("60/minute")
async def list_plans(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
):
    """List available subscription plans."""
    from app.models.saas import SubscriptionPlan as SubscriptionPlanModel
    from app.services.subscription_plans import PLAN_RANK, SubscriptionPlan
    
    plans = db.query(SubscriptionPlanModel).filter(SubscriptionPlanModel.is_active == True).order_by(SubscriptionPlanModel.monthly_price).all()
    
    result = []
    for p in plans:
        try:
            enum_val = SubscriptionPlan(p.plan_name.lower())
            rank = PLAN_RANK[enum_val]
        except ValueError:
            rank = 99
            
        result.append({
            "plan": p.plan_name.lower(),
            "plan_id": p.plan_id,
            "display_name": p.plan_name.title(),
            "monthly_price": int(p.monthly_price),
            "annual_price": int(p.annual_price),
            "trial_days": 14,
            "max_users": p.max_users,
            "storage_gb": p.storage_gb,
            "features": sorted(p.modules_included or []),
            "rank": rank,
            "plan_name": p.plan_name.title(),
            "modules_included": sorted(p.modules_included or []),
            "uptime_sla_pct": float(p.uptime_sla_pct),
            "backup_frequency_hours": p.backup_frequency_hours,
        })
    return result


@router.get("/subscription/invoices", response_model=list[InvoiceOut])
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
    from app.services.provision import get_tenant_db_session
    from sqlalchemy import text

    user_count = 0
    active_user_count = 0
    patient_count = 0
    db_size_bytes = 0
    try:
        tenant_db = get_tenant_db_session(tenant_id)
        result = tenant_db.execute(text("SELECT COUNT(*) FROM users"))
        user_count = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM users WHERE is_active = true"))
        active_user_count = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM patients"))
        patient_count = result.scalar() or 0
        
        db_name = tenant_db.get_bind().url.database
        result = tenant_db.execute(
            text("SELECT pg_database_size(:db)"),
            {"db": db_name},
        )
        db_size_bytes = result.scalar() or 0
    except Exception:
        pass
    finally:
        if 'tenant_db' in locals():
            tenant_db.close()
            
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
        "user_count": user_count,
        "active_user_count": active_user_count,
        "patient_count": patient_count,
        "db_size_bytes": db_size_bytes,
        "db_size_mb": round(db_size_bytes / 1024 / 1024),
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
