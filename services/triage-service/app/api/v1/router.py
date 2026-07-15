import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import UUID as pgUUID
from typing import Optional
from uuid import UUID

from app.core.tenant_auth import get_current_tenant
from app.core.config import settings
from app.dependencies import get_tenant_db
from app.core.security import TokenPayload, require_role, require_any_role
from app.exceptions import NotFoundError, BadRequestError, ConflictError
from app.api.v1.schemas import (
    TriageAssessmentCreate,
    TriageAssessmentResponse,
    VitalsInput,
    TriageCategorySuggestionResponse,
    TriageQueueResponse,
    QueueCallResponse,
    QueueSkipResponse,
    TriageQueueItem,
    PatientQueueInfo,
    VisitQueueInfo,
    TriageSummaryResponse,
    TriageHistorySearchResponse,
    TriageHistoryPatientItem
)
from app.services.triage import (
    suggest_category_from_vitals,
    record_triage_assessment,
    get_triage_summary
)
import logging

logger = logging.getLogger("triage_router")

router = APIRouter(dependencies=[Depends(get_current_tenant)])


@router.post(
    "/assessments",
    response_model=TriageAssessmentResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Triage Assessments"]
)
async def create_assessment(
    request: Request,
    payload: TriageAssessmentCreate,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("triage_nurse")),
    tenant_ctx = Depends(get_current_tenant)
):
    """
    Record a new triage assessment for a patient visit.
    This also completes the triage queue and enqueues the patient to the doctor consultation queue.
    """
    auth_header = request.headers.get("Authorization")
    assessment_dict = payload.model_dump()
    
    try:
        assessment = await record_triage_assessment(
            db=db,
            assessment_data=assessment_dict,
            created_by=current_user.sub,
            auth_header=auth_header,
            tenant_id=tenant_ctx.tenant_id
        )
        return assessment
    except httpx.HTTPStatusError as e:
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Downstream visit-service error: {e.response.text}"
        )


@router.post("/assessments/suggest-category", response_model=TriageCategorySuggestionResponse, tags=["Triage Suggestions"])
def suggest_category(payload: VitalsInput):
    """
    Calculate and suggest a triage category based on recorded vital signs.
    """
    category, reason = suggest_category_from_vitals(payload.model_dump(exclude_unset=True))
    return TriageCategorySuggestionResponse(
        suggested_category=category,
        reason=reason
    )


@router.get("/assessments/{visit_id}", response_model=TriageSummaryResponse, tags=["Triage Assessments"])
async def read_triage_summary(
    visit_id: str,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_any_role(["triage_nurse", "doctor"]))
):
    """
    Retrieve the triage summary for a specific visit.
    """
    assessment = await get_triage_summary(db, visit_id)
    if not assessment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Triage assessment not found for this visit"
        )
    return assessment


@router.get("/queue", response_model=TriageQueueResponse, tags=["Triage Queue"])
async def get_triage_queue(
    status: Optional[str] = Query("waiting,in_progress"),
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("triage_nurse"))
):
    """
    Returns the list of patients currently in the triage queue, ordered by clinical priority and arrival.
    """
    from app.models.triage import Queue, Visit, Patient, TriageAssessment
    from sqlalchemy import case

    status_list = [s.strip() for s in status.split(",") if s.strip()] if status else ["waiting", "in_progress"]

    priority_case = case(
        (Queue.priority == "emergency", 1),
        (Queue.priority == "urgent", 2),
        (Queue.priority == "semi_urgent", 3),
        (Queue.priority == "non_urgent", 4),
        else_=5
    )
    
    stmt = (
        select(Queue, Visit, Patient, TriageAssessment.triage_category)
        .join(Visit, Queue.visit_id == Visit.visit_id)
        .join(Patient, Patient.id == cast(Queue.patient_id, pgUUID))
        .outerjoin(TriageAssessment, TriageAssessment.visit_id == Queue.visit_id)
        .where(Queue.queue_type == "triage")
        .where(Queue.status.in_(status_list))
        .order_by(priority_case, Queue.created_at.asc())
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    queue_items = []
    for row in rows:
        q = row[0]
        v = row[1]
        p = row[2]
        cat = row[3]
        
        queue_items.append(
            TriageQueueItem(
                queue_id=q.queue_id,
                queue_number=q.queue_number,
                priority=cat if cat else q.priority,
                status=q.status,
                called_at=q.called_at,
                completed_at=q.completed_at,
                created_at=q.created_at,
                patient=PatientQueueInfo(
                    patient_id=p.id,
                    patient_number=p.patient_number,
                    full_name=p.full_name,
                    date_of_birth=p.date_of_birth,
                    gender=p.gender
                ),
                visit=VisitQueueInfo(
                    visit_id=v.visit_id,
                    visit_number=v.visit_number,
                    visit_type=v.visit_type,
                    payment_type=v.payment_type
                )
            )
        )
        
    return TriageQueueResponse(
        queue=queue_items,
        total=len(queue_items)
    )


@router.patch("/queue/{queue_id}/call", response_model=QueueCallResponse, tags=["Triage Queue"])
async def call_patient(
    queue_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("triage_nurse"))
):
    """
    Call a patient to the triage bay, updating status to in_progress.
    """
    from app.models.triage import Queue
    
    stmt = select(Queue).where(Queue.queue_id == queue_id)
    result = await db.execute(stmt)
    q = result.scalars().first()
    
    if not q:
        raise NotFoundError("Queue entry not found")
        
    if q.queue_type != "triage":
        raise BadRequestError("Queue entry is not of type triage")
        
    if q.status != "waiting":
        raise ConflictError("Queue entry is not in waiting status")
        
    auth_header = request.headers.get("Authorization")
    headers = {"Authorization": auth_header} if auth_header else {}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        url = f"{settings.visit_service_url}/api/v1/visits/queues/{queue_id}/status"
        try:
            resp = await client.patch(url, json={"status": "in_progress"}, headers=headers)
            if resp.status_code >= 400:
                resp.raise_for_status()
        except Exception as e:
            logger.exception(f"Failed to update queue status in visit-service: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to call patient: {str(e)}")
            
    db_stmt = select(Queue).where(Queue.queue_id == queue_id)
    db_res = await db.execute(db_stmt)
    updated_q = db_res.scalars().first()
    await db.refresh(updated_q)
    
    return QueueCallResponse(
        queue_id=updated_q.queue_id,
        status=updated_q.status,
        called_at=updated_q.called_at
    )


