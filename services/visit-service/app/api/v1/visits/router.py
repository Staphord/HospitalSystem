import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.visits.schemas import (
    QueueAddRequest,
    QueueCallResponse,
    QueueListResponse,
    QueueStatusUpdateRequest,
    QueueTodayResponse,
    VisitCreateRequest,
    VisitCreateResponse,
    VisitResponse,
    VisitStatusUpdateRequest,
    TriageCompleteRequest,
)
from app.core.security import require_role, require_any_role
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.models.visit import Queue, Visit
from app.services.queue_service import (
    add_to_queue,
    call_next_in_queue,
    get_queue,
    update_queue_status,
)
from app.services.visit_status_service import get_visit_by_id, transition_visit_status

from app.services.visit_service import (
    create_visit,
    complete_triage_and_enqueue_doctor,
    get_ordered_doctor_queue,
)

logger = logging.getLogger("visit_service.router")

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
    except Exception as e:
        logger.exception("Visit creation failed for patient %s", body.patient_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Visit creation failed: {str(e)}",
        )


@router.get("/{visit_id}", response_model=VisitResponse)
def get_visit(
    visit_id: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    return get_visit_by_id(db, visit_id)


@router.patch("/{visit_id}/status", response_model=VisitResponse)
def update_visit_status(
    visit_id: str,
    body: VisitStatusUpdateRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_any_role(["hospital_admin", "doctor", "nurse"])),
):
    return transition_visit_status(db, visit_id, body.status)


@router.get("/queues/{queue_type}", response_model=list[QueueListResponse])
def list_queue(
    queue_type: str,
    status_filter: str = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    valid_types = {"triage", "doctor", "lab", "radiology", "pharmacy", "billing"}
    if queue_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"queue_type must be one of {valid_types}",
        )
    return get_queue(db, queue_type, status_filter=status_filter, limit=limit)


@router.get("/queues/{queue_type}/next", response_model=QueueCallResponse)
def call_next(
    queue_type: str,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    valid_types = {"triage", "doctor", "lab", "radiology", "pharmacy", "billing"}
    if queue_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"queue_type must be one of {valid_types}",
        )
    entry = call_next_in_queue(db, queue_type)
    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No waiting patients in '{queue_type}' queue",
        )
    return entry


@router.patch("/queues/{queue_id}/status", response_model=QueueListResponse)
def update_queue_entry_status(
    queue_id: str,
    body: QueueStatusUpdateRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    return update_queue_status(db, queue_id, body.status)


@router.post("/queues/add", response_model=QueueListResponse, status_code=status.HTTP_201_CREATED)
def add_patient_to_queue(
    body: QueueAddRequest,
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    return add_to_queue(
        db,
        visit_id=body.visit_id,
        patient_id=body.patient_id,
        queue_type=body.queue_type,
        priority=body.priority,
    )


@router.get("/queues/triage/today", response_model=list[QueueTodayResponse])
def triage_queue_today(
    db: Session = Depends(get_tenant_db),
    tenant_id: str = Depends(get_tenant_id_from_token),
    payload: dict = Depends(require_role("hospital_admin")),
):
    try:
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
    except Exception as e:
        logger.exception("Failed to fetch triage queue for tenant %s", tenant_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch triage queue: {str(e)}",
        )


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

