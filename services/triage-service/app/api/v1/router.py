import httpx
from fastapi import APIRouter, Depends, HTTPException, status, Request, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, cast
from sqlalchemy.dialects.postgresql import UUID as pgUUID
from typing import Optional
from uuid import UUID

from app.core.tenant_auth import get_current_tenant
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
    TriageSummaryResponse
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
    status_code=status.HTTP_201_CREATED
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


@router.post("/assessments/suggest-category", response_model=TriageCategorySuggestionResponse)
def suggest_category(payload: VitalsInput):
    """
    Calculate and suggest a triage category based on recorded vital signs.
    """
    category, reason = suggest_category_from_vitals(payload.model_dump(exclude_unset=True))
    return TriageCategorySuggestionResponse(
        suggested_category=category,
        reason=reason
    )


@router.get("/assessments/{visit_id}", response_model=TriageSummaryResponse)
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


@router.get("/queue", response_model=TriageQueueResponse)
async def get_triage_queue(
    status: Optional[str] = Query("waiting,in_progress"),
    db: AsyncSession = Depends(get_tenant_db),
    current_user: TokenPayload = Depends(require_role("triage_nurse"))
):
    """
    Returns the list of patients currently in the triage queue, ordered by clinical priority and arrival.
    """
    from app.models.triage import Queue, Visit, Patient
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
        select(Queue, Visit, Patient)
        .join(Visit, Queue.visit_id == Visit.visit_id)
        .join(Patient, Patient.id == cast(Queue.patient_id, pgUUID))
        .where(Queue.queue_type == "triage")
        .where(Queue.status.in_(status_list))
        .order_by(priority_case, Queue.created_at.asc())
    )
    
    result = await db.execute(stmt)
    rows = result.all()
    
    queue_items = []
    for q, v, p in rows:
        queue_items.append(
            TriageQueueItem(
                queue_id=q.queue_id,
                queue_number=q.queue_number,
                priority=q.priority,
                status=q.status,
                called_at=q.called_at,
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


@router.patch("/queue/{queue_id}/call", response_model=QueueCallResponse)
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
    
    return QueueCallResponse(
        queue_id=updated_q.queue_id,
        status=updated_q.status,
        called_at=updated_q.called_at
    )


@router.patch("/queue/{queue_id}/skip", response_model=QueueSkipResponse)
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
    
    return QueueSkipResponse(
        queue_id=updated_q.queue_id,
        status=updated_q.status,
        completed_at=updated_q.completed_at
    )
