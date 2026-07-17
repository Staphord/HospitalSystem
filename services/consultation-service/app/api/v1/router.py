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
    AdmittedPatientResponse,
    AdmissionDetailsResponse,
    InpatientOrderResponse,
    InpatientOrderCreate,
    OrderStatusUpdate,
    DischargeRequest,
    AdmissionSummary,
    PatientListItem,
    PatientSearchResponse,
    InvestigationPatientResponse,
    InvestigationResultListItem,
    ReferralCreateRequest,
    ReferralResponse,
    ReferralPatientResponse,
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
    InpatientAdmission,
    InpatientOrder,
    Referral,
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
        pending_count = len([i for i in invs if i.status not in ("completed", "cancelled", "acknowledged")])
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
        admission_reason=consultation.admission_reason,
        discharge_instructions=consultation.discharge_instructions,
        follow_up_date=consultation.follow_up_date,
        return_date=consultation.return_date,
        return_reason=consultation.return_reason,
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
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled", "acknowledged")]
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
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled", "acknowledged")]
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
    remaining_pending = [i for i in all_invs if i.id != request_id and i.status not in ("completed", "cancelled", "acknowledged")]

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
        pending_invs = [i for i in invs if i.status not in ("completed", "cancelled", "acknowledged")]
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
    pending_invs = [i for i in invs if i.status not in ("completed", "cancelled", "acknowledged")]
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
        admission_reason=consultation.admission_reason,
        discharge_instructions=consultation.discharge_instructions,
        follow_up_date=consultation.follow_up_date,
        return_date=consultation.return_date,
        return_reason=consultation.return_reason,
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
            admission_reason=consultation.admission_reason,
            discharge_instructions=consultation.discharge_instructions,
            follow_up_date=consultation.follow_up_date,
            return_date=consultation.return_date,
            return_reason=consultation.return_reason,
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
                admission_reason=consultation.admission_reason,
                discharge_instructions=consultation.discharge_instructions,
                follow_up_date=consultation.follow_up_date,
                return_date=consultation.return_date,
                return_reason=consultation.return_reason,
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


# ── Doctor's Inpatient Module Endpoints ─────────────────────────────────────

