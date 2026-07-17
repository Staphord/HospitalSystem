import uuid
import logging
import httpx
import datetime
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select, case, func, cast, Date, or_, and_
from sqlalchemy.dialects.postgresql import UUID as pgUUID
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant_auth import get_current_tenant, TenantContext
from app.dependencies import get_tenant_db
from app.core.config import settings
from app.core.security import get_current_active_user, TokenPayload, _extract_roles, require_role

from app.api.v1.schemas import (
    ConsultationCreate,
    ConsultationResponse,
    DiagnosisCreate,
    DiagnosisResponse,
    DiagnosisUpdateRequest,
    InvestigationRequestCreate,
    InvestigationRequestResponse,
    PrescriptionCreate,
    PrescriptionResponse,
    NotesUpdateRequest,
    DispositionUpdateRequest,
    ConsultationCompleteResponse,
    QueueItemResponse,
    EncounterViewResponse,
    PatientHistoryResponse,
    PatientResponse,
    TriageAssessmentResponse,
    VisitHistoryItem,
    EncounterOpenResponse,
)
from app.models.consultation import (
    Consultation,
    Diagnosis,
    InvestigationRequest,
    Patient,
    Visit,
    TriageAssessment,
    Prescription,
    Queue,
    Bill,
    BillItem,
    FeeSchedule,
    LabResult,
    RadiologyReport,
)

logger = logging.getLogger("consultation_service.api")
router = APIRouter(dependencies=[Depends(get_current_tenant)])
bearer_scheme = HTTPBearer(auto_error=False)


# Helper to check any role from the token
def require_any_role(roles_list: List[str]):
    async def _dependency(user: TokenPayload = Depends(get_current_active_user)) -> TokenPayload:
        user_roles = _extract_roles(user)
        has_allowed = any(r in user_roles for r in roles_list) or "super_admin" in user_roles
        if not has_allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user
    return _dependency


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


async def _generate_queue_number(db: AsyncSession, queue_type: str) -> str:
    """Generate queue number with prefix (e.g. L-001 or R-001)."""
    prefix = "L" if queue_type == "lab" else "R" if queue_type == "radiology" else "P"
    today = datetime.date.today()
    stmt = select(func.count(Queue.queue_id)).where(
        Queue.queue_type == queue_type,
        cast(Queue.created_at, Date) == today
    )
    res = await db.execute(stmt)
    count = res.scalar() or 0
    return f"{prefix}-{count + 1:03d}"


# 1. GET /consultations/queue
@router.get("/queue", response_model=List[QueueItemResponse], tags=["Doctor Queue"])
async def get_doctor_queue(
    status: Optional[str] = Query("waiting"),
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve active doctor queue today ordered by triage priority and arrival time."""
    today = datetime.date.today()
    status_list = [s.strip() for s in status.split(",") if s.strip()] if status else ["waiting"]
    priority_case = case(
        (Queue.priority == "emergency", 1),
        (Queue.priority == "urgent", 2),
        (Queue.priority == "semi_urgent", 3),
        (Queue.priority == "non_urgent", 4),
        else_=5
    )
    stmt = (
        select(Queue, Visit, Patient, TriageAssessment)
        .join(Visit, Queue.visit_id == Visit.visit_id)
        .join(Patient, Patient.id == cast(Queue.patient_id, pgUUID))
        .outerjoin(TriageAssessment, Queue.visit_id == TriageAssessment.visit_id)
        .where(
            Queue.queue_type == "doctor",
            Queue.status.in_(status_list)
        )
        .order_by(priority_case, Queue.created_at.asc())
    )
    res = await db.execute(stmt)
    rows = res.all()

    queue_list = []
    now = datetime.datetime.utcnow()
    for q, v, p, t in rows:
        age = today.year - p.date_of_birth.year - ((today.month, today.day) < (p.date_of_birth.month, p.date_of_birth.day))
        q_created_naive = q.created_at.replace(tzinfo=None)
        end_time = q.called_at or q.completed_at or now
        end_time_naive = end_time.replace(tzinfo=None)
        wait_time = int((end_time_naive - q_created_naive).total_seconds() / 60)
        
        # Calculate pending and completed investigations
        inv_stmt = select(InvestigationRequest).where(InvestigationRequest.visit_id == q.visit_id)
        inv_res = await db.execute(inv_stmt)
        invs = inv_res.scalars().all()
        pending_count = len([i for i in invs if i.status not in ("completed", "cancelled")])
        completed_count = len([i for i in invs if i.status == "completed"])

        queue_list.append(QueueItemResponse(
            queue_id=q.queue_id,
            queue_number=q.queue_number,
            priority=q.priority,
            visit_id=q.visit_id,
            visit_number=v.visit_number,
            patient_id=q.patient_id,
            full_name=p.full_name,
            patient_number=p.patient_number,
            age=age,
            triage_category=t.triage_category if t else None,
            chief_complaint=t.chief_complaint if t else None,
            wait_time_minutes=max(0, wait_time),
            queue_status=q.status,
            visit_status=v.status,
            pending_investigations_count=pending_count,
            completed_investigations_count=completed_count
        ))
    return queue_list


# 2. POST /consultations/encounters/{visit_id}/open
@router.post("/encounters/{visit_id}/open", response_model=EncounterOpenResponse, status_code=status.HTTP_201_CREATED, tags=["Consultation Encounters"])
async def open_encounter(
    visit_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Open and start a consultation for a patient visit."""
    # Validate visit exists
    visit_stmt = select(Visit).where(Visit.visit_id == visit_id)
    visit_res = await db.execute(visit_stmt)
    visit = visit_res.scalars().first()
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    # Verify visit is triaged
    if visit.status != "triaged":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Visit status must be 'triaged' to open encounter"
        )

    # Verify no existing in_progress consultation
    exist_stmt = select(Consultation).where(
        Consultation.visit_id == visit_id,
        Consultation.consultation_status == "in_progress"
    )
    exist_res = await db.execute(exist_stmt)
    if exist_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An in-progress consultation already exists for this visit"
        )

    # Fetch patient
    pat_stmt = select(Patient).where(Patient.id == visit.patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    # Fetch triage summary
    triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    triage_res = await db.execute(triage_stmt)
    triage = triage_res.scalars().first()

    # Fetch last 5 completed visits
    hist_stmt = (
        select(Visit)
        .where(Visit.patient_id == visit.patient_id, Visit.status == "completed")
        .order_by(Visit.visit_date.desc(), Visit.created_at.desc())
        .limit(5)
    )
    hist_res = await db.execute(hist_stmt)
    hist_visits = hist_res.scalars().all()

    visit_history = []
    for hv in hist_visits:
        hv_triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == hv.visit_id)
        hv_triage_res = await db.execute(hv_triage_stmt)
        hv_triage = hv_triage_res.scalars().first()

        hv_cons_stmt = select(Consultation).where(Consultation.visit_id == hv.visit_id)
        hv_cons_res = await db.execute(hv_cons_stmt)
        hv_cons = hv_cons_res.scalars().first()

        visit_history.append(VisitHistoryItem(
            visit_id=hv.visit_id,
            visit_date=hv.visit_date,
            visit_type=hv.visit_type,
            status=hv.status,
            triage_summary=hv_triage,
            consultation=hv_cons
        ))

    # Create consultation
    consultation = Consultation(
        id=uuid.uuid4(),
        visit_id=visit_id,
        patient_id=visit.patient_id,
        consultation_status="in_progress",
        started_at=datetime.datetime.utcnow(),
        created_by=ctx.user_sub,
    )
    db.add(consultation)

    # Update visit status in DB directly
    visit.status = "in_consultation"
    visit.updated_at = datetime.datetime.utcnow()

    # Call visit-service HTTP PATCH to transition status & fire events
    if credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(visit_id), "in_consultation", auth_header, tenant_db_url)

    # Update doctor queue entry in DB
    queue_stmt = select(Queue).where(
        Queue.visit_id == visit_id,
        Queue.queue_type == "doctor",
        Queue.status == "waiting"
    )
    q_res = await db.execute(queue_stmt)
    q_entry = q_res.scalars().first()
    if q_entry:
        q_entry.status = "in_progress"
        q_entry.called_at = datetime.datetime.utcnow()

    # Billing integration
    fee_stmt = select(FeeSchedule).where(
        FeeSchedule.item_type == "consultation",
        FeeSchedule.is_active == True,
        FeeSchedule.effective_from <= datetime.date.today(),
        (FeeSchedule.effective_to == None) | (FeeSchedule.effective_to >= datetime.date.today())
    )
    fee_res = await db.execute(fee_stmt)
    fee = fee_res.scalars().first()
    price = 0.0
    if fee:
        price = fee.insurance_price if visit.payment_type == "insurance" else fee.standard_price

    # Open bill if not exist
    bill_stmt = select(Bill).where(Bill.visit_id == visit_id)
    bill_res = await db.execute(bill_stmt)
    bill = bill_res.scalars().first()
    if not bill:
        bill = Bill(
            bill_id=uuid.uuid4(),
            visit_id=visit_id,
            patient_id=visit.patient_id,
            total_amount=0.0,
            status="open",
            created_at=datetime.datetime.utcnow(),
        )
        db.add(bill)
        await db.flush()

    # Insert Consultation Fee BillItem
    bill_item = BillItem(
        bill_item_id=uuid.uuid4(),
        bill_id=bill.bill_id,
        item_type="consultation",
        description="Consultation Fee",
        quantity=1,
        unit_price=price,
        total_price=price,
        reference_id=consultation.id,
        created_at=datetime.datetime.utcnow(),
    )
    db.add(bill_item)
    bill.total_amount += price

    await db.commit()
    await db.refresh(consultation)
    if bill:
        await db.refresh(bill)

    return EncounterOpenResponse(
        consultation_id=consultation.id,
        visit_id=visit_id,
        patient=patient,
        triage_summary=triage,
        visit_history=visit_history,
        bills_bill_id=bill.bill_id if bill else None,
        consultation_status=consultation.consultation_status,
        started_at=consultation.started_at,
    )


