import uuid
import logging
import httpx
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_auth import get_current_tenant, TenantContext
from app.dependencies import get_tenant_db
from app.core.config import settings

from app.api.v1.schemas import (
    ConsultationCreate,
    ConsultationResponse,
    DiagnosisCreate,
    DiagnosisResponse,
    DispositionRequest,
    DispositionResponse,
    InvestigationRequestCreate,
    InvestigationRequestResponse,
    EncounterViewResponse,
    PatientHistoryResponse,
    PatientResponse,
    TriageAssessmentResponse,
    VisitHistoryItem,
)
from app.models.consultation import (
    Consultation,
    Diagnosis,
    InvestigationRequest,
    Patient,
    Visit,
    TriageAssessment,
)

logger = logging.getLogger("consultation_service.api")
router = APIRouter(dependencies=[Depends(get_current_tenant)])
bearer_scheme = HTTPBearer(auto_error=False)

async def _transition_visit_status(
    visit_id: str,
    new_status: str,
    auth_header: str,
    tenant_db_url: str | None = None
) -> None:
    """Helper to transition visit status in visit-service."""
    url = f"{settings.visit_service_url}/api/v1/visits/{visit_id}/status"
    headers = {"Authorization": auth_header}
    if tenant_db_url:
        headers["X-Tenant-DB"] = tenant_db_url
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.patch(url, json={"status": new_status}, headers=headers)
            if resp.status_code >= 400:
                logger.error("Failed to update visit status: %s - %s", resp.status_code, resp.text)
        except Exception as exc:
            logger.exception("HTTP error communicating with visit-service: %s", exc)

@router.post("/encounters", response_model=ConsultationResponse, status_code=status.HTTP_201_CREATED)
async def create_consultation(
    request: Request,
    body: ConsultationCreate,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    # Verify visit exists
    visit_stmt = select(Visit).where(Visit.visit_id == body.visit_id)
    visit_res = await db.execute(visit_stmt)
    visit = visit_res.scalars().first()
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    # Check if consultation already exists
    stmt = select(Consultation).where(Consultation.visit_id == body.visit_id)
    res = await db.execute(stmt)
    db_consultation = res.scalars().first()

    if db_consultation:
        db_consultation.history_of_presenting_illness = body.history_of_presenting_illness
        db_consultation.examination_findings = body.examination_findings
        db_consultation.clinical_impression = body.clinical_impression
        db_consultation.created_by = ctx.user_sub
        db_consultation.updated_at = datetime.utcnow()
    else:
        db_consultation = Consultation(
            id=uuid.uuid4(),
            visit_id=body.visit_id,
            patient_id=body.patient_id,
            history_of_presenting_illness=body.history_of_presenting_illness,
            examination_findings=body.examination_findings,
            clinical_impression=body.clinical_impression,
            created_by=ctx.user_sub,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(db_consultation)

    await db.commit()
    await db.refresh(db_consultation)

    # Transition visit to in_consultation
    if credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(body.visit_id), "in_consultation", auth_header, tenant_db_url)

    return db_consultation

@router.get("/encounters/visit/{visit_id}", response_model=ConsultationResponse)
async def get_consultation_by_visit(
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    stmt = select(Consultation).where(Consultation.visit_id == visit_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    return consultation

@router.post("/encounters/{consultation_id}/diagnoses", response_model=DiagnosisResponse, status_code=status.HTTP_201_CREATED)
async def add_diagnosis(
    consultation_id: uuid.UUID,
    body: DiagnosisCreate,
    db: AsyncSession = Depends(get_tenant_db),
):
    # Verify consultation exists
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if body.diagnosis_type not in ("provisional", "differential", "final"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="diagnosis_type must be 'provisional', 'differential', or 'final'",
        )

    db_diagnosis = Diagnosis(
        id=uuid.uuid4(),
        consultation_id=consultation_id,
        diagnosis_type=body.diagnosis_type,
        code=body.code,
        description=body.description,
        created_at=datetime.utcnow(),
    )
    db.add(db_diagnosis)
    await db.commit()
    await db.refresh(db_diagnosis)
    return db_diagnosis


@router.post(
    "/encounters/{consultation_id}/disposition",
    response_model=DispositionResponse,
    tags=["Disposition"],
)
async def set_disposition(
    consultation_id: uuid.UUID,
    body: DispositionRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
):
    """FR-21 discharge/admission decision; FR-18 requires a final diagnosis first."""
    allowed = {"outpatient", "admission", "referral", "deceased"}
    if body.disposition not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"disposition must be one of {sorted(allowed)}",
        )

    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if body.disposition in ("admission", "outpatient"):
        diag_stmt = select(Diagnosis).where(
            Diagnosis.consultation_id == consultation_id,
            Diagnosis.diagnosis_type == "final",
        )
        diag_res = await db.execute(diag_stmt)
        if diag_res.scalars().first() is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A final diagnosis is required before an admission or outpatient discharge decision (FR-18).",
            )

    consultation.disposition = body.disposition
    consultation.disposition_notes = body.notes
    consultation.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(consultation)
    return consultation


@router.post("/encounters/{consultation_id}/investigations", response_model=list[InvestigationRequestResponse], status_code=status.HTTP_201_CREATED)
async def raise_investigations(
    request: Request,
    consultation_id: uuid.UUID,
    body: list[InvestigationRequestCreate],
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Request body cannot be empty")

    # Verify consultation exists
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    # Enforce: Provisional diagnosis must exist before investigations are raised
    diag_stmt = select(Diagnosis).where(
        Diagnosis.consultation_id == consultation_id,
        Diagnosis.diagnosis_type == "provisional",
    )
    diag_res = await db.execute(diag_stmt)
    has_provisional = diag_res.scalars().first() is not None
    if not has_provisional:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A provisional diagnosis must be recorded before requesting investigations."
        )

    requests = []
    has_lab = False
    for item in body:
        if item.request_type not in ("laboratory", "radiology"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="request_type must be either 'laboratory' or 'radiology'"
            )
        if item.request_type == "laboratory":
            has_lab = True

        db_req = InvestigationRequest(
            id=uuid.uuid4(),
            consultation_id=consultation_id,
            visit_id=consultation.visit_id,
            patient_id=consultation.patient_id,
            request_type=item.request_type,
            test_name=item.test_name,
            clinical_history=item.clinical_history,
            status="pending",
            created_by=ctx.user_sub,
            created_at=datetime.utcnow(),
        )
        db.add(db_req)
        requests.append(db_req)

    await db.commit()

    # Trigger events or transition visit status if lab tests requested
    if has_lab and credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(consultation.visit_id), "in_lab", auth_header, tenant_db_url)

    # Refresh instances
    for r in requests:
        await db.refresh(r)

    return requests