@router.patch("/queue/{queue_id}/skip", response_model=QueueSkipResponse, tags=["Triage Queue"])
async def skip_patient(
    queue_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("triage_nurse"))
):
    """
    Skip a patient when they do not respond to being called.
    """
    from app.models.triage import Queue
    
    stmt = select(Queue).where(Queue.queue_id == queue_id)
    result = await db.execute(stmt)
    q = result.scalars().first()
    
    if not q:
        raise NotFoundError("Queue entry not found")
        
    if q.queue_type != "triage":
        raise BadRequestError("Queue entry is not of type triage")
        
    if q.status not in ("waiting", "in_progress"):
        raise ConflictError("Queue entry already completed or skipped")
        
    auth_header = request.headers.get("Authorization")
    headers = {"Authorization": auth_header} if auth_header else {}
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        url = f"{settings.visit_service_url}/api/v1/visits/queues/{queue_id}/status"
        try:
            resp = await client.patch(url, json={"status": "skipped"}, headers=headers)
            if resp.status_code >= 400:
                resp.raise_for_status()
        except Exception as e:
            logger.exception(f"Failed to update queue status in visit-service: {e}")
            raise HTTPException(status_code=500, detail=f"Failed to skip patient: {str(e)}")
            
    db_stmt = select(Queue).where(Queue.queue_id == queue_id)
    db_res = await db.execute(db_stmt)
    updated_q = db_res.scalars().first()
    await db.refresh(updated_q)
    
    return QueueSkipResponse(
        queue_id=updated_q.queue_id,
        status=updated_q.status,
        completed_at=updated_q.completed_at
    )


