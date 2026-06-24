from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.visits.schemas import (
    QueueTodayResponse,
    VisitCreateRequest,
    VisitCreateResponse,
)
from app.core.security import require_role
from app.dependencies import get_tenant_db, get_tenant_id_from_token
from app.models.visit import Queue
from app.services.visit_service import create_visit

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
