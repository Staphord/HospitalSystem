from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    EnterReportRequest,
    ImagingRequestDetailResponse,
    ImagingRequestListResponse,
    PerformImagingRequest,
    RadiologyReportCreate,
    RadiologyReportListResponse,
    RadiologyReportResponse,
    RadiologyReportUpdate,
    ScheduleImagingRequest,
)
from app.core.limiter import limiter
from app.core.security import TokenPayload, _extract_roles, get_current_active_user, require_role
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db
from app.services import radiology as radiology_service

router = APIRouter(dependencies=[Depends(get_current_tenant)])


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


# ── Imaging request worklist (FR-28) ────────────────────────────────────────────

@router.get(
    "/requests",
    response_model=ImagingRequestListResponse,
    tags=["Requests"],
    dependencies=[Depends(require_any_role(["radiographer", "doctor", "hospital_admin"]))],
)
async def list_imaging_requests(
    status_filter: str | None = Query(None, alias="status"),
    urgency: str | None = Query(None),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
):
    items, total = await radiology_service.list_imaging_requests(
        db,
        status=status_filter,
        urgency=urgency,
        search=search,
        skip=skip,
        limit=limit,
    )
    return ImagingRequestListResponse(requests=items, total=total)


@router.get(
    "/requests/{request_id}",
    response_model=ImagingRequestDetailResponse,
    tags=["Requests"],
    dependencies=[Depends(require_any_role(["radiographer", "doctor", "hospital_admin"]))],
)
async def get_imaging_request(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    data = await radiology_service.get_imaging_request_detail(db, request_id)
    report = data.pop("report", None)
    return ImagingRequestDetailResponse(
        **data,
        report=RadiologyReportResponse.model_validate(report) if report else None,
    )


# ── Workflow (FR-29, FR-30, FR-31) ──────────────────────────────────────────────

@router.post(
    "/requests/{request_id}/schedule",
    response_model=RadiologyReportResponse,
    tags=["Workflow"],
)
@limiter.limit("30/minute")
async def schedule_imaging(
    request: Request,
    request_id: UUID,
    body: ScheduleImagingRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: TokenPayload = Depends(require_role("radiographer")),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return await radiology_service.schedule_imaging(
        db,
        request_id,
        body.model_dump(),
        user,
        tenant_id=ctx.tenant_id,
    )


@router.post(
    "/requests/{request_id}/perform",
    response_model=RadiologyReportResponse,
    tags=["Workflow"],
)
@limiter.limit("30/minute")
async def perform_imaging(
    request: Request,
    request_id: UUID,
    body: PerformImagingRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: TokenPayload = Depends(require_role("radiographer")),
):
    return await radiology_service.perform_imaging(db, request_id, body.model_dump(exclude_unset=True), user)


@router.put(
    "/requests/{request_id}/report",
    response_model=RadiologyReportResponse,
    tags=["Workflow"],
)
@limiter.limit("30/minute")
async def enter_report(
    request: Request,
    request_id: UUID,
    body: EnterReportRequest,
    db: AsyncSession = Depends(get_tenant_db),
    user: TokenPayload = Depends(require_role("radiographer")),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return await radiology_service.enter_report(
        db,
        request_id,
        body.model_dump(),
        user,
        tenant_id=ctx.tenant_id,
    )


@router.get(
    "/requests/{request_id}/report",
    response_model=RadiologyReportResponse,
    tags=["Workflow"],
    dependencies=[Depends(require_any_role(["radiographer", "doctor", "hospital_admin"]))],
)
async def get_request_report(
    request_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    detail = await radiology_service.get_imaging_request_detail(db, request_id)
    report = detail.get("report")
    if not report:
        raise HTTPException(status_code=404, detail="No radiology report for this request")
    return report


@router.patch(
    "/reports/{report_id}/verify",
    response_model=RadiologyReportResponse,
    tags=["Workflow"],
)
@limiter.limit("30/minute")
async def verify_report(
    request: Request,
    report_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    user: TokenPayload = Depends(require_role("radiographer")),
    ctx: TenantContext = Depends(get_current_tenant),
):
    return await radiology_service.verify_report(db, report_id, user, tenant_id=ctx.tenant_id)


# ── Report CRUD ─────────────────────────────────────────────────────────────────

@router.post("/reports", response_model=RadiologyReportResponse, status_code=201, tags=["Reports"])
@limiter.limit("30/minute")
async def create_report(
    request: Request,
    body: RadiologyReportCreate,
    db: AsyncSession = Depends(get_tenant_db),
    _user: TokenPayload = Depends(require_role("radiographer")),
):
    return await radiology_service.create_report(db, body.model_dump())


@router.get(
    "/reports/{report_id}",
    response_model=RadiologyReportResponse,
    tags=["Reports"],
    dependencies=[Depends(require_any_role(["radiographer", "doctor", "hospital_admin"]))],
)
async def get_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    return await radiology_service.get_report(db, report_id)


@router.get(
    "/reports",
    response_model=RadiologyReportListResponse,
    tags=["Reports"],
    dependencies=[Depends(require_any_role(["radiographer", "doctor", "hospital_admin"]))],
)
async def list_reports(
    patient_id: UUID | None = Query(None),
    visit_id: UUID | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
):
    reports, total = await radiology_service.list_reports(
        db,
        patient_id=patient_id,
        visit_id=visit_id,
        status=status,
        skip=skip,
        limit=limit,
    )
    return RadiologyReportListResponse(reports=reports, total=total)


@router.put("/reports/{report_id}", response_model=RadiologyReportResponse, tags=["Reports"])
async def update_report(
    report_id: UUID,
    body: RadiologyReportUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    _user: TokenPayload = Depends(require_role("radiographer")),
):
    return await radiology_service.update_report(db, report_id, body.model_dump(exclude_unset=True))


@router.delete("/reports/{report_id}", status_code=204, tags=["Reports"])
async def delete_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    _user: TokenPayload = Depends(require_role("radiographer")),
):
    await radiology_service.delete_report(db, report_id)
