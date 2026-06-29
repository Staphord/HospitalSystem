from uuid import UUID
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    RadiologyReportCreate,
    RadiologyReportListResponse,
    RadiologyReportResponse,
    RadiologyReportUpdate,
)
from app.core.limiter import limiter
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.dependencies import get_tenant_db
from app.services import radiology as radiology_service

router = APIRouter(dependencies=[Depends(get_current_tenant)])


@router.post("/reports", response_model=RadiologyReportResponse, status_code=201)
@limiter.limit("30/minute")
async def create_report(
    request: Request,
    body: RadiologyReportCreate,
    db: AsyncSession = Depends(get_tenant_db),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    report = await radiology_service.create_report(db, body.model_dump())
    return report


@router.get("/reports/{report_id}", response_model=RadiologyReportResponse)
async def get_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await radiology_service.get_report(db, report_id)


@router.get("/reports", response_model=RadiologyReportListResponse)
async def list_reports(
    patient_id: UUID | None = Query(None),
    visit_id: UUID | None = Query(None),
    status: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_tenant_db),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    reports, total = await radiology_service.list_reports(
        db, patient_id=patient_id, visit_id=visit_id,
        status=status, skip=skip, limit=limit,
    )
    return RadiologyReportListResponse(reports=reports, total=total)


@router.put("/reports/{report_id}", response_model=RadiologyReportResponse)
async def update_report(
    report_id: UUID,
    body: RadiologyReportUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    return await radiology_service.update_report(db, report_id, body.model_dump(exclude_unset=True))


@router.delete("/reports/{report_id}", status_code=204)
async def delete_report(
    report_id: UUID,
    db: AsyncSession = Depends(get_tenant_db),
    _ctx: TenantContext = Depends(get_current_tenant),
):
    await radiology_service.delete_report(db, report_id)