@router.get("/history/search", response_model=TriageHistorySearchResponse, tags=["Triage History"])
async def search_triage_history(
    query: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_tenant_db),
    tenant_ctx = Depends(get_current_tenant)
):
    """
    Search patients in unified hospital DB and return their triage stats summary.
    If no query is provided, returns the most recently triaged patients.
    """
    from app.models.triage import Patient, TriageAssessment, Visit
    from sqlalchemy import func
    from datetime import date

    # Subquery 1: Row numbers for latest assessment of each patient to select its fields
    latest_sub = (
        select(
            TriageAssessment.patient_id,
            TriageAssessment.triage_category,
            TriageAssessment.assessed_at,
            func.row_number().over(
                partition_by=TriageAssessment.patient_id,
                order_by=TriageAssessment.assessed_at.desc()
            ).label("rn")
        )
    ).subquery()

    # Subquery 2: Count of visits per patient (cast patient_id to PG UUID to match Patient.id)
    count_sub = (
        select(
            cast(Visit.patient_id, pgUUID).label("patient_id"),
            func.count(Visit.visit_id).label("total_count")
        )
        .group_by(cast(Visit.patient_id, pgUUID))
    ).subquery()

    # Base select query
    stmt = (
        select(
            Patient,
            latest_sub.c.triage_category,
            latest_sub.c.assessed_at,
            func.coalesce(count_sub.c.total_count, 0)
        )
        .outerjoin(latest_sub, (latest_sub.c.patient_id == Patient.id) & (latest_sub.c.rn == 1))
        .outerjoin(count_sub, count_sub.c.patient_id == Patient.id)
        .where(Patient.hospital_id == tenant_ctx.tenant_id)
    )

    if query:
        search_term = f"%{query.strip()}%"
        stmt = stmt.where(
            (Patient.full_name.ilike(search_term)) |
            (Patient.patient_number.ilike(search_term))
        )
    else:
        # If no search term is entered, return patients ordered by their latest visit created_at descending
        latest_visit_sub = (
            select(
                cast(Visit.patient_id, pgUUID).label("patient_id"),
                func.max(Visit.created_at).label("latest_created")
            )
            .group_by(cast(Visit.patient_id, pgUUID))
        ).subquery()
        stmt = stmt.join(latest_visit_sub, latest_visit_sub.c.patient_id == Patient.id).order_by(latest_visit_sub.c.latest_created.desc())

    # Count total matching results
    count_stmt = select(func.count()).select_from(stmt.alias())
    count_res = await db.execute(count_stmt)
    total_count = count_res.scalar() or 0

    # Paginate and fetch results
    stmt = stmt.limit(limit).offset(offset)
    result = await db.execute(stmt)
    rows = result.all()

    today = date.today()
    patients_list = []
    for row in rows:
        p = row[0]
        triage_cat = row[1]
        assessed_at = row[2]
        total_visits = row[3]

        # Calculate age
        age = today.year - p.date_of_birth.year - ((today.month, today.day) < (p.date_of_birth.month, p.date_of_birth.day))

        # Format category label
        cat_label = None
        if triage_cat:
            cat_label = triage_cat.replace('_', '-').title()

        patients_list.append(
            TriageHistoryPatientItem(
                id=p.id,
                name=p.full_name,
                patientNumber=p.patient_number,
                gender=p.gender.title(),
                dob=p.date_of_birth.strftime("%d %b %Y"),
                age=age,
                phone="", # Default empty or query if phone exists on patient schema replica
                lastTriageCategory=cat_label,
                lastAssessedAt=assessed_at.strftime("%d/%m/%Y %H:%M") if assessed_at else None,
                assessmentCount=total_visits
            )
        )

    return TriageHistorySearchResponse(
        patients=patients_list,
        total=total_count
    )


@router.get("/patients/{patient_id}/assessments", response_model=list[TriageSummaryResponse], tags=["Triage History"])
async def get_patient_assessments(
    patient_id: UUID,
    db: AsyncSession = Depends(get_tenant_db)
):
    """
    Retrieve all historical triage assessments and visits for a patient ID.
    """
    from app.models.triage import TriageAssessment, Patient, Visit, Queue
    from app.models.user import User
    
    stmt = (
        select(Visit, TriageAssessment, Patient, Queue)
        .join(Patient, Patient.id == cast(Visit.patient_id, pgUUID))
        .outerjoin(TriageAssessment, TriageAssessment.visit_id == Visit.visit_id)
        .outerjoin(Queue, (Queue.visit_id == Visit.visit_id) & (Queue.queue_type == "triage"))
        .where(cast(Visit.patient_id, pgUUID) == patient_id)
        .order_by(Visit.created_at.desc())
    )
    result = await db.execute(stmt)
    rows = result.all()

    response_list = []
    for v, ass, p, q in rows:
        nurse_data = None
        vitals_data = None
        
        # Override visit status if the triage queue entry was skipped
        raw_status = v.status
        if q and q.status == "skipped":
            raw_status = "skipped"
            
        if ass:
            nurse_stmt = select(User).where(User.keycloak_sub == str(ass.triage_nurse_id))
            nurse_res = await db.execute(nurse_stmt)
            nurse = nurse_res.scalars().first()
            nurse_name = nurse.full_name if nurse else "Triage Nurse"
            
            nurse_data = {
                "user_id": ass.triage_nurse_id,
                "full_name": nurse_name
            }
            vitals_data = {
                "blood_pressure_systolic": ass.blood_pressure_systolic,
                "blood_pressure_diastolic": ass.blood_pressure_diastolic,
                "temperature": ass.temperature,
                "pulse_rate": ass.pulse_rate,
                "oxygen_saturation": ass.oxygen_saturation,
                "respiratory_rate": ass.respiratory_rate,
                "weight_kg": ass.weight_kg
            }

        response_list.append(
            {
                "triage_id": ass.triage_id if ass else None,
                "visit_id": v.visit_id,
                "patient": {
                    "patient_id": p.id,
                    "full_name": p.full_name,
                    "date_of_birth": p.date_of_birth,
                    "gender": p.gender
                },
                "triage_nurse": nurse_data,
                "chief_complaint": ass.chief_complaint if ass else None,
                "complaint_code": ass.complaint_code if ass else None,
                "triage_category": ass.triage_category if ass else None,
                "triage_notes": ass.triage_notes if ass else None,
                "assessed_at": ass.assessed_at if ass else None,
                "vitals": vitals_data,
                "visit_date": v.visit_date,
                "visit_status": raw_status
            }
        )

    return response_list

