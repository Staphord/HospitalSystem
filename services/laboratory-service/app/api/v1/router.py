from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    LabQueueResponse,
    LabQueueItem,
    LabRequestDetailResponse,
    SpecimenCreateRequest,
    SpecimenResponse,
    SpecimenStatusRequest,
    SpecimenRejectRequest,
    ResultCreateRequest,
    ResultUpdateRequest,
    ResultResponse,
    PatientResultsResponse,
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


# Helper to check for either role (e.g., doctor OR lab_technician)
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


# ── Queue & Worklist ────────────────────────────────────────────────────────────

@router.get(
    "/queue",
    response_model=LabQueueResponse,
    tags=["Queue"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_queue(
    status: Literal["waiting", "in_progress"] = Query("waiting"),
    queue_date: date = Query(default_factory=date.today, alias="date"),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabQueueResponse:
    items = await lab_service.get_lab_queue(db, status, queue_date)
    return LabQueueResponse(date=queue_date, queue=items)


@router.post(
    "/queue/{queue_id}/call",
    response_model=LabQueueItem,
    tags=["Queue"],
)
async def call_queue_patient(
    queue_id: UUID,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabQueueItem:
    await lab_service.call_queue_patient(db, queue_id, user)
    item_dict = await lab_service.get_queue_item_by_id(db, queue_id)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Queue item details not found")
    return LabQueueItem(**item_dict)


@router.post(
    "/queue/{queue_id}/skip",
    response_model=LabQueueItem,
    tags=["Queue"],
)
async def skip_queue_patient(
    queue_id: UUID,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> LabQueueItem:
    await lab_service.skip_queue_patient(db, queue_id, user)
    item_dict = await lab_service.get_queue_item_by_id(db, queue_id)
    if not item_dict:
        raise HTTPException(status_code=404, detail="Queue item details not found")
    return LabQueueItem(**item_dict)


# ── Request Details ─────────────────────────────────────────────────────────────

@router.get(
    "/requests/{request_id}",
    response_model=LabRequestDetailResponse,
    tags=["Requests"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_request_detail(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> LabRequestDetailResponse:
    data = await lab_service.get_request_detail(db, request_id)
    # Wrap in response structure
    return LabRequestDetailResponse(
        request_id=data["request_id"],
        test_name=data["test_name"],
        request_type=data["request_type"],
        clinical_indication=data["clinical_indication"],
        urgency=data["urgency"],
        requested_by=data["requested_by"],
        requested_at=data["requested_at"],
        status=data["status"],
        patient=data["patient"],
        visit=data["visit"],
    )


# ── Specimen Management ────────────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/specimen",
    response_model=SpecimenResponse,
    status_code=201,
    tags=["Specimens"],
)
async def collect_specimen(
    request_id: UUID,
    body: SpecimenCreateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenResponse:
    specimen = await lab_service.collect_specimen(db, request_id, body, user)
    return specimen


@router.patch(
    "/specimens/{specimen_id}/receive",
    response_model=SpecimenResponse,
    tags=["Specimens"],
)
async def receive_specimen(
    specimen_id: UUID,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenResponse:
    specimen = await lab_service.receive_specimen(db, specimen_id, user)
    return specimen


@router.patch(
    "/specimens/{specimen_id}/status",
    response_model=SpecimenResponse,
    tags=["Specimens"],
)
async def update_specimen_status(
    specimen_id: UUID,
    body: SpecimenStatusRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenResponse:
    specimen = await lab_service.update_specimen_status(db, specimen_id, body.status, user)
    return specimen


@router.patch(
    "/specimens/{specimen_id}/reject",
    response_model=SpecimenResponse,
    tags=["Specimens"],
)
async def reject_specimen(
    specimen_id: UUID,
    body: SpecimenRejectRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> SpecimenResponse:
    specimen = await lab_service.reject_specimen(db, specimen_id, body.rejection_reason, user)
    return specimen


# ── Results Entry ──────────────────────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/results",
    response_model=ResultResponse,
    status_code=201,
    tags=["Results"],
)
async def create_lab_result(
    request_id: UUID,
    body: ResultCreateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultResponse:
    result = await lab_service.create_lab_result(db, request_id, body, user)
    return result


@router.patch(
    "/results/{result_id}",
    response_model=ResultResponse,
    tags=["Results"],
)
async def update_lab_result(
    result_id: UUID,
    body: ResultUpdateRequest,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultResponse:
    result = await lab_service.update_lab_result(db, result_id, body, user)
    return result


@router.get(
    "/results/{result_id}",
    response_model=ResultResponse,
    tags=["Results"],
    dependencies=[Depends(require_role("lab_technician"))],
)
async def get_lab_result(
    result_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultResponse:
    result = await lab_service.get_lab_result(db, result_id)
    return result


@router.patch(
    "/results/{result_id}/verify",
    response_model=ResultResponse,
    tags=["Results"],
)
async def verify_lab_result(
    result_id: UUID,
    user: TokenPayload = Depends(require_role("lab_technician")),
    db: AsyncSession = Depends(get_tenant_db),
) -> ResultResponse:
    result = await lab_service.verify_lab_result(db, result_id, user)
    return result


# ── Patient Results History ───────────────────────────────────────────────────

@router.get(
    "/patients/{patient_id}/results",
    response_model=PatientResultsResponse,
    tags=["History"],
)
async def get_patient_results_history(
    patient_id: UUID,
    user: TokenPayload = Depends(require_any_role(["doctor", "lab_technician"])),
    db: AsyncSession = Depends(get_tenant_db),
) -> PatientResultsResponse:
    results = await lab_service.get_patient_results(db, patient_id)
    return PatientResultsResponse(patient_id=patient_id, results=results)
