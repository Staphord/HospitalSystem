from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.visit import Queue, Visit
from app.services.number_generator import generate_queue_number


def call_next_in_queue(
    db: Session,
    queue_type: str,
) -> Optional[Queue]:
    entry = (
        db.query(Queue)
        .filter(
            Queue.queue_type == queue_type,
            Queue.status == "waiting",
        )
        .order_by(Queue.priority.asc(), Queue.created_at.asc())
        .first()
    )
    if not entry:
        return None
    entry.status = "in_progress"
    entry.called_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return entry


def update_queue_status(
    db: Session,
    queue_id: str,
    new_status: str,
) -> Queue:
    import uuid as uuid_mod
    try:
        qid = uuid_mod.UUID(queue_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid queue_id UUID")

    entry = db.query(Queue).filter(Queue.queue_id == qid).first()
    if not entry:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Queue entry not found")

    valid_transitions = {
        "waiting": ["in_progress", "skipped"],
        "in_progress": ["completed", "skipped"],
        "completed": [],
        "skipped": [],
    }

    allowed = valid_transitions.get(entry.status, [])
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot transition queue from '{entry.status}' to '{new_status}'",
        )

    entry.status = new_status
    if new_status == "in_progress" and not entry.called_at:
        entry.called_at = datetime.now(timezone.utc)
    if new_status in ["completed", "skipped"] and not entry.completed_at:
        entry.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return entry


def get_queue(
    db: Session,
    queue_type: str,
    status_filter: Optional[str] = None,
    limit: int = 50,
) -> list[Queue]:
    filters = [Queue.queue_type == queue_type]
    if status_filter:
        filters.append(Queue.status == status_filter)
    return (
        db.query(Queue)
        .filter(*filters)
        .order_by(Queue.priority.asc(), Queue.created_at.asc())
        .limit(limit)
        .all()
    )


def add_to_queue(
    db: Session,
    visit_id: str,
    patient_id: str,
    queue_type: str,
    priority: str = "non_urgent",
) -> Queue:
    import uuid as uuid_mod
    try:
        vid = uuid_mod.UUID(visit_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid visit_id UUID")

    visit = db.query(Visit).filter(Visit.visit_id == vid).first()
    if not visit:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Visit not found")

    existing = (
        db.query(Queue)
        .filter(
            Queue.visit_id == vid,
            Queue.queue_type == queue_type,
            Queue.status.in_(["waiting", "in_progress"]),
        )
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Patient already has an active '{queue_type}' queue entry",
        )

    queue_number = generate_queue_number(db, queue_type)
    if isinstance(patient_id, uuid_mod.UUID):
        patient_uuid = patient_id
    else:
        try:
            patient_uuid = uuid_mod.UUID(patient_id)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid patient_id UUID")

    entry = Queue(
        visit_id=vid,
        patient_id=patient_uuid,
        queue_type=queue_type,
        queue_number=queue_number,
        priority=priority,
        status="waiting",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
