from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.visits.schemas import (
    QueueTodayResponse,
    VisitCreateRequest,
    VisitCreateResponse,
    TriageCompleteRequest,
)
from app.core.security import require_role, require_any_role
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.models.visit import Queue
from app.services.visit_service import (
    create_visit,
    complete_triage_and_enqueue_doctor,
    get_ordered_doctor_queue,
)

router = APIRouter(prefix="/visits", tags=["visits"])


@router.post("", response_model=VisitCreateResponse, status_code=status.HTTP_201_CREATED)
def create(
    body: VisitCreateRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    try:
        registered_by = payload.get("sub", "")
        result = create_visit(
            db=db,
            hospital_id=tenant_id,
            patient_id=body.patient_id,
            visit_type=body.visit_type,
            payment_type=body.payment_type,
            registered_by=registered_by,
            insurer_name=body.insurer_name,
            policy_number=body.policy_number,
        )
        return VisitCreateResponse(
            visit=result["visit"],
            queue_number=result["queue_number"],
            verification_flag=result["verification_flag"],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/queues/triage/today", response_model=list[QueueTodayResponse])
def triage_queue_today(
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    today = date.today()
    queues = (
        db.query(Queue)
        .filter(
            Queue.queue_type == "triage",
            Queue.created_at >= today,
        )
        .order_by(Queue.created_at.asc())
        .all()
    )
    return queues


@router.post("/{visit_id}/triage-complete", status_code=status.HTTP_200_OK)
def complete_triage(
    visit_id: str,
    body: TriageCompleteRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "nurse"])),
):
    try:
        result = complete_triage_and_enqueue_doctor(
            db=db,
            visit_id=visit_id,
            priority=body.priority,
        )
        return {
            "status": "success",
            "visit_id": result["visit"].visit_id,
            "visit_status": result["visit"].status,
            "queue_number": result["queue_number"]
        }
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.get("/queues/doctor/today", response_model=list[QueueTodayResponse])
def doctor_queue_today(
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "doctor", "nurse"])),
):
    queues = get_ordered_doctor_queue(db)
    return queues