@router.get("/encounters/patient/{patient_id}/encounter-view/{visit_id}", response_model=EncounterViewResponse)
async def get_patient_encounter_view(
    patient_id: uuid.UUID,
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    # Fetch Patient record
    pat_stmt = select(Patient).where(Patient.id == patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # Fetch Triage Summary & vitals
    triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    triage_res = await db.execute(triage_stmt)
    triage_summary = triage_res.scalars().first()

    # Fetch Consultation (including clinical notes, provisional/differential diagnoses)
    cons_stmt = select(Consultation).where(Consultation.visit_id == visit_id)
    cons_res = await db.execute(cons_stmt)
    consultation = cons_res.scalars().first()

    return EncounterViewResponse(
        patient=patient,
        current_visit_id=visit_id,
        triage_summary=triage_summary,
        consultation=consultation,
    )

@router.get("/encounters/patient/{patient_id}/history", response_model=PatientHistoryResponse)
async def get_patient_history(
    patient_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    # Fetch Patient record
    pat_stmt = select(Patient).where(Patient.id == patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # Fetch all visits for the patient, ordered by date descending
    visit_stmt = select(Visit).where(Visit.patient_id == patient_id).order_by(Visit.visit_date.desc(), Visit.created_at.desc())
    visit_res = await db.execute(visit_stmt)
    visits = visit_res.scalars().all()

    previous_visits = []
    for visit in visits:
        # Fetch triage
        triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit.visit_id)
        triage_res = await db.execute(triage_stmt)
        triage = triage_res.scalars().first()

        # Fetch consultation
        cons_stmt = select(Consultation).where(Consultation.visit_id == visit.visit_id)
        cons_res = await db.execute(cons_stmt)
        consultation = cons_res.scalars().first()

        previous_visits.append(
            VisitHistoryItem(
                visit_id=visit.visit_id,
                visit_date=visit.visit_date,
                visit_type=visit.visit_type,
                status=visit.status,
                triage_summary=triage,
                consultation=consultation,
            )
        )

    return PatientHistoryResponse(patient=patient, previous_visits=previous_visits)
