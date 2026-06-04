from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.limiter import limiter
from app.services.keycloak_admin import (
    create_keycloak_user,
    ensure_roles,
    create_local_user,
)
from app.api.v1.admin.schemas import HospitalUserCreate, HospitalUserOut

router = APIRouter(dependencies=[Depends(require_role("hospital_admin"))])


@router.post("/users", response_model=HospitalUserOut, status_code=201)
@limiter.limit("30/minute")
async def create_hospital_user(
    request: Request,
    body: HospitalUserCreate,
    db: Session = Depends(get_db),
    admin: TokenPayload = Depends(get_current_active_user),
) -> HospitalUserOut:
    await ensure_roles(["hospital_user", "hospital_admin"])

    kc_roles = ["hospital_admin", "hospital_user"] if body.role == "hospital_admin" else ["hospital_user"]

    try:
        kc_sub = await create_keycloak_user(
            username=body.username,
            password=body.password,
            email=body.email,
            roles=kc_roles,
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create user in Keycloak: {e}",
        )

    user = create_local_user(
        db=db,
        keycloak_sub=kc_sub,
        email=body.email,
        hospital_id=body.hospital_id,
    )

    return HospitalUserOut(
        keycloak_sub=user.keycloak_sub,
        username=body.username,
        email=user.email,
        hospital_id=user.hospital_id,
    )
