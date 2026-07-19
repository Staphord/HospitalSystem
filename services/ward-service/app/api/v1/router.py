"""Ward API routes (FR-47–FR-52).

TEMPORARY: JWT / role checks disabled for local testing.
Tenant DB is resolved from header ``X-Tenant-ID`` or ``DEV_TENANT_ID`` /
``DEFAULT_HOSPITAL_ID`` (compose default should be your real tenant, e.g. hosp-ac224699).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.schemas import (
    AdmissionCreate,
    AdmissionOut,
    BedAssignRequest,
    BedOut,
    DischargeRequest,
    HandoverCreate,
    HandoverOut,
    LosOut,
    NursingNoteCreate,
    NursingNoteOut,
    OrderCreate,
    OrderOut,
    OrderUpdate,
    VisitorCreate,
    VisitorOut,
)
from app.dependencies import get_tenant_db, resolve_tenant_id
from app.events.publisher import publish_patient_admitted, publish_patient_discharged
from app.services import ward as ward_svc

router = APIRouter()

_DEV_ACTOR = "dev-unauthenticated"


# ── Beds (FR-47) ──────────────────────────────────────────────────────────────


@router.get("/beds", response_model=list[BedOut], tags=["Beds"])
async def list_beds(
    ward_name: str | None = None,
    bed_type: str | None = None,
    is_available: bool | None = None,
    is_active: bool | None = True,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[BedOut]:
    rows = await ward_svc.list_beds(
        db, ward_name=ward_name, bed_type=bed_type, is_available=is_available, is_active=is_active
    )
    return [BedOut.model_validate(r) for r in rows]


@router.get("/beds/board", tags=["Beds"])
async def beds_board(
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
):
    return await ward_svc.beds_board(db)


@router.post("/beds/{bed_id}/assign", response_model=BedOut, tags=["Beds"])
async def assign_bed(
    bed_id: UUID,
    body: BedAssignRequest | None = None,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> BedOut:
    admission_id = body.admission_id if body else None
    bed = await ward_svc.assign_bed(db, bed_id, admission_id=admission_id)
    return BedOut.model_validate(bed)


@router.post("/beds/{bed_id}/release", response_model=BedOut, tags=["Beds"])
async def release_bed(
    bed_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> BedOut:
    bed = await ward_svc.release_bed(db, bed_id)
    return BedOut.model_validate(bed)


# ── Admissions (FR-48 / FR-51 / FR-52) ─────────────────────────────────────────


@router.post("/admissions", response_model=AdmissionOut, status_code=201, tags=["Admissions"])
async def create_admission(
    body: AdmissionCreate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> AdmissionOut:
    adm = await ward_svc.create_admission(
        db,
        visit_id=body.visit_id,
        bed_id=body.bed_id,
        admitting_diagnosis=body.admitting_diagnosis,
        doctor_sub=_DEV_ACTOR,
        tenant_id=tenant_id,
        require_disposition=False,
    )
    await publish_patient_admitted(
        admission_id=str(adm.admission_id),
        patient_id=str(adm.patient_id),
        tenant_id=tenant_id,
        bed_id=str(adm.bed_id),
    )
    return AdmissionOut.model_validate(adm)


@router.get("/admissions", response_model=list[AdmissionOut], tags=["Admissions"])
async def list_admissions(
    status: str | None = None,
    patient_id: UUID | None = None,
    ward_name: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[AdmissionOut]:
    rows = await ward_svc.list_admissions(
        db,
        status_filter=status,
        patient_id=patient_id,
        ward_name=ward_name,
        limit=limit,
        offset=offset,
    )
    return [AdmissionOut.model_validate(r) for r in rows]


@router.get("/admissions/{admission_id}", response_model=AdmissionOut, tags=["Admissions"])
async def get_admission(
    admission_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> AdmissionOut:
    return AdmissionOut.model_validate(await ward_svc.get_admission(db, admission_id))


@router.get("/admissions/{admission_id}/los", response_model=LosOut, tags=["Admissions"])
async def get_los(
    admission_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> LosOut:
    return LosOut(**await ward_svc.get_los(db, admission_id))


@router.post("/admissions/{admission_id}/discharge", response_model=AdmissionOut, tags=["Admissions"])
async def discharge_admission(
    admission_id: UUID,
    body: DischargeRequest,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> AdmissionOut:
    adm = await ward_svc.discharge_admission(
        db,
        admission_id,
        discharge_diagnosis=body.discharge_diagnosis,
        discharge_instructions=body.discharge_instructions,
        doctor_sub=_DEV_ACTOR,
    )
    await publish_patient_discharged(
        admission_id=str(adm.admission_id),
        patient_id=str(adm.patient_id),
        tenant_id=tenant_id,
        discharge_date=adm.discharge_date,
        length_of_stay_days=float(adm.length_of_stay_days or 0),
        visit_id=str(adm.visit_id),
    )
    return AdmissionOut.model_validate(adm)


# ── Orders (FR-49) ────────────────────────────────────────────────────────────


@router.post(
    "/admissions/{admission_id}/orders",
    response_model=OrderOut,
    status_code=201,
    tags=["Orders"],
)
async def create_order(
    admission_id: UUID,
    body: OrderCreate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrderOut:
    row = await ward_svc.create_order(db, admission_id, body.model_dump(), _DEV_ACTOR)
    return OrderOut.model_validate(row)


@router.get("/admissions/{admission_id}/orders", response_model=list[OrderOut], tags=["Orders"])
async def list_orders(
    admission_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[OrderOut]:
    return [OrderOut.model_validate(r) for r in await ward_svc.list_orders(db, admission_id)]


@router.patch(
    "/admissions/{admission_id}/orders/{order_id}",
    response_model=OrderOut,
    tags=["Orders"],
)
async def update_order(
    admission_id: UUID,
    order_id: UUID,
    body: OrderUpdate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> OrderOut:
    row = await ward_svc.update_order(
        db, admission_id, order_id, body.model_dump(exclude_unset=True)
    )
    return OrderOut.model_validate(row)


# ── Nursing notes (FR-50) ─────────────────────────────────────────────────────


@router.post(
    "/admissions/{admission_id}/nursing-notes",
    response_model=NursingNoteOut,
    status_code=201,
    tags=["Nursing Notes"],
)
async def create_nursing_note(
    admission_id: UUID,
    body: NursingNoteCreate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> NursingNoteOut:
    row = await ward_svc.create_nursing_note(db, admission_id, body.model_dump(), _DEV_ACTOR)
    return NursingNoteOut.model_validate(row)


@router.get(
    "/admissions/{admission_id}/nursing-notes",
    response_model=list[NursingNoteOut],
    tags=["Nursing Notes"],
)
async def list_nursing_notes(
    admission_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[NursingNoteOut]:
    return [
        NursingNoteOut.model_validate(r)
        for r in await ward_svc.list_nursing_notes(db, admission_id)
    ]


def _visitor_out(row) -> VisitorOut:
    data = VisitorOut.model_validate(row).model_dump()
    data["time_left_seconds"] = ward_svc._visitor_time_left(row)
    return VisitorOut.model_validate(data)


# ── Visitors ──────────────────────────────────────────────────────────────────


@router.get("/visitors", response_model=list[VisitorOut], tags=["Visitors"])
async def list_visitors(
    status: str | None = None,
    active_only: bool = False,
    limit: int = Query(200, ge=1, le=500),
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[VisitorOut]:
    rows = await ward_svc.list_visitors(
        db, status=status, active_only=active_only, limit=limit
    )
    return [_visitor_out(r) for r in rows]


@router.get("/visitors/active", response_model=list[VisitorOut], tags=["Visitors"])
async def list_active_visitors(
    limit: int = Query(200, ge=1, le=500),
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[VisitorOut]:
    rows = await ward_svc.list_visitors(db, active_only=True, limit=limit)
    return [_visitor_out(r) for r in rows]


@router.post("/visitors", response_model=VisitorOut, status_code=201, tags=["Visitors"])
async def create_visitor(
    body: VisitorCreate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> VisitorOut:
    row = await ward_svc.create_visitor(db, body.model_dump(), approved_by=_DEV_ACTOR)
    return _visitor_out(row)


@router.post("/visitors/{visitor_id}/checkout", response_model=VisitorOut, tags=["Visitors"])
async def checkout_visitor(
    visitor_id: UUID,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> VisitorOut:
    row = await ward_svc.checkout_visitor(db, visitor_id)
    return _visitor_out(row)


# ── Shift handovers ───────────────────────────────────────────────────────────


@router.get("/handovers", response_model=list[HandoverOut], tags=["Handovers"])
async def list_handovers(
    limit: int = Query(50, ge=1, le=200),
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> list[HandoverOut]:
    return [
        HandoverOut.model_validate(r) for r in await ward_svc.list_handovers(db, limit=limit)
    ]


@router.post("/handovers", response_model=HandoverOut, status_code=201, tags=["Handovers"])
async def create_handover(
    body: HandoverCreate,
    tenant_id: str = Depends(resolve_tenant_id),
    db: AsyncSession = Depends(get_tenant_db),
) -> HandoverOut:
    row = await ward_svc.create_handover(db, body.model_dump(), submitted_by=_DEV_ACTOR)
    return HandoverOut.model_validate(row)
