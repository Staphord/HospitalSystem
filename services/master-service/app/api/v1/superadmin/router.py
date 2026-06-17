import logging

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.limiter import limiter
from app.services.keycloak_realm import (
    setup_tenant_realm,
    verify_tenant_realm_exists,
    list_all_realms,
    get_realm_roles,
    create_realm_role,
    update_realm_role,
    delete_realm_role,
    get_all_realm_users,
)
from app.services.keycloak_admin import (
    create_keycloak_user,
    delete_keycloak_user,
    ensure_roles,
    create_local_user,
    update_local_user,
    delete_local_user,
)
from app.services import superadmin_auth as superadmin_auth_service
from app.services import subscription_service
from app.services.subscription_plans import (
    BillingCycle,
    SubscriptionPlan,
    plan_to_json,
)
from app.services.tenant_service import (
    cache_tenant_suspension,
    remove_tenant_suspension_cache,
)
from app.api.v1.superadmin.schemas import (
    SuperAdminCreate,
    SuperAdminUpdate,
    SuperAdminOut,
    SuperAdminDelete,
    TenantOut,
    TenantCreate,
    TenantUpdate,
    RoleCreate,
    SubscriptionSubscribeRequest,
    SubscriptionPlanChangeRequest,
    SubscriptionRenewRequest,
    TenantSuspendRequest,
    TenantTerminateRequest,
    SubscriptionStateOut,
    SubscriptionActionOut,
    PlanCatalogOut,
    SubscriptionPlanOut,
    SubscriptionOut,
    SubscriptionAuditLogOut,
    AnnouncementOut,
    AnnouncementCreate,
    AnnouncementUpdate,
    PlanCreate,
    PlanUpdate,
    InvoiceOut,
    InvoiceCreate,
    InvoiceUpdate,
    SaaSPaymentOut,
    SaaSPaymentCreate,
    SuperAdminAuditLogOut,
)
from app.models.user import User
from app.models.admin import SuperAdmin
from app.models.master import Tenant
from app.models.saas import SubscriptionAuditLog, Announcement

logger = logging.getLogger("master_service.superadmin")

router = APIRouter(dependencies=[Depends(require_role("super_admin"))])


def _request_meta(request: Request, user: TokenPayload) -> tuple[str | None, str | None]:
    """Return (user_sub, ip_address) for audit logging."""
    user_sub = getattr(request.state, "user_sub", user.sub)
    ip_address = request.client.host if request.client else None
    return user_sub, ip_address


# ---------------------------------------------------------------------------
# Super-admin user management
# ---------------------------------------------------------------------------

@router.post("/users", response_model=SuperAdminOut, status_code=201, tags=["Super Admin Users"])
@limiter.limit("30/minute")
async def create_user(
    request: Request,
    body: SuperAdminCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> SuperAdminOut:
    if body.role not in ("super_admin", "billing_manager", "support"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin can only create users with valid superadmin roles. "
                   "Use /admin/users for hospital-level user creation.",
        )

    await ensure_roles(["super_admin", "hospital_admin"], realm="master")

    kc_roles = ["super_admin", "hospital_admin"]

    try:
        kc_sub = await create_keycloak_user(
            username=body.username,
            password=body.password,
            email=body.email,
            roles=kc_roles,
            full_name=body.full_name or None,
            realm="master",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create user in Keycloak: {e}",
        )

    create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=body.username,
        full_name=body.full_name or None,
        email=body.email,
        role=body.role,
        hospital_id=None,
    )

    admin = superadmin_auth_service.create_superadmin(
        db=db,
        username=body.username,
        email=body.email,
        password=body.password,
        full_name=body.full_name,
        role=body.role,
        mfa_secret=body.mfa_secret,
    )

    return SuperAdminOut.model_validate(admin)


