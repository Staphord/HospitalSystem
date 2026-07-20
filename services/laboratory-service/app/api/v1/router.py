from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    LabRequestsListResponse,
    LabRequestDetailResponse,
    SpecimenCreateRequest,
    SpecimenCreateResponse,
    SpecimenStatusUpdateRequest,
    SpecimenUpdateResponse,
    SpecimenListResponse,
    AllSpecimensResponse,
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
from app.core.security import TokenPayload, require_role, get_current_active_user
from app.core.tenant_auth import get_current_tenant
from app.dependencies import get_tenant_db
from app.services import laboratory as lab_service

router = APIRouter(
    dependencies=[
        Depends(get_current_tenant),
    ],
)


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
    date: Optional[date] = Query(None, description="Filter by request date"),
    session: AsyncSession = Depends(get_tenant_db),
):
    items = await lab_service.get_lab_requests(
        session,
        status=status,
        urgency=urgency,
        date_filter=date,
    )
    return LabRequestsListResponse(requests=items)


@router.get(
    "/requests/{request_id}",
    response_model=LabRequestDetailResponse,
    tags=["Request Queue"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_request(
    request_id: UUID,
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.get_lab_request_detail(session, request_id)


# ── Group 2 — Specimen Tracking ──────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/specimen",
    response_model=SpecimenCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Specimen Tracking"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def collect_specimen(
    request_id: UUID,
    payload: SpecimenCreateRequest,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.collect_specimen(session, request_id, payload, user=user)


@router.patch(
    "/requests/{request_id}/specimen",
    response_model=SpecimenUpdateResponse,
    tags=["Specimen Tracking"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def update_specimen_status(
    request_id: UUID,
    payload: SpecimenStatusUpdateRequest,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.update_specimen_status(session, request_id, payload, user=user)


@router.get(
    "/requests/{request_id}/specimen",
    response_model=SpecimenListResponse,
    tags=["Specimen Tracking"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def list_request_specimens(
    request_id: UUID,
    session: AsyncSession = Depends(get_tenant_db),
):
    items = await lab_service.get_specimens_for_request(session, request_id)
    return SpecimenListResponse(specimens=items)


@router.get(
    "/specimens",
    response_model=AllSpecimensResponse,
    tags=["Specimen Tracking"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def list_all_specimens(
    session: AsyncSession = Depends(get_tenant_db),
):
    items = await lab_service.get_all_tracked_specimens(session)
    return AllSpecimensResponse(specimens=items)



# ── Group 3 — Results Entry ──────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/result",
    response_model=ResultCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Results Entry"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def create_result(
    request_id: UUID,
    payload: ResultCreateRequest,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.create_lab_result(session, request_id, payload, user=user)


@router.patch(
    "/requests/{request_id}/result",
    response_model=ResultUpdateResponse,
    tags=["Results Entry"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def update_result(
    request_id: UUID,
    payload: ResultUpdateRequest,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.update_lab_result(session, request_id, payload, user=user)


@router.get(
    "/requests/{request_id}/result",
    response_model=ResultDetailResponse,
    tags=["Results Entry"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_result(
    request_id: UUID,
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.get_lab_result_by_request(session, request_id)


# ── Group 4 — Result Verification ────────────────────────────────────────────

@router.post(
    "/results/{result_id}/verify",
    response_model=ResultVerifyResponse,
    tags=["Result Verification"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def verify_result(
    result_id: UUID,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.verify_lab_result(session, result_id, user=user)


# ── Group 5 — Billing ─────────────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/bill",
    response_model=LabBillResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Billing"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def create_lab_bill(
    request_id: UUID,
    payload: LabBillCreateRequest,
    user: TokenPayload = Depends(get_current_active_user),
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.create_lab_bill(session, request_id, payload, user=user)


# ── Group 6 — Doctor View ────────────────────────────────────────────────────

@router.get(
    "/visits/{visit_id}/results",
    response_model=DoctorVisitResultsResponse,
    tags=["Doctor View"],
    dependencies=[Depends(require_role("doctor"))],
)
async def get_visit_verified_results(
    visit_id: UUID,
    session: AsyncSession = Depends(get_tenant_db),
):
    return await lab_service.get_visit_verified_results(session, visit_id)
