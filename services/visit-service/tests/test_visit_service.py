import uuid
from datetime import date

import pytest
from sqlalchemy.orm import Session

from app.models.visit import Queue, Visit
from app.services.visit_service import create_visit


def test_create_visit_cash(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    assert result["visit"].visit_number.startswith("VIS-")
    assert result["visit"].payment_type == "cash"
    assert result["visit"].insurance_id is None
    assert result["verification_flag"] is None
    assert result["queue_number"].startswith("T-")

    q = db_session.query(Queue).filter(Queue.visit_id == result["visit"].visit_id).first()
    assert q is not None
    assert q.queue_type == "triage"
    assert q.queue_number == result["queue_number"]
    assert q.status == "waiting"


def test_create_visit_verified_insurance(
    db_session: Session,
    sample_patient_id: str,
    verified_insurance,
):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="emergency",
        payment_type="insurance",
        registered_by=str(uuid.uuid4()),
        insurer_name="TestInsurer",
        policy_number="POL-001",
    )

    assert result["visit"].insurance_id == verified_insurance.insurance_id
    assert result["verification_flag"] is None
    assert result["visit"].status == "registered"

    q = db_session.query(Queue).filter(Queue.visit_id == result["visit"].visit_id).first()
    assert q is not None


def test_create_visit_pending_insurance(
    db_session: Session,
    sample_patient_id: str,
    pending_insurance,
):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="inpatient",
        payment_type="insurance",
        registered_by=str(uuid.uuid4()),
        insurer_name="PendingInsurer",
        policy_number="POL-002",
    )

    assert result["visit"].insurance_id == pending_insurance.insurance_id
    assert result["verification_flag"] == "manual_review_required"


def test_create_visit_expired_insurance(
    db_session: Session,
    sample_patient_id: str,
    expired_insurance,
):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="insurance",
        registered_by=str(uuid.uuid4()),
        insurer_name="ExpiredInsurer",
        policy_number="POL-003",
    )

    assert result["visit"].insurance_id == expired_insurance.insurance_id
    assert result["verification_flag"] == "Policy has expired"


def test_create_visit_insurance_no_policy(
    db_session: Session,
    sample_patient_id: str,
):
    with pytest.raises(ValueError, match="No active insurance policy found"):
        create_visit(
            db=db_session,
            hospital_id="hosp-001",
            patient_id=sample_patient_id,
            visit_type="outpatient",
            payment_type="insurance",
            registered_by=str(uuid.uuid4()),
            insurer_name="UnknownInsurer",
            policy_number="POL-999",
        )


def test_create_visit_insurance_missing_fields(
    db_session: Session,
    sample_patient_id: str,
):
    with pytest.raises(ValueError, match="insurer_name and policy_number are required"):
        create_visit(
            db=db_session,
            hospital_id="hosp-001",
            patient_id=sample_patient_id,
            visit_type="outpatient",
            payment_type="insurance",
            registered_by=str(uuid.uuid4()),
            insurer_name=None,
            policy_number=None,
        )


def test_visit_number_format(db_session: Session, sample_patient_id: str):
    from app.services.number_generator import generate_visit_number

    vn = generate_visit_number(db_session)
    assert vn.startswith("VIS-")
    parts = vn.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert parts[1].isdigit()
    assert parts[2].isdigit()
    assert len(parts[2]) == 4


def test_visit_number_increment(db_session: Session):
    from app.services.number_generator import generate_visit_number

    vn1 = generate_visit_number(db_session)
    vn2 = generate_visit_number(db_session)
    seq1 = int(vn1.split("-")[2])
    seq2 = int(vn2.split("-")[2])
    assert seq2 == seq1 + 1


def test_queue_number_format(db_session: Session):
    from app.services.number_generator import generate_queue_number

    qn = generate_queue_number(db_session, "triage")
    assert qn.startswith("T-")
    seq = int(qn.split("-")[1])
    assert seq == 1


def test_queue_number_increment(db_session: Session):
    from app.services.number_generator import generate_queue_number

    qn1 = generate_queue_number(db_session, "triage")
    qn2 = generate_queue_number(db_session, "triage")
    seq1 = int(qn1.split("-")[1])
    seq2 = int(qn2.split("-")[1])
    assert seq2 == seq1 + 1


def test_queue_number_per_type(db_session: Session):
    from app.services.number_generator import generate_queue_number

    qn1 = generate_queue_number(db_session, "triage")
    qn2 = generate_queue_number(db_session, "doctor")
    assert qn1.startswith("T-")
    assert qn2.startswith("D-")
