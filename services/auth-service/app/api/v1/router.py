from fastapi import APIRouter, Depends

from app.core.security import get_current_active_user
from app.core.tenant_auth import get_current_tenant
from .auth.router import public_router as auth_public_router
from .auth.router import router as auth_router
from .users.router import router as users_router

router = APIRouter()
protected_router = APIRouter(dependencies=[Depends(get_current_tenant)])

@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}

router.include_router(auth_public_router, prefix="/auth", tags=["auth"])
protected_router.include_router(auth_router, prefix="/auth", tags=["auth"])
protected_router.include_router(users_router, tags=["users"])

router.include_router(protected_router)
