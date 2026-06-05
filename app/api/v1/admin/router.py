from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.limiter import limiter
from app.services.keycloak_admin import (
    create_keycloak_user,
    ensure_roles,
    create_local_user,
    update_local_user,
    update_keycloak_user,
    set_user_attribute,
    get_local_users_by_hospital,
    delete_keycloak_user,
    delete_local_user,
)
from app.api.v1.admin.schemas import HospitalUserCreate, HospitalUserUpdate, HospitalUserOut
from app.models.user import User

router = APIRouter(dependencies=[Depends(require_role("hospital_admin"))])


@router.get("/users", response_model=list[HospitalUserOut])
@limiter.limit("30/minute")
async def list_hospital_users(
    request: Request,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> list[HospitalUserOut]:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")
    users = get_local_users_by_hospital(db=db, hospital_id=ctx.tenant_id)
    return [
        HospitalUserOut(
            keycloak_sub=u.keycloak_sub,
            username=u.username,
            full_name=u.full_name,
            email=u.email,
            role=u.role,
            hospital_id=u.hospital_id,
        )
        for u in users
    ]


@router.post("/users", response_model=HospitalUserOut, status_code=201)
@limiter.limit("30/minute")
async def create_hospital_user(
    request: Request,
    body: HospitalUserCreate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalUserOut:
    if not ctx.tenant_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="No tenant association")

    await ensure_roles(["hospital_admin", "hospital_user", "nurse", "clinician", "doctor", "patient"])

    kc_roles_map = {
        "hospital_admin": ["hospital_admin", "hospital_user"],
        "hospital_user": ["hospital_user"],
        "nurse": ["hospital_user"],
        "clinician": ["hospital_user"],
        "doctor": ["hospital_user"],
        "patient": ["hospital_user"],
    }
    kc_roles = kc_roles_map.get(body.role, ["hospital_user"])

    try:
        kc_sub = await create_keycloak_user(
            username=body.username,
            password=body.password,
            email=body.email,
            roles=kc_roles,
            full_name=body.full_name or None,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create user in Keycloak: {e}",
        )

    await set_user_attribute(kc_sub, "tenant_id", ctx.tenant_id)

    user = create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        username=body.username,
        full_name=body.full_name or None,
        email=body.email,
        role=body.role,
        hospital_id=ctx.tenant_id,
    )

    return HospitalUserOut(
        keycloak_sub=user.keycloak_sub,
        username=user.username,
        full_name=user.full_name,
        email=user.email,
        role=user.role,
        hospital_id=user.hospital_id,
    )


@router.patch("/users/{sub}", response_model=HospitalUserOut)
@limiter.limit("30/minute")
async def update_hospital_user(
    request: Request,
    sub: str,
    body: HospitalUserUpdate,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> HospitalUserOut:
    user = db.query(User).filter(User.keycloak_sub == sub).first()
    if not user or user.hospital_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this hospital")

    if body.username is not None or body.email is not None or (body.full_name is not None and body.full_name.strip()):
        await update_keycloak_user(
            user.keycloak_sub,
            username=body.username,
            email=body.email,
            full_name=body.full_name,
        )
    if body.password is not None:
        from app.services.keycloak_admin import set_user_password
        await set_user_password(user.keycloak_sub, body.password)
    if body.role is not None:
        target_roles = ["hospital_admin", "hospital_user"] if body.role == "hospital_admin" else ["hospital_user"]
        from app.services.keycloak_admin import assign_user_roles
        await assign_user_roles(user.keycloak_sub, target_roles)

    updated = update_local_user(
        db=db,
        keycloak_sub=sub,
        username=body.username,
        full_name=body.full_name,
        email=body.email,
        role=body.role,
    )
    return HospitalUserOut(
        keycloak_sub=updated.keycloak_sub,
        username=updated.username,
        full_name=updated.full_name,
        email=updated.email,
        role=updated.role,
        hospital_id=updated.hospital_id,
    )


@router.delete("/users/{sub}", status_code=204)
@limiter.limit("30/minute")
async def delete_hospital_user(
    request: Request,
    sub: str,
    db: Session = Depends(get_db),
    ctx: TenantContext = Depends(get_current_tenant),
) -> None:
    user = db.query(User).filter(User.keycloak_sub == sub).first()
    if not user or user.hospital_id != ctx.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in this hospital")

    kc_deleted = await delete_keycloak_user(user.username or user.email or "")
    if not kc_deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found in Keycloak")
    delete_local_user(db, sub)
