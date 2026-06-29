from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.services.orchestrator import (
    delete_patient,
    get_patient,
    register_patient,
    search_patients,
)

router = APIRouter()


@router.get("/patients")
@limiter.limit("30/minute")
async def list_patients(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await search_patients(request)


@router.get("/patients/search")
@limiter.limit("30/minute")
async def search(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await search_patients(request)


@router.post("/patients/register")
@limiter.limit("10/minute")
async def register(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await register_patient(request)


@router.get("/patients/{patient_id}")
@limiter.limit("30/minute")
async def get(
    patient_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await get_patient(patient_id, request)


@router.delete("/patients/{patient_id}")
@limiter.limit("10/minute")
async def remove(
    patient_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await delete_patient(patient_id, request)
