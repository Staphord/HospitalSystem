from fastapi import APIRouter, Depends, Request

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.api.v1.schemas import (
    PatientRegisterRequest,
    PatientResponse,
    PatientSearchResponse,
)
from app.services.orchestrator import (
    delete_patient,
    get_patient,
    register_patient,
    search_patients,
)

router = APIRouter()


@router.get("/patients", response_model=PatientSearchResponse)
@limiter.limit("30/minute")
async def list_patients(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await search_patients(request)


@router.get("/patients/search", response_model=PatientSearchResponse)
@limiter.limit("30/minute")
async def search(
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await search_patients(request)


@router.post("/patients/register", response_model=PatientResponse)
@limiter.limit("10/minute")
async def register(
    body: PatientRegisterRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await register_patient(body, request)


@router.get("/patients/{patient_id}", response_model=PatientResponse)
@limiter.limit("30/minute")
async def get(
    patient_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await get_patient(patient_id, request)


@router.delete("/patients/{patient_id}", response_model=dict)
@limiter.limit("10/minute")
async def remove(
    patient_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await delete_patient(patient_id, request)