# 3. GET /consultations/encounters/{visit_id}
@router.get("/encounters/{visit_id}", response_model=EncounterViewResponse, tags=["Consultation Encounters"])
async def get_encounter(
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve full encounter view for an open or completed consultation."""
    # Fetch consultation
    cons_stmt = select(Consultation).where(Consultation.visit_id == visit_id)
    cons_res = await db.execute(cons_stmt)
    consultation = cons_res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    # Fetch patient
    pat_stmt = select(Patient).where(Patient.id == consultation.patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()

    # Fetch triage
    triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    triage_res = await db.execute(triage_stmt)
    triage_summary = triage_res.scalars().first()

    # Fetch diagnoses
    diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation.id).order_by(Diagnosis.diagnosis_type, Diagnosis.recorded_at)
    diag_res = await db.execute(diag_stmt)
    diagnoses = diag_res.scalars().all()

    # Fetch investigations with result details
    inv_stmt = select(InvestigationRequest).where(
        InvestigationRequest.consultation_id == consultation.id,
        InvestigationRequest.status != "cancelled"
    )
    inv_res = await db.execute(inv_stmt)
    investigations = inv_res.scalars().all()

    inv_list = []
    for inv in investigations:
        result_data = None
        if inv.request_type == "laboratory" or inv.request_type == "lab":
            lab_stmt = select(LabResult).where(LabResult.request_id == inv.id)
            lab_res = await db.execute(lab_stmt)
            lab = lab_res.scalars().first()
            if lab:
                result_data = {
                    "result_id": str(lab.result_id),
                    "result_value": lab.result_value,
                    "unit": lab.unit,
                    "reference_range": lab.reference_range,
                    "is_critical": lab.is_critical,
                    "result_notes": lab.result_notes,
                    "status": lab.status,
                    "resulted_at": lab.resulted_at.isoformat() if lab.resulted_at else None
                }
        elif inv.request_type == "radiology":
            rad_stmt = select(RadiologyReport).where(RadiologyReport.request_id == inv.id)
            rad_res = await db.execute(rad_stmt)
            rad = rad_res.scalars().first()
            if rad:
                result_data = {
                    "report_id": str(rad.report_id),
                    "modality": rad.modality,
                    "body_part": rad.body_part,
                    "findings": rad.findings,
                    "impression": rad.impression,
                    "status": rad.status,
                    "reported_at": rad.reported_at.isoformat() if rad.reported_at else None
                }

        inv_list.append(InvestigationRequestResponse(
            id=inv.id,
            visit_id=inv.visit_id,
            consultation_id=inv.consultation_id,
            patient_id=inv.patient_id,
            request_type=inv.request_type,
            test_name=inv.test_name,
            test_code=inv.test_code,
            clinical_history=inv.clinical_history,
            status=inv.status,
            urgency=inv.urgency,
            created_by=inv.requested_by,
            created_at=inv.requested_at,
            result=result_data
        ))

    # Fetch prescriptions
    presc_stmt = select(Prescription).where(Prescription.consultation_id == consultation.id)
    presc_res = await db.execute(presc_stmt)
    prescriptions = presc_res.scalars().all()

    presc_list = [
        PrescriptionResponse(
            id=p.id,
            visit_id=p.visit_id,
            consultation_id=p.consultation_id,
            patient_id=p.patient_id,
            drug_name=p.drug_name,
            dose=p.dose,
            frequency=p.frequency,
            duration=p.duration,
            route=p.route,
            instructions=p.instructions,
            prescribed_by=p.prescribed_by,
            status=p.status,
            prescribed_at=p.prescribed_at
        ) for p in prescriptions
    ]

    # Fetch last 5 completed visits history
    hist_stmt = (
        select(Visit)
        .where(Visit.patient_id == consultation.patient_id, Visit.status == "completed")
        .order_by(Visit.visit_date.desc(), Visit.created_at.desc())
        .limit(5)
    )
    hist_res = await db.execute(hist_stmt)
    hist_visits = hist_res.scalars().all()

    visit_history = []
    for hv in hist_visits:
        hv_triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == hv.visit_id)
        hv_triage_res = await db.execute(hv_triage_stmt)
        hv_triage = hv_triage_res.scalars().first()

        hv_cons_stmt = select(Consultation).where(Consultation.visit_id == hv.visit_id)
        hv_cons_res = await db.execute(hv_cons_stmt)
        hv_cons = hv_cons_res.scalars().first()

        visit_history.append(VisitHistoryItem(
            visit_id=hv.visit_id,
            visit_date=hv.visit_date,
            visit_type=hv.visit_type,
            status=hv.status,
            triage_summary=hv_triage,
            consultation=hv_cons
        ))

    # Construct complete ConsultationResponse
    cons_response = ConsultationResponse(
        id=consultation.id,
        visit_id=consultation.visit_id,
        patient_id=consultation.patient_id,
        history_of_presenting_illness=consultation.history_of_presenting_illness,
        examination_findings=consultation.examination_findings,
        clinical_impression=consultation.clinical_impression,
        consultation_status=consultation.consultation_status,
        started_at=consultation.started_at,
        completed_at=consultation.completed_at,
        disposition=consultation.disposition,
        referral_type=consultation.referral_type,
        referral_notes=consultation.referral_notes,
        created_by=consultation.created_by,
        created_at=consultation.created_at,
        updated_at=consultation.updated_at,
        diagnoses=diagnoses,
        investigation_requests=inv_list,
        prescriptions=presc_list
    )

    return EncounterViewResponse(
        patient=patient,
        current_visit_id=visit_id,
        triage_summary=triage_summary,
        consultation=cons_response,
    )


# 4. PUT /consultations/{consultation_id}/notes
@router.put("/{consultation_id}/notes", response_model=ConsultationResponse, tags=["Clinical Notes"])
async def update_clinical_notes(
    consultation_id: uuid.UUID,
    body: NotesUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Write or update narrative clinical notes for a consultation."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.created_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned doctor can edit this consultation"
        )

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update notes for a completed consultation"
        )

    consultation.history_of_presenting_illness = body.presenting_history
    consultation.examination_findings = body.examination_findings
    consultation.clinical_impression = body.clinical_impression
    consultation.updated_at = datetime.datetime.utcnow()

    await db.commit()
    await db.refresh(consultation)
    return consultation


# 5. POST /consultations/{consultation_id}/diagnoses
@router.post("/{consultation_id}/diagnoses", response_model=DiagnosisResponse, status_code=status.HTTP_201_CREATED, tags=["Diagnoses"])
async def record_diagnosis(
    consultation_id: uuid.UUID,
    body: DiagnosisCreate,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Record provisional, differential, or final diagnosis."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add diagnosis to a completed consultation"
        )

    # Sequence order required for differential
    if body.diagnosis_type == "differential" and body.sequence_order is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sequence_order is required for differential diagnoses"
        )

    # Provisional diagnosis check (limit 1)
    if body.diagnosis_type == "provisional":
        prov_stmt = select(Diagnosis).where(
            Diagnosis.consultation_id == consultation_id,
            Diagnosis.diagnosis_type == "provisional"
        )
        prov_res = await db.execute(prov_stmt)
        if prov_res.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A provisional diagnosis already exists. Use PUT to update it."
            )

    # Final diagnosis check (investigation gate)
    if body.diagnosis_type == "final":
        inv_stmt = select(InvestigationRequest).where(InvestigationRequest.consultation_id == consultation_id)
        inv_res = await db.execute(inv_stmt)
        invs = inv_res.scalars().all()
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled")]
        if pending_invs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pending investigation results must be completed before final diagnosis can be recorded."
            )

    db_diagnosis = Diagnosis(
        id=uuid.uuid4(),
        consultation_id=consultation_id,
        diagnosis_type=body.diagnosis_type,
        code=body.code,
        description=body.description,
        sequence_order=body.sequence_order,
        recorded_by=ctx.user_sub,
        recorded_at=datetime.datetime.utcnow(),
    )
    db.add(db_diagnosis)
    await db.commit()
    await db.refresh(db_diagnosis)
    return db_diagnosis


# 6. PUT /consultations/{consultation_id}/diagnoses/{diagnosis_id}
@router.put("/{consultation_id}/diagnoses/{diagnosis_id}", response_model=DiagnosisResponse, tags=["Diagnoses"])
async def update_diagnosis(
    consultation_id: uuid.UUID,
    diagnosis_id: uuid.UUID,
    body: DiagnosisUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Update existing diagnosis entry."""
    cons_stmt = select(Consultation).where(Consultation.id == consultation_id)
    cons_res = await db.execute(cons_stmt)
    consultation = cons_res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot edit diagnosis on a completed consultation"
        )

    diag_stmt = select(Diagnosis).where(Diagnosis.id == diagnosis_id, Diagnosis.consultation_id == consultation_id)
    diag_res = await db.execute(diag_stmt)
    diagnosis = diag_res.scalars().first()
    if not diagnosis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis entry not found")

    if diagnosis.recorded_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the doctor who recorded the diagnosis can update it"
        )

    # Investigation gate check for final diagnosis
    if diagnosis.diagnosis_type == "final":
        inv_stmt = select(InvestigationRequest).where(InvestigationRequest.consultation_id == consultation_id)
        inv_res = await db.execute(inv_stmt)
        invs = inv_res.scalars().all()
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled")]
        if pending_invs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Pending investigation results must be completed before final diagnosis can be recorded."
            )

    diagnosis.code = body.icd10_code
    diagnosis.description = body.diagnosis_text
    diagnosis.sequence_order = body.sequence_order

    await db.commit()
    await db.refresh(diagnosis)
    return diagnosis


