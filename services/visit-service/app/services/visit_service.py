import uuid
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.visit import Visit, Queue, PatientInsurance
from app.services.insurance_service import verify_insurance
from app.services.number_generator import generate_queue_number, generate_visit_number


def create_visit(
    db: Session,
    hospital_id: str,
    patient_id: str,
    visit_type: str,
    payment_type: str,
    registered_by: str,
    insurance_id: Optional[str] = None,
) -> dict:
    """Create a visit and auto-assign a triage queue entry.

    For insurance payments, validates that the given insurance_id:
      - exists in the DB
      - belongs to the given patient_id
      - has is_active = True
    Does NOT block on verification_status (pending/rejected are accepted).
    """
    visit_number = generate_visit_number(db)

    patient_uuid = uuid.UUID(patient_id) if isinstance(patient_id, str) else patient_id
    registered_by_uuid = uuid.UUID(registered_by) if isinstance(registered_by, str) else registered_by

    resolved_insurance_id = None
    verification_flag = None

    if payment_type == "insurance":
        if not insurance_id:
            raise ValueError("insurance_id is required when payment_type is 'insurance'")

        ins_uuid = uuid.UUID(str(insurance_id))
        policy = (
            db.query(PatientInsurance)
            .filter(
                PatientInsurance.insurance_id == ins_uuid,
                PatientInsurance.patient_id == patient_uuid,
                PatientInsurance.is_active == True,
            )
            .first()
        )

        if policy is None:
            raise ValueError(
                f"insurance_id '{insurance_id}' not found, does not belong to patient "
                f"'{patient_id}', or is not active"
            )

        resolved_insurance_id = policy.insurance_id
        _, reason = verify_insurance(policy)
        verification_flag = reason  # None if verified, otherwise a flag string

    visit = Visit(
        patient_id=patient_uuid,
        visit_number=visit_number,
        visit_date=date.today(),
        visit_type=visit_type,
        payment_type=payment_type,
        insurance_id=resolved_insurance_id,
        verification_flag=verification_flag,
        status="registered",
        registered_by=registered_by_uuid,
    )
    db.add(visit)
    db.flush()

    queue_number = generate_queue_number(db, "triage")
    queue = Queue(
        visit_id=visit.visit_id,
        patient_id=patient_uuid,
        queue_type="triage",
        queue_number=queue_number,
        priority="non_urgent",
        status="waiting",
    )
    db.add(queue)

    visit.queue_number = queue_number
    db.commit()
    db.refresh(visit)
    db.refresh(queue)

    return {
        "visit": visit,
        "queue": queue,
        "queue_number": queue_number,
        "verification_flag": verification_flag,
    }


def complete_triage_and_enqueue_doctor(
    db: Session,
    visit_id: str,
    priority: str,
) -> dict:
    from sqlalchemy import func

    # 1. Fetch the visit
    visit = db.query(Visit).filter(Visit.visit_id == visit_id).first()
    if not visit:
        raise ValueError(f"Visit with ID {visit_id} not found")

    # Update visit status
    visit.status = "triaged"

    # 2. Complete current triage queue entry
    triage_queue = db.query(Queue).filter(
        Queue.visit_id == visit_id,
        Queue.queue_type == "triage",
        Queue.status.in_(["waiting", "in_progress"])
    ).first()
    if triage_queue:
        triage_queue.status = "completed"

    # 3. Create or update doctor queue entry
    queue_number = generate_queue_number(db, "doctor")

    existing_doctor_queue = db.query(Queue).filter(
        Queue.visit_id == visit_id,
        Queue.queue_type == "doctor"
    ).first()

    if existing_doctor_queue:
        queue = existing_doctor_queue
        queue.priority = priority.lower()
        queue.status = "waiting"
    else:
        queue = Queue(
            visit_id=visit_id,
            patient_id=visit.patient_id,
            queue_type="doctor",
            queue_number=queue_number,
            priority=priority.lower(),
            status="waiting",
        )
        db.add(queue)

    db.commit()
    db.refresh(visit)
    return {
        "visit": visit,
        "queue_number": queue.queue_number,
    }


def get_ordered_doctor_queue(
    db: Session,
) -> list[Queue]:
    from sqlalchemy import case
    today = date.today()

    priority_order = case(
        (Queue.priority == "emergency", 1),
        (Queue.priority == "urgent", 2),
        (Queue.priority == "semi_urgent", 3),
        (Queue.priority == "non_urgent", 4),
        else_=5
    )

    queues = (
        db.query(Queue)
        .filter(
            Queue.queue_type == "doctor",
            Queue.status == "waiting",
            Queue.created_at >= today
        )
        .order_by(
            priority_order.asc(),
            Queue.created_at.asc()
        )
        .all()
    )
    return queues

