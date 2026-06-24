from datetime import date
from typing import Optional

from sqlalchemy.orm import Session

from app.models.visit import PatientInsurance


def find_insurance_policy(
    db: Session,
    patient_id: str,
    insurer_name: str,
    policy_number: str,
) -> Optional[PatientInsurance]:
    return (
        db.query(PatientInsurance)
        .filter(
            PatientInsurance.patient_id == patient_id,
            PatientInsurance.insurer_name == insurer_name,
            PatientInsurance.policy_number == policy_number,
            PatientInsurance.is_active == True,
        )
        .first()
    )


def verify_insurance(policy: PatientInsurance) -> tuple[bool, Optional[str]]:
    if policy.verification_status == "verified":
        if policy.coverage_limit is not None and policy.coverage_limit <= 0:
            return False, "Coverage limit exhausted"
        if policy.expiry_date and policy.expiry_date < date.today():
            return False, "Policy has expired"
        return True, None

    if policy.verification_status == "pending":
        return False, "manual_review_required"

    if policy.verification_status == "rejected":
        return False, "Policy verification was rejected"

    return False, "manual_review_required"
