from typing import Optional

from fastapi import APIRouter, Depends, Query, Request, status

from app.core.limiter import limiter
from app.core.tenant_auth import get_current_tenant, TenantContext
from app.api.v1.schemas import (
    InsurancePolicyCreateRequest,
    InsurancePolicyResponse,
    PatientRegisterRequest,
    PatientResponse,
    PatientSearchResponse,
)
from app.services.orchestrator import (
    add_insurance_policy,
    delete_patient,
    get_insurance_policies,
    get_patient,
    register_patient,
    search_patients,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Group 1 — Patient Registry
# ---------------------------------------------------------------------------

@router.post(
    "/patients",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new patient (FR-01 / 1.1)",
)
@limiter.limit("10/minute")
async def register(
    body: PatientRegisterRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Register a new patient in this tenant.

    Returns 201 with the full patient object including the auto-generated
    patient_number. Returns 422 if national_id is already registered.
    """
    return await register_patient(body, request)


@router.post(
    "/patients/register",
    response_model=PatientResponse,
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,   # Legacy alias — hidden from docs
)
@limiter.limit("10/minute")
async def register_legacy(
    body: PatientRegisterRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Legacy endpoint kept for backward compat. Prefer POST /patients."""
    return await register_patient(body, request)


@router.get(
    "/patients",
    response_model=PatientSearchResponse,
    summary="Search / list patients (FR-02 / 1.2)",
)
@limiter.limit("30/minute")
async def list_patients(
    request: Request,
    search: Optional[str] = Query(None, description="Free-text search across name, phone, national_id"),
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(20, ge=1, le=100, description="Records per page"),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """List/search patients with pagination.

    Pass `search` to filter by name, phone number, or national_id.
    Returns paginated results with total count.
    """
    return await search_patients(request, search=search, page=page, page_size=page_size)


@router.get(
    "/patients/search",
    response_model=PatientSearchResponse,
    include_in_schema=False,   # Legacy alias
)
@limiter.limit("30/minute")
async def search_legacy(
    request: Request,
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await search_patients(request, search=search, page=page, page_size=page_size)


@router.get(
    "/patients/{patient_id}",
    response_model=PatientResponse,
    summary="Get patient by ID (1.3)",
)
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


# ---------------------------------------------------------------------------
# Group 2 — Patient Insurance
# ---------------------------------------------------------------------------

@router.post(
    "/patients/{patient_id}/insurance",
    response_model=InsurancePolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add insurance policy to patient (FR-04 / 2.1)",
)
@limiter.limit("10/minute")
async def add_insurance(
    patient_id: str,
    body: InsurancePolicyCreateRequest,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """Add an insurance policy to a registered patient.

    The policy starts with `verification_status = pending`.
    Use PATCH /insurance/{insurance_id}/verify to record the outcome.
    """
    return await add_insurance_policy(patient_id, body, request)


@router.get(
    "/patients/{patient_id}/insurance",
    response_model=list[InsurancePolicyResponse],
    summary="List patient insurance policies (2.3)",
)
@limiter.limit("30/minute")
async def list_insurance(
    patient_id: str,
    request: Request,
    _ctx: TenantContext = Depends(get_current_tenant),
):
    """List all insurance policies held by a patient, newest first."""
    return await get_insurance_policies(patient_id, request)
