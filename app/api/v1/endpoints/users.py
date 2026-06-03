from fastapi import APIRouter, Depends, Request

from app.core.security import TokenPayload, get_current_active_user, get_current_hospital_id
from app.core.limiter import limiter

router = APIRouter()


@router.get("/me")
@limiter.limit("30/minute")
async def me(
    request: Request,
    user: TokenPayload = Depends(get_current_active_user),
    hospital_id: str = Depends(get_current_hospital_id),
) -> dict:
    return {
        "sub": user.sub,
        "preferred_username": user.preferred_username,
        "email": user.email,
        "roles": user.realm_access.get("roles", []),
        "hospital_id": hospital_id,
    }
