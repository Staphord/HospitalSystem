import uuid
from datetime import date, datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.visit import PatientInsurance


def find_insurance_policy(
    db: Session,
    patient_id: str,
    insurer_name: str,
    policy_number: str,
) -> Optional[PatientInsurance]:
    patient_uuid = uuid.UUID(patient_id) if isinstance(patient_id, str) else patient_id
    return (
        db.query(PatientInsurance)
        .filter(
            PatientInsurance.patient_id == patient_uuid,
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


def create_insurance_policy(
    db: Session,
    patient_id: str,
    insurer_name: str,
    policy_number: str,
    coverage_limit=None,
    expiry_date=None,
) -> PatientInsurance:
    """Create a new insurance policy for a patient with pending status."""
    patient_uuid = uuid.UUID(patient_id) if isinstance(patient_id, str) else patient_id
    policy = PatientInsurance(
        patient_id=patient_uuid,
        insurer_name=insurer_name,
        policy_number=policy_number,
        coverage_limit=coverage_limit,
        expiry_date=expiry_date,
        verification_status="pending",
        verified_at=None,
        is_active=True,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return policy


def get_patient_policies(
    db: Session,
    patient_id: str,
) -> list[PatientInsurance]:
    """Return all insurance policies for a patient, newest first."""
    patient_uuid = uuid.UUID(patient_id) if isinstance(patient_id, str) else patient_id
    return (
        db.query(PatientInsurance)
        .filter(PatientInsurance.patient_id == patient_uuid)
        .order_by(PatientInsurance.created_at.desc())
        .all()
    )


def update_verification_status(
    db: Session,
    insurance_id: str,
    verification_status: str,
) -> Optional[PatientInsurance]:
    """Record the manual verification outcome for an insurance policy."""
    ins_uuid = uuid.UUID(insurance_id) if isinstance(insurance_id, str) else insurance_id
    policy = (
        db.query(PatientInsurance)
        .filter(PatientInsurance.insurance_id == ins_uuid)
        .first()
    )
    if not policy:
        return None
    policy.verification_status = verification_status
    policy.verified_at = datetime.utcnow()
    db.commit()
    db.refresh(policy)
    return policy
