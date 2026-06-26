import uuid
from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.visit import Visit, Queue
from app.services.insurance_service import find_insurance_policy, verify_insurance
from app.services.number_generator import generate_queue_number, generate_visit_number


def create_visit(
    db: Session,
    hospital_id: str,
    patient_id: str,
    visit_type: str,
    payment_type: str,
    registered_by: str,
    insurer_name: Optional[str] = None,
    policy_number: Optional[str] = None,
) -> dict:
    visit_number = generate_visit_number(db)

    patient_uuid = uuid.UUID(patient_id) if isinstance(patient_id, str) else patient_id
    registered_by_uuid = uuid.UUID(registered_by) if isinstance(registered_by, str) else registered_by

    insurance_id = None
    verification_flag = None

    if payment_type == "insurance":
        if not insurer_name or not policy_number:
            raise ValueError("insurer_name and policy_number are required when payment_type is insurance")

        policy = find_insurance_policy(db, str(patient_uuid), insurer_name, policy_number)

        if policy is None:
            raise ValueError(
                f"No active insurance policy found for patient {patient_id} "
                f"with insurer '{insurer_name}' and policy '{policy_number}'"
            )

        insurance_id = policy.insurance_id
        is_verified, reason = verify_insurance(policy)

        if is_verified:
            verification_flag = None
        else:
            verification_flag = reason

    visit = Visit(
        patient_id=patient_uuid,
        visit_number=visit_number,
        visit_date=date.today(),
        visit_type=visit_type,
        payment_type=payment_type,
        insurance_id=insurance_id,
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

    return {
        "visit": visit,
        "queue_number": queue_number,
        "verification_flag": verification_flag,
    }
