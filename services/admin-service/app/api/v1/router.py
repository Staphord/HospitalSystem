from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.v1.admin.router import router as admin_router
from app.api.v1.admin.schemas import DepartmentOut, HospitalUserOut, InsuranceProviderOut
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db_for_request
from app.services import admin as admin_svc

shared_router = APIRouter()

@shared_router.get("/insurance-providers", response_model=list[InsuranceProviderOut], tags=["Shared Read-Only"])
async def get_shared_providers(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return [InsuranceProviderOut.model_validate(p) for p in admin_svc.list_insurance_providers(db)]

@shared_router.get("/departments", response_model=list[DepartmentOut], tags=["Shared Read-Only"])
async def get_shared_departments(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return [DepartmentOut.model_validate(d) for d in admin_svc.list_departments(db)]

@shared_router.get("/users", response_model=list[HospitalUserOut], tags=["Shared Read-Only"])
async def get_shared_users(
    request: Request,
    db: Session = Depends(get_tenant_db_for_request),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return [HospitalUserOut.model_validate(u) for u in admin_svc.list_users(db)]


router = APIRouter()
# Mount shared read-only endpoints before the protected admin_router
router.include_router(shared_router, prefix="/admin/shared")
router.include_router(admin_router, prefix="/admin")
