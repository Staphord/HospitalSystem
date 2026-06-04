from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.limiter import limiter
from app.services.keycloak_admin import (
    create_keycloak_user,
    delete_keycloak_user,
    ensure_roles,
    create_local_user,
    delete_local_user,
)
from app.api.v1.superadmin.schemas import UserCreate, UserOut, UserDelete

router = APIRouter(dependencies=[Depends(require_role("super_admin"))])


@router.post("/users", response_model=UserOut, status_code=201)
@limiter.limit("30/minute")
async def create_user(
    request: Request,
    body: UserCreate,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> UserOut:
    await ensure_roles(["hospital_user", "hospital_admin", "super_admin"])

    roles_map = {
        "super_admin": ["super_admin", "hospital_admin"],
        "hospital_admin": ["hospital_admin", "hospital_user"],
        "hospital_user": ["hospital_user"],
    }
    kc_roles = roles_map.get(body.role, ["hospital_user"])

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

    return UserOut(
        keycloak_sub=user.keycloak_sub,
        username=body.username,
        email=user.email,
        hospital_id=user.hospital_id,
    )


@router.delete("/users", status_code=204)
@limiter.limit("30/minute")
async def delete_user(
    request: Request,
    body: UserDelete,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> None:
    kc_sub = await delete_keycloak_user(body.username)
    if not kc_sub:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User '{body.username}' not found in Keycloak",
        )
    delete_local_user(db, kc_sub)


@router.get("/users", response_model=list[UserOut])
@limiter.limit("30/minute")
async def list_users(
    request: Request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> list[UserOut]:
    from app.models.user import User
    users = db.query(User).all()
    return [
        UserOut(
            keycloak_sub=u.keycloak_sub,
            username="(unknown)",
            email=u.email,
            hospital_id=u.hospital_id,
        )
        for u in users
    ]