@router.patch("/users/{super_admin_id}", response_model=SuperAdminOut, tags=["Super Admin Users"])
@limiter.limit("30/minute")
async def update_user(
    request: Request,
    super_admin_id: str,
    body: SuperAdminUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> SuperAdminOut:
    admin = db.query(SuperAdmin).filter(SuperAdmin.super_admin_id == super_admin_id).first()
    if not admin:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Super admin not found")

    from app.services.keycloak_admin import update_keycloak_user, set_user_password
    # Look up the Keycloak user ID via the local User record
    user_record = db.query(User).filter(User.username == admin.username).first()
    kc_sub = user_record.keycloak_sub if user_record else None
    if body.username is not None or body.email is not None or (body.full_name is not None and body.full_name.strip()):
        if kc_sub:
            await update_keycloak_user(
                kc_sub,
                username=body.username,
                email=body.email,
                full_name=body.full_name,
                realm="master",
            )
    if body.password is not None:
        superadmin_auth_service.update_superadmin_password(db, admin, body.password)
        if kc_sub:
            await set_user_password(kc_sub, body.password, realm="master")

    if body.username is not None:
        admin.username = body.username
    if body.email is not None:
        admin.email = body.email
    if body.full_name is not None:
        admin.full_name = body.full_name
    if body.role is not None:
        admin.role = body.role
    if body.mfa_secret is not None:
        admin.mfa_secret = body.mfa_secret
    if body.is_active is not None:
        admin.is_active = body.is_active

    # Sync local User record with updated fields
    if user_record:
        if body.username is not None:
            user_record.username = body.username
        if body.email is not None:
            user_record.email = body.email
        if body.full_name is not None:
            user_record.full_name = body.full_name
        if body.role is not None:
            user_record.role = body.role

    db.commit()
    db.refresh(admin)
    return SuperAdminOut.model_validate(admin)


@router.delete("/users", status_code=204, tags=["Super Admin Users"])
@limiter.limit("30/minute")
async def delete_user(
    request: Request,
    body: SuperAdminDelete,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    kc_sub = await delete_keycloak_user(body.username, realm="master")
    if kc_sub:
        delete_local_user(db, kc_sub)

    admin = db.query(SuperAdmin).filter(SuperAdmin.username == body.username).first()
    if admin:
        db.delete(admin)
        db.commit()


@router.get("/users", response_model=list[SuperAdminOut], tags=["Super Admin Users"])
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SuperAdminOut]:
    admins = db.query(SuperAdmin).all()
    return [SuperAdminOut.model_validate(a) for a in admins]


# ---------------------------------------------------------------------------
# Tenant management
# ---------------------------------------------------------------------------

@router.get("/tenants", response_model=list[TenantOut], tags=["Tenants"])
@limiter.limit("30/minute")
async def list_tenants(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[TenantOut]:
    tenants = db.query(Tenant).order_by(Tenant.created_at.desc()).all()
    return [TenantOut.model_validate(t) for t in tenants]


@router.post("/tenants", response_model=TenantOut, status_code=201, tags=["Tenants"])
@limiter.limit("10/minute")
async def create_tenant(
    request: Request,
    body: TenantCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> TenantOut:
    import uuid
    from app.config import settings
    from app.services.tenant_service import encrypt_dsn

    tenant_id = f"hosp-{uuid.uuid4().hex[:8]}"
    existing = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Tenant ID collision")

    tenant = Tenant(
        tenant_id=tenant_id,
        name=body.hospital_name,
        db_dsn_encrypted=encrypt_dsn(f"postgresql://placeholder@{tenant_id}:5432/{tenant_id}"),
        status="trial",
        is_active=True,
        country=body.country or "",
        city=body.city or "",
        address=body.address,
        primary_contact_name=body.primary_contact_name or "",
        primary_contact_email=body.primary_contact_email or "",
        primary_contact_phone=body.primary_contact_phone or "",
        billing_email=body.billing_email or "",
        timezone=body.timezone or "UTC",
        currency=body.currency or "USD",
        date_format=body.date_format or "%Y-%m-%d",
        logo_url=body.logo_url,
        data_region=body.data_region,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    # Create per-tenant Keycloak realm and verify it exists
    try:
        await setup_tenant_realm(tenant_id)
        exists = await verify_tenant_realm_exists(tenant_id)
        if exists:
            tenant.keycloak_realm = tenant_id
        else:
            logger.warning("Realm %s was not actually created after setup, falling back", tenant_id)
            tenant.keycloak_realm = settings.keycloak_realm
        db.commit()
    except Exception as e:
        logger.warning("Failed to setup tenant realm %s: %s", tenant_id, e)
        tenant.keycloak_realm = settings.keycloak_realm
        db.commit()

    realm = tenant.keycloak_realm or settings.keycloak_realm

    # Superadmin-created tenants start with a free trial; the tenant admin
    # can upgrade later through self-service.
    subscription_service.subscribe_tenant(
        db=db,
        tenant_id=tenant.tenant_id,
        plan=SubscriptionPlan.FREE_TRIAL,
        billing_cycle=BillingCycle.MONTHLY,
    )
    db.commit()
    db.refresh(tenant)

    await ensure_roles(["hospital_user", "hospital_admin"], realm=realm)
    try:
        kc_sub = await create_keycloak_user(
            username=body.admin_username,
            password=body.admin_password,
            email=body.admin_email,
            roles=["hospital_admin", "hospital_user"],
            full_name=body.admin_full_name or None,
            realm=realm,
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create admin user: {e}",
        )

    from app.services.keycloak_admin import set_user_attribute
    await set_user_attribute(kc_sub, "tenant_id", tenant_id, realm=realm)

    # Create tenant database synchronously and store user in tenant DB
    # NO FALLBACK: tenant database MUST be created, or tenant creation fails
    from app.services.provision import provision_tenant_database_sync, get_tenant_db_session
    dsn = provision_tenant_database_sync(tenant_id, body.hospital_name)

    # Create local user in the tenant database (not master DB)
    tenant_db = get_tenant_db_session(tenant_id)
    try:
        create_local_user(
            db=tenant_db,
            keycloak_sub=kc_sub,
            username=body.admin_username,
            full_name=body.admin_full_name or None,
            email=body.admin_email,
            role="hospital_admin",
            hospital_id=tenant_id,
            force_password_change=True,
        )
    finally:
        tenant_db.close()

    return TenantOut.model_validate(tenant)


@router.patch("/tenants/{tenant_id}", response_model=TenantOut, tags=["Tenants"])
@limiter.limit("30/minute")
async def update_tenant(
    request: Request,
    tenant_id: str,
    body: TenantUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> TenantOut:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    if body.name is not None:
        tenant.name = body.name
    if body.status is not None:
        tenant.status = body.status
        # Status is the single source of truth for the active flag.
        tenant.is_active = body.status not in ("suspended", "terminated")
    if body.is_active is not None:
        tenant.is_active = body.is_active
        if not body.is_active:
            tenant.status = "suspended"
        elif body.is_active and tenant.status in ("suspended", "terminated"):
            tenant.status = "active"

    if body.country is not None:
        tenant.country = body.country
    if body.city is not None:
        tenant.city = body.city
    if body.address is not None:
        tenant.address = body.address
    if body.primary_contact_name is not None:
        tenant.primary_contact_name = body.primary_contact_name
    if body.primary_contact_email is not None:
        tenant.primary_contact_email = body.primary_contact_email
    if body.primary_contact_phone is not None:
        tenant.primary_contact_phone = body.primary_contact_phone
    if body.billing_email is not None:
        tenant.billing_email = body.billing_email
    if body.timezone is not None:
        tenant.timezone = body.timezone
    if body.currency is not None:
        tenant.currency = body.currency
    if body.date_format is not None:
        tenant.date_format = body.date_format
    if body.logo_url is not None:
        tenant.logo_url = body.logo_url
    if body.data_region is not None:
        tenant.data_region = body.data_region

    db.commit()
    db.refresh(tenant)
    return TenantOut.model_validate(tenant)


# ---------------------------------------------------------------------------
# Subscription lifecycle endpoints
# ---------------------------------------------------------------------------

def _serialize_state(tenant: Tenant) -> dict:
    return subscription_service.get_subscription_state(tenant)


@router.get("/plans", response_model=list[PlanCatalogOut], tags=["Subscription Plans"])
@limiter.limit("100/minute")
async def list_plans(
    request: Request,
    _: TokenPayload = Depends(get_current_active_user),
) -> list[PlanCatalogOut]:
    """Return the canonical subscription plan catalog."""
    return [PlanCatalogOut.model_validate(plan_to_json(p)) for p in SubscriptionPlan]


# ---------------------------------------------------------------------------
# Plan CRUD endpoints
# ---------------------------------------------------------------------------

@router.post("/plans", response_model=SubscriptionPlanOut, status_code=201, tags=["Subscription Plans"])
@limiter.limit("30/minute")
async def create_plan(
    request: Request,
    body: PlanCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionPlanOut:
    from app.models.saas import SubscriptionPlan as SubscriptionPlanModel
    plan = SubscriptionPlanModel(
        plan_name=body.plan_name,
        description=body.description,
        max_users=body.max_users,
        max_patients=body.max_patients,
        storage_gb=body.storage_gb,
        modules_included=body.modules_included,
        monthly_price=body.monthly_price,
        annual_price=body.annual_price,
        annual_discount_pct=body.annual_discount_pct,
        uptime_sla_pct=body.uptime_sla_pct,
        backup_frequency_hours=body.backup_frequency_hours,
        is_active=body.is_active,
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return SubscriptionPlanOut.model_validate(plan)


@router.patch("/plans/{plan_id}", response_model=SubscriptionPlanOut, tags=["Subscription Plans"])
@limiter.limit("30/minute")
async def update_plan(
    request: Request,
    plan_id: UUID,
    body: PlanUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionPlanOut:
    from app.models.saas import SubscriptionPlan as SubscriptionPlanModel
    plan = db.query(SubscriptionPlanModel).filter(SubscriptionPlanModel.plan_id == plan_id).first()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Plan not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return SubscriptionPlanOut.model_validate(plan)


@router.delete("/plans/{plan_id}", status_code=204, tags=["Subscription Plans"])
@limiter.limit("30/minute")
async def delete_plan(
    request: Request,
    plan_id: UUID,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    from app.models.saas import SubscriptionPlan as SubscriptionPlanModel
    plan = db.query(SubscriptionPlanModel).filter(SubscriptionPlanModel.plan_id == plan_id).first()
    if not plan:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Plan not found")
    db.delete(plan)
    db.commit()


@router.get("/tenants/{tenant_id}/subscription", response_model=SubscriptionStateOut, tags=["Subscriptions"])
@limiter.limit("60/minute")
async def get_subscription(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionStateOut:
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return SubscriptionStateOut.model_validate(_serialize_state(tenant))


@router.post("/tenants/{tenant_id}/subscribe", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def subscribe_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: SubscriptionSubscribeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    try:
        plan = SubscriptionPlan(body.plan)
        billing_cycle = BillingCycle(body.billing_cycle)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    result = subscription_service.subscribe_tenant(
        db=db,
        tenant_id=tenant_id,
        plan=plan,
        billing_cycle=billing_cycle,
        start_trial=body.start_trial,
        user_sub=user_sub,
        ip_address=ip_address,
        payment_provider_id=body.payment_provider_id,
    )
    db.commit()

    # If the tenant was previously suspended, clear the blocklist.
    await remove_tenant_suspension_cache(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/upgrade", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def upgrade_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    try:
        new_plan = SubscriptionPlan(body.plan)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service.upgrade_subscription(
        db=db,
        tenant_id=tenant_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/downgrade", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def downgrade_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: SubscriptionPlanChangeRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    try:
        new_plan = SubscriptionPlan(body.plan)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service.downgrade_subscription(
        db=db,
        tenant_id=tenant_id,
        new_plan=new_plan,
        billing_cycle=billing_cycle,
        effective_at_end=body.effective_at_end,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/renew", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def renew_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: SubscriptionRenewRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    billing_cycle = BillingCycle(body.billing_cycle) if body.billing_cycle else None

    result = subscription_service.renew_subscription(
        db=db,
        tenant_id=tenant_id,
        billing_cycle=billing_cycle,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()

    # Renewal may lift a suspension blocklist entry.
    await remove_tenant_suspension_cache(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/activate", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def activate_tenant_endpoint(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    result = subscription_service.activate_tenant(
        db=db,
        tenant_id=tenant_id,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()
    await remove_tenant_suspension_cache(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/suspend", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def suspend_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: TenantSuspendRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    result = subscription_service.suspend_tenant(
        db=db,
        tenant_id=tenant_id,
        reason=body.reason,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()

    # Side effects: cache suspension and revoke active sessions.
    await cache_tenant_suspension(tenant_id)
    from app.services.tenant_service import _revoke_keycloak_sessions
    await _revoke_keycloak_sessions(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/reactivate", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def reactivate_tenant_endpoint(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)
    result = subscription_service.reactivate_tenant(
        db=db,
        tenant_id=tenant_id,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()
    await remove_tenant_suspension_cache(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


@router.post("/tenants/{tenant_id}/terminate", response_model=SubscriptionActionOut, tags=["Subscriptions"])
@limiter.limit("10/minute")
async def terminate_tenant_endpoint(
    request: Request,
    tenant_id: str,
    body: TenantTerminateRequest,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SubscriptionActionOut:
    user_sub, ip_address = _request_meta(request, user)

    # Optionally delete the tenant's Keycloak realm
    from app.config import settings
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if tenant and tenant.keycloak_realm and tenant.keycloak_realm != settings.keycloak_realm:
        try:
            from app.services.keycloak_realm import delete_tenant_realm
            await delete_tenant_realm(tenant.keycloak_realm)
        except Exception as exc:
            logger.warning("Failed to delete tenant realm %s: %s", tenant.keycloak_realm, exc)

    result = subscription_service.terminate_tenant(
        db=db,
        tenant_id=tenant_id,
        reason=body.reason,
        user_sub=user_sub,
        ip_address=ip_address,
    )
    db.commit()

    # Termination is irreversible: block the tenant permanently.
    await cache_tenant_suspension(tenant_id)
    from app.services.tenant_service import _revoke_keycloak_sessions
    await _revoke_keycloak_sessions(tenant_id)

    state = _serialize_state(result.tenant)
    return SubscriptionActionOut.model_validate(
        {
            "tenant_id": tenant_id,
            "action": result.action,
            "previous_status": result.previous_status,
            "previous_plan": result.previous_plan,
            **state,
        }
    )


# ---------------------------------------------------------------------------
# SaaS schema read endpoints
# ---------------------------------------------------------------------------

@router.get("/subscription-plans", response_model=list[SubscriptionPlanOut], tags=["Subscription Plans"])
@limiter.limit("60/minute")
async def list_subscription_plans(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SubscriptionPlanOut]:
    from app.models.saas import SubscriptionPlan as SubscriptionPlanModel
    plans = db.query(SubscriptionPlanModel).order_by(SubscriptionPlanModel.monthly_price).all()
    return [SubscriptionPlanOut.model_validate(p) for p in plans]


@router.get("/tenants/{tenant_id}/subscriptions", response_model=list[SubscriptionOut], tags=["Subscriptions"])
@limiter.limit("60/minute")
async def list_tenant_subscriptions(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SubscriptionOut]:
    from app.models.saas import Subscription as SubscriptionModel
    subs = (
        db.query(SubscriptionModel)
        .filter(SubscriptionModel.tenant_id == tenant_id)
        .order_by(SubscriptionModel.created_at.desc())
        .all()
    )
    return [SubscriptionOut.model_validate(s) for s in subs]


@router.get("/tenants/{tenant_id}/subscription-audit-log", response_model=list[SubscriptionAuditLogOut], tags=["Subscriptions"])
@limiter.limit("60/minute")
async def list_tenant_subscription_audit_log(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SubscriptionAuditLogOut]:
    logs = (
        db.query(SubscriptionAuditLog)
        .filter(SubscriptionAuditLog.tenant_id == tenant_id)
        .order_by(SubscriptionAuditLog.created_at.desc())
        .all()
    )
    return [SubscriptionAuditLogOut.model_validate(l) for l in logs]


@router.get("/announcements", response_model=list[AnnouncementOut], tags=["Announcements"])
@limiter.limit("60/minute")
async def list_announcements(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[AnnouncementOut]:
    from app.models.saas import Announcement as AnnouncementModel
    announcements = db.query(AnnouncementModel).order_by(AnnouncementModel.publish_at.desc()).all()
    return [AnnouncementOut.model_validate(a) for a in announcements]


@router.post("/announcements", response_model=AnnouncementOut, status_code=201, tags=["Announcements"])
@limiter.limit("10/minute")
async def create_announcement(
    request: Request,
    body: AnnouncementCreate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> AnnouncementOut:
    from app.models.saas import Announcement as AnnouncementModel

    # Map Keycloak user sub to super_admin_id via local SuperAdmin record.
    admin = db.query(SuperAdmin).filter(SuperAdmin.username == user.preferred_username).first()
    created_by = admin.super_admin_id if admin else None

    announcement = AnnouncementModel(
        title=body.title,
        body=body.body,
        audience=body.audience,
        target_tenant_ids=body.target_tenant_ids,
        publish_at=body.publish_at,
        expires_at=body.expires_at,
        created_by=created_by,
    )
    db.add(announcement)
    db.commit()
    db.refresh(announcement)
    return AnnouncementOut.model_validate(announcement)


@router.patch("/announcements/{announcement_id}", response_model=AnnouncementOut, tags=["Announcements"])
@limiter.limit("30/minute")
async def update_announcement(
    request: Request,
    announcement_id: UUID,
    body: AnnouncementUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> AnnouncementOut:
    from app.models.saas import Announcement as AnnouncementModel
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.announcement_id == announcement_id).first()
    if not announcement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(announcement, field, value)
    db.commit()
    db.refresh(announcement)
    return AnnouncementOut.model_validate(announcement)


@router.delete("/announcements/{announcement_id}", status_code=204, tags=["Announcements"])
@limiter.limit("30/minute")
async def delete_announcement(
    request: Request,
    announcement_id: UUID,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    from app.models.saas import Announcement as AnnouncementModel
    announcement = db.query(AnnouncementModel).filter(AnnouncementModel.announcement_id == announcement_id).first()
    if not announcement:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    db.delete(announcement)
    db.commit()


@router.post("/roles", status_code=201, tags=["Roles"])
@limiter.limit("30/minute")
async def create_role(
    request: Request,
    body: RoleCreate,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Create a role in the default realm (backwards compatible)."""
    await ensure_roles([body.name])
    return {"detail": f"Role '{body.name}' ensured in default realm"}


@router.post("/realms/{realm}/roles", status_code=201, tags=["Roles"])
@limiter.limit("30/minute")
async def create_realm_role_endpoint(
    request: Request,
    realm: str,
    body: RoleCreate,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Create a role in a specific Keycloak realm."""
    try:
        await create_realm_role(realm, body.name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create role in realm '{realm}': {e}",
        )
    return {"detail": f"Role '{body.name}' created in realm '{realm}'"}


@router.get("/realms/{realm}/roles", tags=["Roles"])
@limiter.limit("60/minute")
async def list_realm_roles_endpoint(
    request: Request,
    realm: str,
    _: TokenPayload = Depends(get_current_active_user),
) -> list[dict]:
    """List all realm-level roles in a specific Keycloak realm."""
    try:
        roles = await get_realm_roles(realm)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list roles in realm '{realm}': {e}",
        )
    return roles


@router.put("/realms/{realm}/roles/{role_name}", tags=["Roles"])
@limiter.limit("30/minute")
async def update_realm_role_endpoint(
    request: Request,
    realm: str,
    role_name: str,
    body: RoleCreate,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Update a role name in a specific Keycloak realm."""
    try:
        await update_realm_role(realm, role_name, new_name=body.name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to update role '{role_name}' in realm '{realm}': {e}",
        )
    return {"detail": f"Role '{role_name}' updated to '{body.name}' in realm '{realm}'"}


@router.delete("/realms/{realm}/roles/{role_name}", status_code=204, tags=["Roles"])
@limiter.limit("30/minute")
async def delete_realm_role_endpoint(
    request: Request,
    realm: str,
    role_name: str,
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    """Delete a realm-level role from a specific Keycloak realm."""
    try:
        await delete_realm_role(realm, role_name)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to delete role '{role_name}' in realm '{realm}': {e}",
        )


# ---------------------------------------------------------------------------
# Keycloak realm management for super admins
# ---------------------------------------------------------------------------


@router.get("/realms", tags=["Realms"])
@limiter.limit("30/minute")
async def list_keycloak_realms(
    request: Request,
    _: TokenPayload = Depends(get_current_active_user),
) -> list[str]:
    """List all Keycloak realm names."""
    try:
        return await list_all_realms()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list Keycloak realms: {e}",
        )


@router.post("/tenants/{tenant_id}/ensure-realm", tags=["Tenants"])
@limiter.limit("10/minute")
async def ensure_tenant_realm(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Create or re-create the Keycloak realm for an existing tenant.

    This is idempotent — if the realm already exists, it will be reused.
    Falls back to the default shared realm on failure.
    """
    from app.config import settings
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    realm_name = tenant.keycloak_realm or tenant_id

    try:
        await setup_tenant_realm(realm_name)
        exists = await verify_tenant_realm_exists(realm_name)
        if exists:
            tenant.keycloak_realm = realm_name
            db.commit()
            # Re-ensure the basic roles exist in this realm
            await ensure_roles(["hospital_user", "hospital_admin"], realm=realm_name)
            return {
                "detail": f"Keycloak realm '{realm_name}' ensured for tenant '{tenant_id}'",
                "realm": realm_name,
            }
        else:
            # Fallback to default realm
            fallback = settings.keycloak_realm
            tenant.keycloak_realm = fallback
            db.commit()
            return {
                "detail": f"Could not create realm '{realm_name}', fell back to default '{fallback}'",
                "realm": fallback,
            }
    except Exception as e:
        logger.warning("Failed to ensure realm for tenant %s: %s", tenant_id, e)
        tenant.keycloak_realm = settings.keycloak_realm
        db.commit()
        return {
            "detail": f"Failed to create realm, fell back to default '{settings.keycloak_realm}': {e}",
            "realm": settings.keycloak_realm,
        }


@router.get("/users", tags=["Users"])
@limiter.limit("10/minute")
async def list_all_keycloak_users(
    request: Request,
    _: TokenPayload = Depends(get_current_active_user),
) -> list[dict]:
    """List all users across all Keycloak realms with their realm and username."""
    try:
        realms = await list_all_realms()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to list Keycloak realms: {e}",
        )

    all_users = []
    for realm_name in realms:
        try:
            users = await get_all_realm_users(realm_name)
            for user in users:
                all_users.append({
                    "id": user.get("id"),
                    "username": user.get("username"),
                    "email": user.get("email"),
                    "firstName": user.get("firstName"),
                    "lastName": user.get("lastName"),
                    "enabled": user.get("enabled"),
                    "emailVerified": user.get("emailVerified"),
                    "realm": realm_name,
                    "createdTimestamp": user.get("createdTimestamp"),
                })
        except Exception as exc:
            logger.warning("Failed to list users in realm %s: %s", realm_name, exc)

    return all_users


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/invoices", response_model=list[InvoiceOut], tags=["Invoices"])
@limiter.limit("60/minute")
async def list_tenant_invoices(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[InvoiceOut]:
    from app.models.saas import Invoice as InvoiceModel
    invoices = (
        db.query(InvoiceModel)
        .filter(InvoiceModel.tenant_id == tenant_id)
        .order_by(InvoiceModel.issued_at.desc())
        .all()
    )
    return [InvoiceOut.model_validate(inv) for inv in invoices]


@router.post("/tenants/{tenant_id}/invoices", response_model=InvoiceOut, status_code=201, tags=["Invoices"])
@limiter.limit("30/minute")
async def create_tenant_invoice(
    request: Request,
    tenant_id: str,
    body: InvoiceCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> InvoiceOut:
    from app.models.saas import Invoice as InvoiceModel
    invoice = InvoiceModel(
        tenant_id=tenant_id,
        subscription_id=body.subscription_id,
        invoice_number=body.invoice_number,
        billing_period_start=body.billing_period_start,
        billing_period_end=body.billing_period_end,
        plan_name=body.plan_name,
        amount=body.amount,
        currency=body.currency,
        due_date=body.due_date,
        status=body.status,
    )
    db.add(invoice)
    db.commit()
    db.refresh(invoice)
    return InvoiceOut.model_validate(invoice)


@router.patch("/invoices/{invoice_id}", response_model=InvoiceOut, tags=["Invoices"])
@limiter.limit("30/minute")
async def update_invoice(
    request: Request,
    invoice_id: UUID,
    body: InvoiceUpdate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> InvoiceOut:
    from app.models.saas import Invoice as InvoiceModel
    invoice = db.query(InvoiceModel).filter(InvoiceModel.invoice_id == invoice_id).first()
    if not invoice:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Invoice not found")
    if body.status is not None:
        invoice.status = body.status
    if body.paid_at is not None:
        invoice.paid_at = body.paid_at
    db.commit()
    db.refresh(invoice)
    return InvoiceOut.model_validate(invoice)


# ---------------------------------------------------------------------------
# SaaS Payments
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/payments", response_model=list[SaaSPaymentOut], tags=["Payments"])
@limiter.limit("60/minute")
async def list_tenant_payments(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SaaSPaymentOut]:
    from app.models.saas import SaaSPayment as SaaSPaymentModel
    payments = (
        db.query(SaaSPaymentModel)
        .filter(SaaSPaymentModel.tenant_id == tenant_id)
        .order_by(SaaSPaymentModel.paid_at.desc())
        .all()
    )
    return [SaaSPaymentOut.model_validate(p) for p in payments]


@router.post("/tenants/{tenant_id}/payments", response_model=SaaSPaymentOut, status_code=201, tags=["Payments"])
@limiter.limit("30/minute")
async def create_tenant_payment(
    request: Request,
    tenant_id: str,
    body: SaaSPaymentCreate,
    db: Session = Depends(get_db),
    user: TokenPayload = Depends(get_current_active_user),
) -> SaaSPaymentOut:
    from app.models.saas import SaaSPayment as SaaSPaymentModel
    from uuid import UUID as PyUUID

    # Map Keycloak user to local super_admin_id.
    admin = db.query(SuperAdmin).filter(SuperAdmin.username == user.preferred_username).first()
    recorded_by = admin.super_admin_id if admin else None
    if recorded_by is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Super admin record required to record payments")

    payment = SaaSPaymentModel(
        invoice_id=body.invoice_id,
        tenant_id=tenant_id,
        amount=body.amount,
        currency=body.currency,
        payment_method=body.payment_method,
        reference_number=body.reference_number,
        receipt_sent_at=body.receipt_sent_at,
        recorded_by=recorded_by,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return SaaSPaymentOut.model_validate(payment)


# ---------------------------------------------------------------------------
# Super Admin Audit Log
# ---------------------------------------------------------------------------


@router.get("/audit-log", response_model=list[SuperAdminAuditLogOut], tags=["Audit Log"])
@limiter.limit("60/minute")
async def list_super_admin_audit_log(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[SuperAdminAuditLogOut]:
    from app.models.saas import SuperAdminAuditLog as SuperAdminAuditLogModel
    logs = (
        db.query(SuperAdminAuditLogModel)
        .order_by(SuperAdminAuditLogModel.created_at.desc())
        .all()
    )
    return [SuperAdminAuditLogOut.model_validate(l) for l in logs]


# ---------------------------------------------------------------------------
# System Health Dashboard
# ---------------------------------------------------------------------------


@router.get("/health", response_model=dict, tags=["System Health"])
@limiter.limit("60/minute")
async def system_health(
    request: Request,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Aggregate health status from all downstream services."""
    import httpx
    from app.config import settings

    services = {
        "api-gateway": settings.api_gateway_url or "http://api-gateway:8000",
        "auth-service": settings.auth_service_url or "http://auth-service:8001",
        "master-service": settings.master_service_url or "http://master-service:8002",
        "admin-service": settings.admin_service_url or "http://admin-service:8018",
        "reception-service": settings.reception_service_url or "http://reception-service:8010",
        "triage-service": settings.triage_service_url or "http://triage-service:8011",
        "consultation-service": settings.consultation_service_url or "http://consultation-service:8012",
        "laboratory-service": settings.laboratory_service_url or "http://laboratory-service:8013",
        "radiology-service": settings.radiology_service_url or "http://radiology-service:8014",
        "pharmacy-service": settings.pharmacy_service_url or "http://pharmacy-service:8015",
        "billing-service": settings.billing_service_url or "http://billing-service:8016",
        "ward-service": settings.ward_service_url or "http://ward-service:8017",
        "notification-service": settings.notification_service_url or "http://notification-service:8019",
        "report-service": settings.report_service_url or "http://report-service:8020",
    }

    results = {}
    healthy_count = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        for name, url in services.items():
            try:
                resp = await client.get(f"{url}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    status_val = data.get("status", "unknown")
                    results[name] = {"status": status_val, "response_time_ms": None}
                    if status_val == "ok":
                        healthy_count += 1
                else:
                    results[name] = {"status": "error", "error": f"HTTP {resp.status_code}"}
            except Exception as exc:
                results[name] = {"status": "unreachable", "error": str(exc)[:100]}

    return {
        "overall": "healthy" if healthy_count == len(services) else "degraded" if healthy_count > 0 else "unhealthy",
        "healthy_count": healthy_count,
        "total_count": len(services),
        "services": results,
    }


# ---------------------------------------------------------------------------
# Per-tenant Usage Statistics
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/stats", response_model=dict, tags=["Tenant Stats"])
@limiter.limit("60/minute")
async def get_tenant_usage_stats(
    request: Request,
    tenant_id: str,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Return usage statistics for a specific tenant.

    Includes: user counts (local DB + Keycloak), patient records per month,
    storage consumed, and estimated API call volume.
    """
    tenant = db.query(Tenant).filter(Tenant.tenant_id == tenant_id).first()
    if not tenant:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    from app.config import settings
    from app.services.provision import get_tenant_db_session
    from sqlalchemy import text
    from datetime import datetime, timezone

    # --- Local DB counts (single tenant_db session) ---
    user_count = 0
    active_user_count = 0
    patient_count = 0
    patients_this_month = 0
    visit_count = 0
    appointment_count = 0
    db_size_bytes = 0
    try:
        tenant_db = get_tenant_db_session(tenant_id)
        result = tenant_db.execute(text("SELECT COUNT(*) FROM users"))
        user_count = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM users WHERE is_active = true"))
        active_user_count = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM patients"))
        patient_count = result.scalar() or 0
        result = tenant_db.execute(
            text("SELECT COUNT(*) FROM patients WHERE created_at >= :start"),
            {"start": datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)},
        )
        patients_this_month = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM visits"))
        visit_count = result.scalar() or 0
        result = tenant_db.execute(text("SELECT COUNT(*) FROM appointments"))
        appointment_count = result.scalar() or 0
        # Database size
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

    # --- Keycloak user count (from tenant realm) ---
    kc_user_count = 0
    kc_active_count = 0
    realm = tenant.keycloak_realm or settings.keycloak_realm
    if realm != settings.keycloak_realm:  # Only for per-tenant realms
        try:
            from app.services.keycloak_admin import _get_admin_token
            token = await _get_admin_token()
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as c:
                hdrs = {"Authorization": f"Bearer {token}"}
                kc_url = f"{settings.keycloak_url}/admin/realms/{realm}"
                r = await c.get(f"{kc_url}/users/count", headers=hdrs)
                if r.is_success:
                    kc_user_count = int(r.text)
                r2 = await c.get(f"{kc_url}/users/count", headers=hdrs, params={"enabled": "true"})
                if r2.is_success:
                    kc_active_count = int(r2.text)
        except Exception as exc:
            logger.warning("Failed to query Keycloak for realm %s: %s", realm, exc)

    # --- API call volume (estimate from global_audit_log per tenant) ---
    api_calls_this_month = 0
    try:
        result = db.execute(
            text("SELECT COUNT(*) FROM global_audit_logs WHERE tenant_id = :tid AND created_at >= :start"),
            {"tid": tenant_id, "start": datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)},
        )
        api_calls_this_month = result.scalar() or 0
    except Exception:
        pass

    max_users = None
    try:
        from app.services.subscription_plans import SubscriptionPlan
        plan = getattr(tenant, "subscription_plan", None)
        if plan:
            sp = SubscriptionPlan.from_slug(plan)
            if sp:
                max_users = sp.max_users
    except Exception:
        pass

    usage_pct = None
    if max_users and max_users > 0:
        usage_pct = round((kc_active_count / max_users) * 100, 1)

    return {
        "tenant_id": tenant_id,
        "tenant_name": tenant.name,
        "user_count": user_count,
        "active_user_count": active_user_count,
        "kc_user_count": kc_user_count,
        "kc_active_user_count": kc_active_count,
        "patient_count": patient_count,
        "patients_this_month": patients_this_month,
        "visit_count": visit_count,
        "appointment_count": appointment_count,
        "db_size_bytes": db_size_bytes,
        "db_size_mb": round(db_size_bytes / 1024 / 1024, 2) if db_size_bytes else 0,
        "api_calls_this_month": api_calls_this_month,
        "subscription_plan": tenant.subscription_plan,
        "subscription_status": tenant.subscription_status,
        "max_users": max_users,
        "usage_pct": usage_pct,
    }
