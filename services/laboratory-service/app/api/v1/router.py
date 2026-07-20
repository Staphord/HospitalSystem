from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    LabRequestsListResponse,
    LabRequestDetailResponse,
    SpecimenCreateRequest,
    SpecimenCreateResponse,
    SpecimenStatusUpdateRequest,
    SpecimenUpdateResponse,
    SpecimenListResponse,
    ResultCreateRequest,
    ResultCreateResponse,
    ResultUpdateRequest,
    ResultUpdateResponse,
    ResultDetailResponse,
    ResultVerifyResponse,
    LabBillCreateRequest,
    LabBillResponse,
    DoctorVisitResultsResponse,
)
from app.core.security import TokenPayload, require_role, get_current_active_user, _extract_roles
from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db
from app.services import laboratory as lab_service

router = APIRouter(
    dependencies=[
        Depends(get_current_tenant),
    ],
)


def require_any_role(allowed: list[str]):
    async def _dependency(user: TokenPayload = Depends(get_current_active_user)) -> TokenPayload:
        roles = _extract_roles(user)
        if not (any(r in roles for r in allowed) or "super_admin" in roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user
    return _dependency


# ── Group 1 — Request Queue ───────────────────────────────────────────────────

@router.get(
    "/requests",
    response_model=LabRequestsListResponse,
    tags=["Request Queue"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def list_lab_requests(
    status: Optional[str] = Query(None, description="Filter by status"),
    urgency: Optional[str] = Query(None, description="Filter by urgency"),
    date_param: Optional[date] = Query(None, alias="date", description="Filter by requested_at date"),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabRequestsListResponse:
    requests_list = await lab_service.get_lab_requests(db, status=status, urgency=urgency, date_filter=date_param)
    return LabRequestsListResponse(requests=requests_list)


@router.get(
    "/requests/{request_id}",
    response_model=LabRequestDetailResponse,
    tags=["Request Queue"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_request_detail(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> LabRequestDetailResponse:
    data = await lab_service.get_lab_request_detail(db, request_id)
    return LabRequestDetailResponse(**data)


# ── Group 2 — Specimen Tracking ───────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/specimen",
    response_model=SpecimenCreateResponse,
    status_code=201,
    tags=["Specimen Tracking"],
)
async def collect_specimen(
    request_id: UUID,
    body: SpecimenCreateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenCreateResponse:
    specimen = await lab_service.collect_specimen(db, request_id, body, user)
    return specimen


@router.patch(
    "/requests/{request_id}/specimen",
    response_model=SpecimenUpdateResponse,
    tags=["Specimen Tracking"],
)
async def update_specimen_status(
    request_id: UUID,
    body: SpecimenStatusUpdateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenUpdateResponse:
    data = await lab_service.update_specimen_status(db, request_id, body, user)
    return SpecimenUpdateResponse(**data)


@router.get(
    "/requests/{request_id}/specimen",
    response_model=SpecimenListResponse,
    tags=["Specimen Tracking"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_request_specimens(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenListResponse:
    specimens = await lab_service.get_specimens_for_request(db, request_id)
    return SpecimenListResponse(specimens=specimens)


# ── Group 3 — Results Entry ───────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/result",
    response_model=ResultCreateResponse,
    status_code=201,
    tags=["Results Entry"],
)
async def create_lab_result(
    request_id: UUID,
    body: ResultCreateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultCreateResponse:
    result = await lab_service.create_lab_result(db, request_id, body, user)
    return result


@router.patch(
    "/requests/{request_id}/result",
    response_model=ResultUpdateResponse,
    tags=["Results Entry"],
)
async def update_lab_result(
    request_id: UUID,
    body: ResultUpdateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultUpdateResponse:
    data = await lab_service.update_lab_result(db, request_id, body, user)
    return ResultUpdateResponse(**data)


@router.get(
    "/requests/{request_id}/result",
    response_model=ResultDetailResponse,
    tags=["Results Entry"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_result_by_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultDetailResponse:
    data = await lab_service.get_lab_result_by_request(db, request_id)
    return ResultDetailResponse(**data)


# ── Group 4 — Result Verification ─────────────────────────────────────────────

@router.post(
    "/results/{result_id}/verify",
    response_model=ResultVerifyResponse,
    tags=["Result Verification"],
)
async def verify_lab_result(
    result_id: UUID,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultVerifyResponse:
    data = await lab_service.verify_lab_result(db, result_id, user)
    return ResultVerifyResponse(**data)


# ── Group 5 — Billing ─────────────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/bill",
    response_model=LabBillResponse,
    status_code=201,
    tags=["Billing"],
)
async def create_lab_bill(
    request_id: UUID,
    body: LabBillCreateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabBillResponse:
    data = await lab_service.create_lab_bill(db, request_id, body, user)
    return LabBillResponse(**data)


# ── Group 6 — Doctor-Facing Result Read ───────────────────────────────────────

@router.get(
    "/visits/{visit_id}/results",
    response_model=DoctorVisitResultsResponse,
    tags=["Doctor View"],
)
async def get_visit_verified_results(
    visit_id: UUID,
    user: TokenPayload = Depends(require_any_role(["doctor", "lab_technician"])),
    db: AsyncSession = Depends(get_tenant_db),
) -> DoctorVisitResultsResponse:
    data = await lab_service.get_visit_verified_results(db, visit_id)
    return DoctorVisitResultsResponse(**data)