@router.get("/inpatient/admissions", response_model=List[AdmittedPatientResponse], tags=["Inpatient Dashboard"])
async def get_inpatient_admissions(
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """List all currently active admitted patients (and auto-admit pending ones)."""
    # 1. Self-Healing Bridge: Find all visits with status = "admitted"
    visits_stmt = select(Visit).where(Visit.status == "admitted")
    visits_res = await db.execute(visits_stmt)
    admitted_visits = visits_res.scalars().all()

    for visit in admitted_visits:
        # Check if InpatientAdmission exists
        adm_stmt = select(InpatientAdmission).where(InpatientAdmission.visit_id == visit.visit_id)
        adm_res = await db.execute(adm_stmt)
        existing_adm = adm_res.scalars().first()

        if not existing_adm:
            # Fetch admitting diagnosis/reason from completed consultation
            cons_stmt = select(Consultation).where(Consultation.visit_id == visit.visit_id)
            cons_res = await db.execute(cons_stmt)
            consultation = cons_res.scalars().first()
            
            admitting_diag = "Inpatient Admission Requested"
            if consultation:
                admitting_diag = consultation.admission_reason or consultation.clinical_impression or admitting_diag

            new_adm = InpatientAdmission(
                visit_id=visit.visit_id,
                patient_id=visit.patient_id,
                ward="Unassigned Ward",
                bed="Pending Bed",
                status="admitted",
                condition="monitoring",
                admitting_diagnosis=admitting_diag,
                admission_date=datetime.datetime.utcnow(),
            )
            db.add(new_adm)
    
    await db.commit()

    # 2. Return all admissions where status != 'discharged'
    stmt = (
        select(InpatientAdmission)
        .where(InpatientAdmission.status != "discharged")
        .order_by(InpatientAdmission.admission_date.desc())
    )
    res = await db.execute(stmt)
    admissions = res.scalars().all()

    results = []
    for adm in admissions:
        # Join with Patient to get demographics
        patient_stmt = select(Patient).where(Patient.id == adm.patient_id)
        patient_res = await db.execute(patient_stmt)
        patient = patient_res.scalars().first()
        if not patient:
            continue

        # Calculate length of stay (minimum 1 day)
        los = (datetime.datetime.utcnow() - adm.admission_date).days
        if los < 1:
            los = 1

        # Calculate age
        today = datetime.date.today()
        birth = patient.date_of_birth
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

        # Get initials
        names = patient.full_name.split()
        initials = "".join([n[0] for n in names[:2]]).upper() if names else "PT"

        results.append(
            AdmittedPatientResponse(
                id=adm.id,
                patient_id=adm.patient_id,
                name=patient.full_name,
                patient_number=patient.patient_number,
                initials=initials,
                gender=patient.gender,
                age=age,
                ward=adm.ward or "Unassigned Ward",
                bed=adm.bed or "Pending Bed",
                admission_date=adm.admission_date.strftime("%b %d, %Y"),
                length_of_stay=los,
                diagnosis=adm.admitting_diagnosis or "Unknown",
                primary_diagnosis=adm.admitting_diagnosis or "Unknown",
                status=adm.condition, # 'critical' | 'stable' | 'monitoring' | 'discharge-ready'
            )
        )
    return results


@router.get("/inpatient/admissions/{admission_id}", response_model=AdmissionDetailsResponse, tags=["Inpatient Dashboard"])
async def get_admission_details(
    admission_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve full details and summary for a single inpatient admission."""
    adm_stmt = select(InpatientAdmission).where(InpatientAdmission.id == admission_id)
    adm_res = await db.execute(adm_stmt)
    adm = adm_res.scalars().first()
    if not adm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admission not found")

    patient_stmt = select(Patient).where(Patient.id == adm.patient_id)
    patient_res = await db.execute(patient_stmt)
    patient = patient_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Patient demographics not found")

    # Fetch admitting doctor from consultation
    cons_stmt = select(Consultation).where(Consultation.visit_id == adm.visit_id)
    cons_res = await db.execute(cons_stmt)
    consultation = cons_res.scalars().first()
    doc_name = consultation.created_by if (consultation and consultation.created_by) else "Dr. Amina Hassan"

    # Calculate length of stay (minimum 1 day)
    los = (datetime.datetime.utcnow() - adm.admission_date).days
    if los < 1:
        los = 1

    # Calculate age
    today = datetime.date.today()
    birth = patient.date_of_birth
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))

    names = patient.full_name.split()
    initials = "".join([n[0] for n in names[:2]]).upper() if names else "PT"

    pat_resp = AdmittedPatientResponse(
        id=adm.id,
        patient_id=adm.patient_id,
        name=patient.full_name,
        patient_number=patient.patient_number,
        initials=initials,
        gender=patient.gender,
        age=age,
        ward=adm.ward or "Unassigned Ward",
        bed=adm.bed or "Pending Bed",
        admission_date=adm.admission_date.strftime("%b %d, %Y"),
        length_of_stay=los,
        diagnosis=adm.admitting_diagnosis or "Unknown",
        primary_diagnosis=adm.admitting_diagnosis or "Unknown",
        status=adm.condition,
    )

    # Generate mock key events based on admission logs
    key_events = [
        {"date": adm.admission_date.strftime("%d %b"), "description": f"Admitted to {adm.ward or 'Unassigned Ward'} / {adm.bed or 'Pending Bed'} with diagnosis: {adm.admitting_diagnosis or 'Unknown'}."}
    ]

    summary = AdmissionSummary(
        admitting_diagnosis=adm.admitting_diagnosis or "Unknown",
        admitting_doctor=doc_name,
        ward_service=adm.ward or "Unassigned Ward",
        key_events=key_events,
    )

    return AdmissionDetailsResponse(patient=pat_resp, summary=summary)


@router.get("/inpatient/admissions/{admission_id}/orders", response_model=List[InpatientOrderResponse], tags=["Inpatient Dashboard"])
async def get_inpatient_orders_route(
    admission_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all active inpatient orders for the admission."""
    stmt = (
        select(InpatientOrder)
        .where(InpatientOrder.admission_id == admission_id)
        .where(InpatientOrder.status != "discontinued")
        .order_by(InpatientOrder.issued_at.desc())
    )
    res = await db.execute(stmt)
    orders = res.scalars().all()

    return [
        InpatientOrderResponse(
            id=o.id,
            admission_id=o.admission_id,
            order_type=o.order_type,
            description=o.description,
            sub_description=o.sub_description,
            issued_at="Issued " + o.issued_at.strftime("%b %d %H:%M"),
            due_label=o.due_label,
            status=o.status,
            completed_by=o.completed_by,
        )
        for o in orders
    ]


@router.post("/inpatient/admissions/{admission_id}/orders", response_model=InpatientOrderResponse, tags=["Inpatient Dashboard"])
async def create_inpatient_order_route(
    admission_id: uuid.UUID,
    body: InpatientOrderCreate,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Issue a new daily ward inpatient order (Medication, Nursing, Diet, Investigation)."""
    # Verify admission exists
    adm_stmt = select(InpatientAdmission).where(InpatientAdmission.id == admission_id)
    adm_res = await db.execute(adm_stmt)
    adm = adm_res.scalars().first()
    if not adm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admission not found")

    new_order = InpatientOrder(
        id=uuid.uuid4(),
        admission_id=admission_id,
        order_type=body.order_type,
        description=body.description,
        sub_description=body.sub_description,
        issued_at=datetime.datetime.utcnow(),
        issued_by=current_user.username if hasattr(current_user, "username") else "doctor",
        due_label=body.due_label or "Due as scheduled",
        status="pending",
    )
    db.add(new_order)
    await db.commit()
    await db.refresh(new_order)

    return InpatientOrderResponse(
        id=new_order.id,
        admission_id=new_order.admission_id,
        order_type=new_order.order_type,
        description=new_order.description,
        sub_description=new_order.sub_description,
        issued_at="Issued Today " + new_order.issued_at.strftime("%H:%M"),
        due_label=new_order.due_label,
        status=new_order.status,
        completed_by=new_order.completed_by,
    )


@router.put("/inpatient/orders/{order_id}/status", response_model=InpatientOrderResponse, tags=["Inpatient Dashboard"])
async def update_inpatient_order_status(
    order_id: uuid.UUID,
    body: OrderStatusUpdate,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Transition an inpatient order status (e.g., mark as done or discontinued)."""
    stmt = select(InpatientOrder).where(InpatientOrder.id == order_id)
    res = await db.execute(stmt)
    order = res.scalars().first()
    if not order:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    order.status = body.status
    if body.status == "done":
        order.completed_at = datetime.datetime.utcnow()
        order.completed_by = current_user.username if hasattr(current_user, "username") else "doctor"
    
    await db.commit()
    await db.refresh(order)

    return InpatientOrderResponse(
        id=order.id,
        admission_id=order.admission_id,
        order_type=order.order_type,
        description=order.description,
        sub_description=order.sub_description,
        issued_at="Issued " + order.issued_at.strftime("%b %d %H:%M"),
        due_label=order.due_label,
        status=order.status,
        completed_by=order.completed_by,
    )


@router.post("/inpatient/admissions/{admission_id}/discharge", tags=["Inpatient Dashboard"])
async def discharge_inpatient_patient(
    admission_id: uuid.UUID,
    body: DischargeRequest,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Discharge an inpatient patient and transition visit status to completed."""
    # 1. Fetch Admission
    adm_stmt = select(InpatientAdmission).where(InpatientAdmission.id == admission_id)
    adm_res = await db.execute(adm_stmt)
    adm = adm_res.scalars().first()
    if not adm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Admission not found")

    # 2. Update Admission details
    adm.status = "discharged"
    adm.discharge_diagnosis = body.discharge_diagnosis
    adm.care_summary = body.care_summary
    adm.discharge_instructions = body.instructions
    if body.follow_up_date:
        try:
            adm.follow_up_date = datetime.datetime.strptime(body.follow_up_date, "%Y-%m-%d").date()
        except ValueError:
            pass
    adm.discharged_at = datetime.datetime.utcnow()
    adm.updated_at = datetime.datetime.utcnow()

    # 3. Transition Visit directly in DB
    visit_stmt = select(Visit).where(Visit.visit_id == adm.visit_id)
    visit_res = await db.execute(visit_stmt)
    visit = visit_res.scalars().first()
    if visit:
        visit.status = "completed"
        visit.updated_at = datetime.datetime.utcnow()

    await db.commit()
    return {"status": "success", "message": "Patient discharged successfully"}


# ── Doctor-side Patient History Search Endpoints ──────────────────────────────

@router.get("/patients/recent", response_model=List[PatientListItem], tags=["Patient History Search"])
async def get_recent_patients(
    limit: int = 6,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Return the most recently visited unique patients (by latest visit_date)."""
    # Get distinct patient_ids ordered by most recent visit, then fetch patient objects
    from sqlalchemy import func
    subq = (
        select(Visit.patient_id, func.max(Visit.visit_date).label("latest_visit"))
        .group_by(Visit.patient_id)
        .order_by(func.max(Visit.visit_date).desc())
        .limit(limit)
        .subquery()
    )
    stmt = (
        select(Patient)
        .join(subq, Patient.id == subq.c.patient_id)
        .order_by(subq.c.latest_visit.desc())
    )
    res = await db.execute(stmt)
    patients = res.scalars().all()
    return [PatientListItem.model_validate(p) for p in patients]


@router.get("/patients", response_model=PatientSearchResponse, tags=["Patient History Search"])
async def search_patients_endpoint(
    search: str = "",
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Search patients by name, patient number, or phone. Returns paginated results."""
    from sqlalchemy import func, or_
    q = search.strip()
    base = select(Patient)
    if q:
        pattern = f"%{q}%"
        base = base.where(
            or_(
                Patient.full_name.ilike(pattern),
                Patient.patient_number.ilike(pattern),
                Patient.phone_primary.ilike(pattern),
            )
        )
    # Count total
    count_stmt = select(func.count()).select_from(base.subquery())
    count_res = await db.execute(count_stmt)
    total = count_res.scalar() or 0

    # Paginate
    stmt = base.order_by(Patient.full_name).offset((page - 1) * page_size).limit(page_size)
    res = await db.execute(stmt)
    patients = res.scalars().all()

    return PatientSearchResponse(
        patients=[PatientListItem.model_validate(p) for p in patients],
        total=total,
        page=page,
        page_size=page_size,
    )


# ── Doctor-side Investigation Results Dashboard ───────────────────────────────

@router.get("/investigations/results", response_model=List[InvestigationResultListItem], tags=["Investigations"])
async def get_all_investigation_results(
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all active/recent investigation requests and results for the doctor dashboard."""
    stmt = select(InvestigationRequest).order_by(InvestigationRequest.created_at.desc())
    res = await db.execute(stmt)
    invs = res.scalars().all()

    results = []
    for inv in invs:
        # Load patient
        pat_stmt = select(Patient).where(Patient.id == inv.patient_id)
        pat_res = await db.execute(pat_stmt)
        patient = pat_res.scalars().first()
        if not patient:
            continue

        result_data = None
        completed_at = None
        
        # Check status
        if inv.request_type == "laboratory" or inv.request_type == "lab":
            lab_stmt = select(LabResult).where(LabResult.request_id == inv.id)
            lab_res = await db.execute(lab_stmt)
            lab = lab_res.scalars().first()
            if lab:
                result_data = {
                    "result_value": lab.result_value,
                    "unit": lab.unit,
                    "reference_range": lab.reference_range,
                    "result_notes": lab.result_notes,
                    "is_critical": lab.is_critical
                }
                completed_at = lab.resulted_at
        elif inv.request_type == "radiology":
            rad_stmt = select(RadiologyReport).where(RadiologyReport.request_id == inv.id)
            rad_res = await db.execute(rad_stmt)
            rad = rad_res.scalars().first()
            if rad:
                result_data = {
                    "findings": rad.findings,
                    "impression": rad.impression
                }
                completed_at = rad.reported_at

        # Determine UI status
        ui_status = inv.status  # default to db status e.g. pending
        if inv.status == "acknowledged":
            ui_status = "acknowledged"
        elif inv.status == "completed":
            if result_data:
                # critical check
                is_critical = False
                if inv.request_type == "laboratory" or inv.request_type == "lab":
                    is_critical = result_data.get("is_critical", False)
                elif inv.request_type == "radiology":
                    findings = (result_data.get("findings") or "").lower()
                    impression = (result_data.get("impression") or "").lower()
                    critical_keywords = ["subdural", "fracture", "hemorrhage", "bleed", "critical", "severe", "pneumothorax"]
                    is_critical = any(kw in findings or kw in impression for kw in critical_keywords)

                ui_status = "critical" if is_critical else "ready"
            else:
                ui_status = "ready"

        # Construct final string values for results
        result_values_str = None
        ref_range_str = None
        notes_str = None

        if result_data:
            if inv.request_type == "laboratory" or inv.request_type == "lab":
                unit = f" {result_data['unit']}" if result_data.get("unit") else ""
                result_values_str = f"{inv.test_name}: {result_data['result_value']}{unit}"
                ref_range_str = result_data.get("reference_range")
                notes_str = result_data.get("result_notes")
            elif inv.request_type == "radiology":
                result_values_str = result_data.get("findings")
                ref_range_str = result_data.get("impression")
                notes_str = "Reported by Radiologist"

        results.append(InvestigationResultListItem(
            id=inv.id,
            patient=InvestigationPatientResponse(
                id=patient.id,
                patient_number=patient.patient_number,
                full_name=patient.full_name
            ),
            visit_id=inv.visit_id,
            test_name=inv.test_name,
            request_type=inv.request_type,
            urgency=inv.urgency,
            status=ui_status,
            ordered_at=inv.requested_at,
            completed_at=completed_at,
            result_values=result_values_str,
            reference_range=ref_range_str,
            lab_notes=notes_str
        ))
    return results


@router.put("/investigations/{request_id}/acknowledge", tags=["Investigations"])
async def acknowledge_investigation(
    request_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Acknowledge an investigation result."""
    stmt = select(InvestigationRequest).where(InvestigationRequest.id == request_id)
    res = await db.execute(stmt)
    inv = res.scalars().first()
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation request not found")
    inv.status = "acknowledged"
    await db.commit()
    return {"status": "success", "message": "Result acknowledged"}


# ── Doctor-side Referrals Dashboard ───────────────────────────────────────────

@router.get("/referrals", response_model=List[ReferralResponse], tags=["Referrals"])
async def get_all_referrals(
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Retrieve all active outgoing referrals."""
    stmt = select(Referral).where(Referral.status != "cancelled").order_by(Referral.referred_at.desc())
    res = await db.execute(stmt)
    referrals = res.scalars().all()
    
    out = []
    for ref in referrals:
        out.append(ReferralResponse(
            id=ref.id,
            patient=ReferralPatientResponse(
                id=ref.patient.id,
                patient_number=ref.patient.patient_number,
                full_name=ref.patient.full_name
            ),
            visit_id=ref.visit_id,
            referred_to=ref.referred_to,
            type=ref.type,
            reason=ref.reason,
            status=ref.status,
            urgency=ref.urgency,
            category=ref.category,
            department=ref.department,
            preferred_doctor=ref.preferred_doctor,
            hospital_name=ref.hospital_name,
            external_doctor=ref.external_doctor,
            contact_number=ref.contact_number,
            decline_reason=ref.decline_reason,
            referred_at=ref.referred_at,
            responded_at=ref.responded_at
        ))
    return out


@router.post("/referrals", response_model=ReferralResponse, tags=["Referrals"])
async def create_referral(
    body: ReferralCreateRequest,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Create a new outgoing patient referral."""
    # Verify patient exists
    pat_stmt = select(Patient).where(Patient.id == body.patient_id)
    pat_res = await db.execute(pat_stmt)
    patient = pat_res.scalars().first()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    new_ref = Referral(
        id=uuid.uuid4(),
        patient_id=body.patient_id,
        visit_id=body.visit_id,
        referred_to=body.referred_to,
        type=body.type,
        reason=body.reason,
        status="pending",
        urgency=body.urgency,
        category=body.category,
        department=body.department,
        preferred_doctor=body.preferred_doctor,
        hospital_name=body.hospital_name,
        external_doctor=body.external_doctor,
        contact_number=body.contact_number,
        referred_at=datetime.datetime.utcnow()
    )
    db.add(new_ref)
    await db.commit()
    
    return ReferralResponse(
        id=new_ref.id,
        patient=ReferralPatientResponse(
            id=patient.id,
            patient_number=patient.patient_number,
            full_name=patient.full_name
        ),
        visit_id=new_ref.visit_id,
        referred_to=new_ref.referred_to,
        type=new_ref.type,
        reason=new_ref.reason,
        status=new_ref.status,
        urgency=new_ref.urgency,
        category=new_ref.category,
        department=new_ref.department,
        preferred_doctor=new_ref.preferred_doctor,
        hospital_name=new_ref.hospital_name,
        external_doctor=new_ref.external_doctor,
        contact_number=new_ref.contact_number,
        referred_at=new_ref.referred_at
    )


@router.put("/referrals/{referral_id}/cancel", tags=["Referrals"])
async def cancel_referral_endpoint(
    referral_id: uuid.UUID,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("doctor")),
):
    """Soft-delete / cancel a pending referral."""
    stmt = select(Referral).where(Referral.id == referral_id)
    res = await db.execute(stmt)
    ref = res.scalars().first()
    if not ref:
        raise HTTPException(status_code=404, detail="Referral not found")
        
    ref.status = "cancelled"
    await db.commit()
    return {"status": "success", "message": "Referral cancelled"}