# 7. GET /consultations/{consultation_id}/diagnoses
@router.get("/{consultation_id}/diagnoses", response_model=List[DiagnosisResponse], tags=["Diagnoses"])
async def get_diagnoses(
    consultation_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all diagnoses for a consultation."""
    stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation_id).order_by(Diagnosis.diagnosis_type, Diagnosis.recorded_at)
    res = await db.execute(stmt)
    return res.scalars().all()


# 7b. DELETE /diagnoses/{diagnosis_id}
@router.delete("/diagnoses/{diagnosis_id}", tags=["Diagnoses"])
async def delete_diagnosis(
    diagnosis_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Delete a diagnosis entry."""
    stmt = select(Diagnosis).where(Diagnosis.id == diagnosis_id)
    res = await db.execute(stmt)
    diagnosis = res.scalars().first()
    if not diagnosis:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Diagnosis entry not found")

    await db.delete(diagnosis)
    await db.commit()
    return {"message": "Diagnosis deleted successfully"}


# 8. POST /consultations/{consultation_id}/investigations
@router.post("/{consultation_id}/investigations", response_model=InvestigationRequestResponse, status_code=status.HTTP_201_CREATED, tags=["Investigations"])
async def raise_investigation(
    consultation_id: uuid.UUID,
    body: InvestigationRequestCreate,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Raise a laboratory or radiology investigation request."""
    # Fetch consultation
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot request investigations for a completed consultation"
        )

    # Fetch visit
    visit_stmt = select(Visit).where(Visit.visit_id == consultation.visit_id)
    visit_res = await db.execute(visit_stmt)
    visit = visit_res.scalars().first()

    # Enforce: Provisional diagnosis must exist before investigations are raised
    diag_stmt = select(Diagnosis).where(
        Diagnosis.consultation_id == consultation_id,
        Diagnosis.diagnosis_type == "provisional",
    )
    diag_res = await db.execute(diag_stmt)
    if not diag_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A provisional diagnosis must be recorded before requesting investigations."
        )

    # Insert request
    inv_req = InvestigationRequest(
        id=uuid.uuid4(),
        consultation_id=consultation_id,
        visit_id=consultation.visit_id,
        patient_id=consultation.patient_id,
        request_type=body.request_type,
        test_name=body.test_name,
        test_code=body.test_code,
        clinical_history=body.clinical_indication,
        status="pending",
        urgency=body.urgency,
        requested_by=ctx.user_sub,
        requested_at=datetime.datetime.utcnow(),
    )
    db.add(inv_req)

    # Update visit status
    visit.status = "awaiting_results"
    visit.updated_at = datetime.datetime.utcnow()

    # HTTP transition to visit-service (optional check/event fire)
    if credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(consultation.visit_id), "awaiting_results", auth_header, tenant_db_url)

    # Create queues entry
    q_type = "lab" if body.request_type.lower() == "lab" or body.request_type.lower() == "laboratory" else "radiology"
    q_num = await _generate_queue_number(db, q_type)
    queue_entry = Queue(
        queue_id=uuid.uuid4(),
        visit_id=consultation.visit_id,
        patient_id=consultation.patient_id,
        queue_type=q_type,
        queue_number=q_num,
        priority=body.urgency,
        status="waiting",
        created_at=datetime.datetime.utcnow()
    )
    db.add(queue_entry)

    # Billing schedule lookup
    fee_query = select(FeeSchedule).where(
        (FeeSchedule.item_type == ("lab" if q_type == "lab" else "radiology")),
        ((FeeSchedule.item_code == body.test_code) | (FeeSchedule.item_name.ilike(body.test_name))),
        (FeeSchedule.is_active == True)
    )
    fee_res = await db.execute(fee_query)
    fee = fee_res.scalars().first()
    price = 0.0
    if fee:
        price = fee.insurance_price if visit.payment_type == "insurance" else fee.standard_price

    # Open bill if not exist
    bill_stmt = select(Bill).where(Bill.visit_id == consultation.visit_id)
    bill_res = await db.execute(bill_stmt)
    bill = bill_res.scalars().first()
    if not bill:
        bill = Bill(
            bill_id=uuid.uuid4(),
            visit_id=consultation.visit_id,
            patient_id=consultation.patient_id,
            total_amount=0.0,
            status="open",
            created_at=datetime.datetime.utcnow()
        )
        db.add(bill)
        await db.flush()

    bill_item = BillItem(
        bill_item_id=uuid.uuid4(),
        bill_id=bill.bill_id,
        item_type=("lab" if q_type == "lab" else "radiology"),
        description=f"Investigation: {body.test_name}",
        quantity=1,
        unit_price=price,
        total_price=price,
        reference_id=inv_req.id,
        created_at=datetime.datetime.utcnow()
    )
    db.add(bill_item)
    bill.total_amount += price

    await db.commit()
    await db.refresh(inv_req)
    return inv_req


# 9. GET /consultations/{consultation_id}/investigations
@router.get("/{consultation_id}/investigations", response_model=List[InvestigationRequestResponse], tags=["Investigations"])
async def get_investigations(
    consultation_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all investigations and their results for a consultation."""
    stmt = select(InvestigationRequest).where(
        InvestigationRequest.consultation_id == consultation_id,
        InvestigationRequest.status != "cancelled"
    )
    res = await db.execute(stmt)
    investigations = res.scalars().all()

    inv_list = []
    for inv in investigations:
        result_data = None
        if inv.request_type == "laboratory" or inv.request_type == "lab":
            lab_stmt = select(LabResult).where(LabResult.request_id == inv.id)
            lab_res = await db.execute(lab_stmt)
            lab = lab_res.scalars().first()
            if lab:
                result_data = {
                    "result_id": str(lab.result_id),
                    "result_value": lab.result_value,
                    "unit": lab.unit,
                    "reference_range": lab.reference_range,
                    "is_critical": lab.is_critical,
                    "result_notes": lab.result_notes,
                    "status": lab.status,
                    "resulted_at": lab.resulted_at.isoformat() if lab.resulted_at else None
                }
        elif inv.request_type == "radiology":
            rad_stmt = select(RadiologyReport).where(RadiologyReport.request_id == inv.id)
            rad_res = await db.execute(rad_stmt)
            rad = rad_res.scalars().first()
            if rad:
                result_data = {
                    "report_id": str(rad.report_id),
                    "modality": rad.modality,
                    "body_part": rad.body_part,
                    "findings": rad.findings,
                    "impression": rad.impression,
                    "status": rad.status,
                    "reported_at": rad.reported_at.isoformat() if rad.reported_at else None
                }

        inv_list.append(InvestigationRequestResponse(
            id=inv.id,
            visit_id=inv.visit_id,
            consultation_id=inv.consultation_id,
            patient_id=inv.patient_id,
            request_type=inv.request_type,
            test_name=inv.test_name,
            test_code=inv.test_code,
            clinical_history=inv.clinical_history,
            status=inv.status,
            urgency=inv.urgency,
            created_by=inv.requested_by,
            created_at=inv.requested_at,
            result=result_data
        ))
    return inv_list


# 10. DELETE /investigations/{request_id}
@router.delete("/investigations/{request_id}", tags=["Investigations"])
async def cancel_investigation(
    request_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Cancel an investigation request before it is processed."""
    stmt = select(InvestigationRequest).where(
        InvestigationRequest.id == request_id
    )
    res = await db.execute(stmt)
    inv = res.scalars().first()
    if not inv:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investigation request not found")

    if inv.requested_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the doctor who raised the request can cancel it"
        )

    if inv.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel investigation that is already in progress or completed"
        )

    inv.status = "cancelled"

    # Set queues entry status to skipped
    q_stmt = select(Queue).where(
        Queue.visit_id == inv.visit_id,
        Queue.queue_type == ("lab" if inv.request_type == "laboratory" or inv.request_type == "lab" else "radiology"),
        Queue.status == "waiting"
    )
    q_res = await db.execute(q_stmt)
    q_entry = q_res.scalars().first()
    if q_entry:
        q_entry.status = "skipped"
        q_entry.completed_at = datetime.datetime.utcnow()

    # Re-evaluate visit status if no pending results exist
    all_stmt = select(InvestigationRequest).where(InvestigationRequest.consultation_id == inv.consultation_id)
    all_res = await db.execute(all_stmt)
    all_invs = all_res.scalars().all()
    remaining_pending = [i for i in all_invs if i.id != request_id and i.status not in ("completed", "cancelled")]

    # Fetch visit
    v_stmt = select(Visit).where(Visit.visit_id == inv.visit_id)
    v_res = await db.execute(v_stmt)
    visit = v_res.scalars().first()

    if not remaining_pending and visit:
        visit.status = "in_consultation"
        visit.updated_at = datetime.datetime.utcnow()
        if credentials:
            auth_header = f"Bearer {credentials.credentials}"
            tenant_db_url = request.headers.get("x-tenant-db")
            await _transition_visit_status(str(visit.visit_id), "in_consultation", auth_header, tenant_db_url)

    await db.commit()
    return {"request_id": request_id, "status": "cancelled"}


# 11. POST /consultations/{consultation_id}/prescriptions
@router.post("/{consultation_id}/prescriptions", response_model=PrescriptionResponse, status_code=status.HTTP_201_CREATED, tags=["Prescriptions"])
async def record_prescription(
    consultation_id: uuid.UUID,
    body: PrescriptionCreate,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Issue a prescription item."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot prescribe drugs for a completed consultation"
        )

    # Insert Prescription
    prescription = Prescription(
        id=uuid.uuid4(),
        visit_id=consultation.visit_id,
        consultation_id=consultation_id,
        patient_id=consultation.patient_id,
        drug_name=body.drug_name,
        dose=body.dose,
        frequency=body.frequency,
        duration=body.duration,
        route=body.route,
        instructions=body.instructions,
        prescribed_by=ctx.user_sub,
        status="pending",
        prescribed_at=datetime.datetime.utcnow(),
    )
    db.add(prescription)

    # Enqueue pharmacy queue (only if not already created)
    pq_stmt = select(Queue).where(
        Queue.visit_id == consultation.visit_id,
        Queue.queue_type == "pharmacy"
    )
    pq_res = await db.execute(pq_stmt)
    if not pq_res.scalars().first():
        q_num = await _generate_queue_number(db, "pharmacy")
        pharmacy_queue = Queue(
            queue_id=uuid.uuid4(),
            visit_id=consultation.visit_id,
            patient_id=consultation.patient_id,
            queue_type="pharmacy",
            queue_number=q_num,
            priority="routine",
            status="waiting",
            created_at=datetime.datetime.utcnow()
        )
        db.add(pharmacy_queue)

    await db.commit()
    await db.refresh(prescription)
    return prescription


# 12. GET /consultations/{consultation_id}/prescriptions
@router.get("/{consultation_id}/prescriptions", response_model=List[PrescriptionResponse], tags=["Prescriptions"])
async def get_prescriptions(
    consultation_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all prescriptions issued in a consultation."""
    stmt = select(Prescription).where(Prescription.consultation_id == consultation_id)
    res = await db.execute(stmt)
    return res.scalars().all()


# 13. DELETE /prescriptions/{prescription_id}
@router.delete("/prescriptions/{prescription_id}", tags=["Prescriptions"])
async def cancel_prescription(
    prescription_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Cancel a prescription item before it is dispensed."""
    stmt = select(Prescription).where(
        Prescription.id == prescription_id
    )
    res = await db.execute(stmt)
    prescription = res.scalars().first()
    if not prescription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prescription not found")

    if prescription.prescribed_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the doctor who issued the prescription can cancel it"
        )

    if prescription.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot cancel prescription that is already dispensed or cancelled"
        )

    prescription.status = "cancelled"
    await db.commit()
    return {"prescription_id": prescription_id, "status": "cancelled"}


# 14. PUT /consultations/{consultation_id}/disposition
@router.put("/{consultation_id}/disposition", response_model=ConsultationResponse, tags=["Disposition"])
async def update_disposition(
    consultation_id: uuid.UUID,
    body: DispositionUpdateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Record disposition (discharge, admission, referral)."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.created_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned doctor can set disposition"
        )

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update disposition on completed consultation"
        )

    # Verify at least one diagnosis exists
    diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation_id)
    diag_res = await db.execute(diag_stmt)
    if not diag_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one diagnosis must be recorded before recording disposition"
        )

    # Investigation gate check — return_visit is allowed even with pending investigations
    if body.disposition != "return_visit":
        inv_stmt = select(InvestigationRequest).where(InvestigationRequest.consultation_id == consultation_id)
        inv_res = await db.execute(inv_stmt)
        invs = inv_res.scalars().all()
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled")]
        if pending_invs:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All investigations must be completed or cancelled before disposition"
            )

    # Per-disposition required-field validation
    valid_dispositions = {"outpatient", "admission", "referral", "return_visit", "deceased"}
    if body.disposition not in valid_dispositions:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid disposition value. Must be one of: {', '.join(sorted(valid_dispositions))}"
        )

    if body.disposition == "admission":
        if not body.admission_reason or not body.admission_reason.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Admission reason is required for admission disposition"
            )

    if body.disposition == "referral":
        errors = []
        if not body.referral_type or not body.referral_type.strip():
            errors.append("Department/Specialty is required")
        if not body.referral_notes or not body.referral_notes.strip():
            errors.append("Referral reason is required")
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="; ".join(errors)
            )

    if body.disposition == "return_visit":
        errors = []
        if not body.return_date:
            errors.append("Return date is required")
        if not body.return_reason or not body.return_reason.strip():
            errors.append("Return reason is required")
        if errors:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="; ".join(errors)
            )

    # Persist disposition and all detail fields
    consultation.disposition = body.disposition
    consultation.referral_type = body.referral_type
    consultation.referral_notes = body.referral_notes
    consultation.admission_reason = body.admission_reason
    consultation.discharge_instructions = body.discharge_instructions
    consultation.return_reason = body.return_reason
    consultation.updated_at = datetime.datetime.utcnow()

    # Parse date strings into date objects
    if body.follow_up_date:
        try:
            consultation.follow_up_date = datetime.datetime.strptime(body.follow_up_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="follow_up_date must be in YYYY-MM-DD format")
    else:
        consultation.follow_up_date = None

    if body.return_date:
        try:
            consultation.return_date = datetime.datetime.strptime(body.return_date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="return_date must be in YYYY-MM-DD format")
    else:
        consultation.return_date = None

    await db.commit()
    await db.refresh(consultation)
    return consultation


# 15. POST /consultations/{consultation_id}/complete
@router.post("/{consultation_id}/complete", response_model=ConsultationCompleteResponse, tags=["Encounter Completion"])
async def complete_consultation(
    consultation_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Finalize and close the consultation, triggering downstream flows."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    if consultation.created_by != ctx.user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned doctor can complete this consultation"
        )

    if consultation.consultation_status != "in_progress":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Consultation is already completed"
        )

    # 1. Diagnosis existence gate (any diagnosis type)
    diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation_id)
    diag_res = await db.execute(diag_stmt)
    if not diag_res.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one diagnosis must exist to complete consultation"
        )

    # 2. Investigation gate
    inv_stmt = select(InvestigationRequest).where(InvestigationRequest.consultation_id == consultation_id)
    inv_res = await db.execute(inv_stmt)
    invs = inv_res.scalars().all()
    pending_invs = [i for i in invs if i.status not in ("completed", "cancelled")]
    if pending_invs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Outstanding investigations must be resolved before completing consultation"
        )

    # 3. Disposition gate
    if not consultation.disposition:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Patient disposition must be recorded before completing consultation"
        )

    # Update consultation status
    consultation.consultation_status = "completed"
    consultation.completed_at = datetime.datetime.utcnow()

    # Determine visit target status
    target_visit_status = "completed"
    if consultation.disposition == "admission":
        target_visit_status = "admitted" # Ward handles from here

    # Update visit status in DB directly
    v_stmt = select(Visit).where(Visit.visit_id == consultation.visit_id)
    v_res = await db.execute(v_stmt)
    visit = v_res.scalars().first()
    if visit:
        visit.status = target_visit_status
        visit.updated_at = datetime.datetime.utcnow()

    # HTTP patch to visit-service to publish events
    if credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(consultation.visit_id), target_visit_status, auth_header, tenant_db_url)

    # Complete doctor queue entry in DB
    q_stmt = select(Queue).where(
        Queue.visit_id == consultation.visit_id,
        Queue.queue_type == "doctor",
        Queue.status == "in_progress"
    )
    q_res = await db.execute(q_stmt)
    q_entry = q_res.scalars().first()
    if q_entry:
        q_entry.status = "completed"
        q_entry.completed_at = datetime.datetime.utcnow()

    # If outpatient with pending prescriptions, make sure pharmacy queue entry exists
    if consultation.disposition == "outpatient":
        presc_stmt = select(Prescription).where(
            Prescription.consultation_id == consultation_id,
            Prescription.status == "pending"
        )
        presc_res = await db.execute(presc_stmt)
        has_pending_presc = presc_res.scalars().first() is not None
        if has_pending_presc:
            pharm_stmt = select(Queue).where(
                Queue.visit_id == consultation.visit_id,
                Queue.queue_type == "pharmacy"
            )
            pharm_res = await db.execute(pharm_stmt)
            if not pharm_res.scalars().first():
                q_num = await _generate_queue_number(db, "pharmacy")
                pharmacy_queue = Queue(
                    queue_id=uuid.uuid4(),
                    visit_id=consultation.visit_id,
                    patient_id=consultation.patient_id,
                    queue_type="pharmacy",
                    queue_number=q_num,
                    priority="routine",
                    status="waiting",
                    created_at=datetime.datetime.utcnow()
                )
                db.add(pharmacy_queue)

    await db.commit()
    await db.refresh(consultation)
    return ConsultationCompleteResponse(
        consultation_id=consultation.id,
        consultation_status=consultation.consultation_status,
        disposition=consultation.disposition,
        completed_at=consultation.completed_at,
        visit_status=target_visit_status
    )


# 16. GET /consultations/{consultation_id}/summary
@router.get("/{consultation_id}/summary", response_model=ConsultationResponse, tags=["Consultation Summary"])
async def get_consultation_summary(
    consultation_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_any_role(["doctor", "pharmacist", "lab_technician", "radiographer"])),
):
    """Retrieve a completed consultation summary."""
    stmt = select(Consultation).where(Consultation.id == consultation_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")

    # Fetch diagnoses
    diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation_id).order_by(Diagnosis.diagnosis_type, Diagnosis.recorded_at)
    diag_res = await db.execute(diag_stmt)
    diagnoses = diag_res.scalars().all()

    # Fetch investigations with result details
    inv_stmt = select(InvestigationRequest).where(
        InvestigationRequest.consultation_id == consultation_id,
        InvestigationRequest.status != "cancelled"
    )
    inv_res = await db.execute(inv_stmt)
    investigations = inv_res.scalars().all()

    inv_list = []
    for inv in investigations:
        result_data = None
        if inv.request_type == "laboratory" or inv.request_type == "lab":
            lab_stmt = select(LabResult).where(LabResult.request_id == inv.id)
            lab_res = await db.execute(lab_stmt)
            lab = lab_res.scalars().first()
            if lab:
                result_data = {
                    "result_id": str(lab.result_id),
                    "result_value": lab.result_value,
                    "unit": lab.unit,
                    "reference_range": lab.reference_range,
                    "is_critical": lab.is_critical,
                    "result_notes": lab.result_notes,
                    "status": lab.status,
                    "resulted_at": lab.resulted_at.isoformat() if lab.resulted_at else None
                }
        elif inv.request_type == "radiology":
            rad_stmt = select(RadiologyReport).where(RadiologyReport.request_id == inv.id)
            rad_res = await db.execute(rad_stmt)
            rad = rad_res.scalars().first()
            if rad:
                result_data = {
                    "report_id": str(rad.report_id),
                    "modality": rad.modality,
                    "body_part": rad.body_part,
                    "findings": rad.findings,
                    "impression": rad.impression,
                    "status": rad.status,
                    "reported_at": rad.reported_at.isoformat() if rad.reported_at else None
                }

        inv_list.append(InvestigationRequestResponse(
            id=inv.id,
            visit_id=inv.visit_id,
            consultation_id=inv.consultation_id,
            patient_id=inv.patient_id,
            request_type=inv.request_type,
            test_name=inv.test_name,
            test_code=inv.test_code,
            clinical_history=inv.clinical_history,
            status=inv.status,
            urgency=inv.urgency,
            created_by=inv.requested_by,
            created_at=inv.requested_at,
            result=result_data
        ))

    # Fetch prescriptions
    presc_stmt = select(Prescription).where(Prescription.consultation_id == consultation_id)
    presc_res = await db.execute(presc_stmt)
    prescriptions = presc_res.scalars().all()

    presc_list = [
        PrescriptionResponse(
            id=p.id,
            visit_id=p.visit_id,
            consultation_id=p.consultation_id,
            patient_id=p.patient_id,
            drug_name=p.drug_name,
            dose=p.dose,
            frequency=p.frequency,
            duration=p.duration,
            route=p.route,
            instructions=p.instructions,
            prescribed_by=p.prescribed_by,
            status=p.status
        ) for p in prescriptions
    ]

    return ConsultationResponse(
        id=consultation.id,
        visit_id=consultation.visit_id,
        patient_id=consultation.patient_id,
        history_of_presenting_illness=consultation.history_of_presenting_illness,
        examination_findings=consultation.examination_findings,
        clinical_impression=consultation.clinical_impression,
        consultation_status=consultation.consultation_status,
        started_at=consultation.started_at,
        completed_at=consultation.completed_at,
        disposition=consultation.disposition,
        referral_type=consultation.referral_type,
        referral_notes=consultation.referral_notes,
        created_by=consultation.created_by,
        created_at=consultation.created_at,
        updated_at=consultation.updated_at,
        diagnoses=diagnoses,
        investigation_requests=inv_list,
        prescriptions=presc_list
    )


# ---------------------------------------------------------------------------
# Re-route legacy/unaligned endpoints to match specifications
# ---------------------------------------------------------------------------

@router.post("/encounters", response_model=ConsultationResponse, status_code=status.HTTP_201_CREATED, tags=["Legacy Helpers"])
async def create_consultation(
    request: Request,
    body: ConsultationCreate,
    db: AsyncSession = Depends(get_tenant_db),
    ctx: TenantContext = Depends(get_current_tenant),
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
):
    """Legacy helper for backward compatibility."""
    # Check if consultation already exists
    stmt = select(Consultation).where(Consultation.visit_id == body.visit_id)
    res = await db.execute(stmt)
    db_consultation = res.scalars().first()

    if db_consultation:
        db_consultation.history_of_presenting_illness = body.history_of_presenting_illness
        db_consultation.examination_findings = body.examination_findings
        db_consultation.clinical_impression = body.clinical_impression
        db_consultation.created_by = ctx.user_sub
        db_consultation.updated_at = datetime.datetime.utcnow()
    else:
        db_consultation = Consultation(
            id=uuid.uuid4(),
            visit_id=body.visit_id,
            patient_id=body.patient_id,
            history_of_presenting_illness=body.history_of_presenting_illness,
            examination_findings=body.examination_findings,
            clinical_impression=body.clinical_impression,
            consultation_status="in_progress",
            started_at=datetime.datetime.utcnow(),
            created_by=ctx.user_sub,
            created_at=datetime.datetime.utcnow(),
            updated_at=datetime.datetime.utcnow(),
        )
        db.add(db_consultation)

    await db.commit()
    await db.refresh(db_consultation)

    if credentials:
        auth_header = f"Bearer {credentials.credentials}"
        tenant_db_url = request.headers.get("x-tenant-db")
        await _transition_visit_status(str(body.visit_id), "in_consultation", auth_header, tenant_db_url)

    return db_consultation


@router.get("/encounters/visit/{visit_id}", response_model=ConsultationResponse, tags=["Legacy Helpers"])
async def get_consultation_by_visit(
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Legacy helper."""
    stmt = select(Consultation).where(Consultation.visit_id == visit_id)
    res = await db.execute(stmt)
    consultation = res.scalars().first()
    if not consultation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Consultation not found")
    return consultation


@router.get("/encounters/patient/{patient_id}/encounter-view/{visit_id}", response_model=EncounterViewResponse, tags=["Legacy Helpers"])
async def get_patient_encounter_view(
    patient_id: uuid.UUID,
    visit_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Legacy view wrapper."""
    pat_stmt = select(Patient).where(Patient.id == patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit_id)
    triage_res = await db.execute(triage_stmt)
    triage_summary = triage_res.scalars().first()

    # Get ConsultationResponse full body
    cons_stmt = select(Consultation).where(Consultation.visit_id == visit_id)
    cons_res = await db.execute(cons_stmt)
    consultation = cons_res.scalars().first()

    cons_resp = None
    if consultation:
        diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation.id).order_by(Diagnosis.diagnosis_type, Diagnosis.recorded_at)
        diag_res = await db.execute(diag_stmt)
        diagnoses = diag_res.scalars().all()

        inv_stmt = select(InvestigationRequest).where(
            InvestigationRequest.consultation_id == consultation.id,
            InvestigationRequest.status != "cancelled"
        )
        inv_res = await db.execute(inv_stmt)
        investigations = inv_res.scalars().all()

        inv_list = []
        for inv in investigations:
            inv_list.append(InvestigationRequestResponse(
                id=inv.id,
                visit_id=inv.visit_id,
                consultation_id=inv.consultation_id,
                patient_id=inv.patient_id,
                request_type=inv.request_type,
                test_name=inv.test_name,
                test_code=inv.test_code,
                clinical_history=inv.clinical_history,
                status=inv.status,
                urgency=inv.urgency,
                created_by=inv.requested_by,
                created_at=inv.requested_at,
                result=None
            ))

        presc_stmt = select(Prescription).where(Prescription.consultation_id == consultation.id)
        presc_res = await db.execute(presc_stmt)
        prescriptions = presc_res.scalars().all()
        presc_list = [
            PrescriptionResponse(
                id=p.id,
                visit_id=p.visit_id,
                consultation_id=p.consultation_id,
                patient_id=p.patient_id,
                drug_name=p.drug_name,
                dose=p.dose,
                frequency=p.frequency,
                duration=p.duration,
                route=p.route,
                instructions=p.instructions,
                prescribed_by=p.prescribed_by,
                status=p.status
            ) for p in prescriptions
        ]

        cons_resp = ConsultationResponse(
            id=consultation.id,
            visit_id=consultation.visit_id,
            patient_id=consultation.patient_id,
            history_of_presenting_illness=consultation.history_of_presenting_illness,
            examination_findings=consultation.examination_findings,
            clinical_impression=consultation.clinical_impression,
            consultation_status=consultation.consultation_status,
            started_at=consultation.started_at,
            completed_at=consultation.completed_at,
            disposition=consultation.disposition,
            referral_type=consultation.referral_type,
            referral_notes=consultation.referral_notes,
            created_by=consultation.created_by,
            created_at=consultation.created_at,
            updated_at=consultation.updated_at,
            diagnoses=diagnoses,
            investigation_requests=inv_list,
            prescriptions=presc_list
        )

    return EncounterViewResponse(
        patient=patient,
        current_visit_id=visit_id,
        triage_summary=triage_summary,
        consultation=cons_resp,
    )


@router.get("/encounters/patient/{patient_id}/history", response_model=PatientHistoryResponse, tags=["Legacy Helpers"])
async def get_patient_history(
    patient_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
):
    """Retrieve history timeline for a patient."""
    pat_stmt = select(Patient).where(Patient.id == patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient not found")

    visit_stmt = select(Visit).where(Visit.patient_id == patient_id).order_by(Visit.visit_date.desc(), Visit.created_at.desc())
    visit_res = await db.execute(visit_stmt)
    visits = visit_res.scalars().all()

    previous_visits = []
    for visit in visits:
        triage_stmt = select(TriageAssessment).where(TriageAssessment.visit_id == visit.visit_id)
        triage_res = await db.execute(triage_stmt)
        triage = triage_res.scalars().first()

        # Load consultation details
        cons_stmt = select(Consultation).where(Consultation.visit_id == visit.visit_id)
        cons_res = await db.execute(cons_stmt)
        consultation = cons_res.scalars().first()

        cons_resp = None
        if consultation:
            diag_stmt = select(Diagnosis).where(Diagnosis.consultation_id == consultation.id).order_by(Diagnosis.diagnosis_type, Diagnosis.recorded_at)
            diag_res = await db.execute(diag_stmt)
            diagnoses = diag_res.scalars().all()

            inv_stmt = select(InvestigationRequest).where(
                InvestigationRequest.consultation_id == consultation.id,
                InvestigationRequest.status != "cancelled"
            )
            inv_res = await db.execute(inv_stmt)
            investigations = inv_res.scalars().all()
            inv_list = []
            for inv in investigations:
                inv_list.append(InvestigationRequestResponse(
                    id=inv.id,
                    visit_id=inv.visit_id,
                    consultation_id=inv.consultation_id,
                    patient_id=inv.patient_id,
                    request_type=inv.request_type,
                    test_name=inv.test_name,
                    test_code=inv.test_code,
                    clinical_history=inv.clinical_history,
                    status=inv.status,
                    urgency=inv.urgency,
                    created_by=inv.requested_by,
                    created_at=inv.requested_at,
                    result=None
                ))

            presc_stmt = select(Prescription).where(Prescription.consultation_id == consultation.id)
            presc_res = await db.execute(presc_stmt)
            prescriptions = presc_res.scalars().all()
            presc_list = [
                PrescriptionResponse(
                    id=p.id,
                    visit_id=p.visit_id,
                    consultation_id=p.consultation_id,
                    patient_id=p.patient_id,
                    drug_name=p.drug_name,
                    dose=p.dose,
                    frequency=p.frequency,
                    duration=p.duration,
                    route=p.route,
                    instructions=p.instructions,
                    prescribed_by=p.prescribed_by,
                    status=p.status
                ) for p in prescriptions
            ]

            cons_resp = ConsultationResponse(
                id=consultation.id,
                visit_id=consultation.visit_id,
                patient_id=consultation.patient_id,
                history_of_presenting_illness=consultation.history_of_presenting_illness,
                examination_findings=consultation.examination_findings,
                clinical_impression=consultation.clinical_impression,
                consultation_status=consultation.consultation_status,
                started_at=consultation.started_at,
                completed_at=consultation.completed_at,
                disposition=consultation.disposition,
                referral_type=consultation.referral_type,
                referral_notes=consultation.referral_notes,
                created_by=consultation.created_by,
                created_at=consultation.created_at,
                updated_at=consultation.updated_at,
                diagnoses=diagnoses,
                investigation_requests=inv_list,
                prescriptions=presc_list
            )

        previous_visits.append(
            VisitHistoryItem(
                visit_id=visit.visit_id,
                visit_date=visit.visit_date,
                visit_type=visit.visit_type,
                status=visit.status,
                triage_summary=triage,
                consultation=cons_resp,
            )
        )

    return PatientHistoryResponse(patient=patient, previous_visits=previous_visits)
