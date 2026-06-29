from fastapi import APIRouter
from app.api.v1.superadmin.router import router as superadmin_router
from app.api.v1.tenant.router import router as tenant_router
from app.api.v1.monitoring.router import router as monitoring_router

router = APIRouter()
router.include_router(superadmin_router, prefix="/superadmin")
router.include_router(tenant_router, prefix="/tenant")
router.include_router(monitoring_router, prefix="/monitoring")
