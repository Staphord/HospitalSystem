import uuid
from datetime import date, datetime, timezone

import pytest
from sqlalchemy.orm import Session

from app.models.visit import Queue, Visit
from app.services.queue_service import (
    add_to_queue,
    call_next_in_queue,
    get_queue,
    update_queue_status,
)
from app.services.visit_service import create_visit
from app.services.visit_status_service import transition_visit_status


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


def test_visit_status_transition(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )
    visit = result["visit"]
    assert visit.status == "registered"

    t1 = transition_visit_status(db_session, str(visit.visit_id), "triaged")
    assert t1.status == "triaged"

    t2 = transition_visit_status(db_session, str(visit.visit_id), "in_consultation")
    assert t2.status == "in_consultation"

    t3 = transition_visit_status(db_session, str(visit.visit_id), "completed")
    assert t3.status == "completed"


def test_visit_status_invalid_transition(db_session: Session, sample_patient_id: str):
    from fastapi import HTTPException

    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException, match="Cannot transition visit"):
        transition_visit_status(db_session, str(result["visit"].visit_id), "completed")


def test_call_next_in_queue(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    called = call_next_in_queue(db_session, "triage")
    assert called is not None
    assert called.status == "in_progress"
    assert called.called_at is not None


def test_call_next_empty_queue(db_session: Session):
    result = call_next_in_queue(db_session, "doctor")
    assert result is None


def test_update_queue_status(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    queue = db_session.query(Queue).filter(
        Queue.visit_id == result["visit"].visit_id
    ).first()

    started = update_queue_status(db_session, str(queue.queue_id), "in_progress")
    assert started.status == "in_progress"
    assert started.called_at is not None

    completed = update_queue_status(db_session, str(queue.queue_id), "completed")
    assert completed.status == "completed"
    assert completed.completed_at is not None


def test_add_to_queue(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    queue_entry = add_to_queue(
        db_session,
        visit_id=str(result["visit"].visit_id),
        patient_id=sample_patient_id,
        queue_type="doctor",
        priority="urgent",
    )
    assert queue_entry.queue_type == "doctor"
    assert queue_entry.priority == "urgent"
    assert queue_entry.status == "waiting"
    assert queue_entry.queue_number.startswith("D-")


def test_add_to_queue_duplicate(db_session: Session, sample_patient_id: str):
    from fastapi import HTTPException

    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    add_to_queue(
        db_session,
        visit_id=str(result["visit"].visit_id),
        patient_id=sample_patient_id,
        queue_type="doctor",
    )

    with pytest.raises(HTTPException, match="already has an active"):
        add_to_queue(
            db_session,
            visit_id=str(result["visit"].visit_id),
            patient_id=sample_patient_id,
            queue_type="doctor",
        )


def test_get_queue_by_type(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    queues = get_queue(db_session, "triage", limit=10)
    assert len(queues) >= 1
    assert all(q.queue_type == "triage" for q in queues)


def test_get_queue_with_status_filter(db_session: Session, sample_patient_id: str):
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )

    waiting = get_queue(db_session, "triage", status_filter="waiting")
    assert len(waiting) >= 1

    completed = get_queue(db_session, "triage", status_filter="completed")
    assert len(completed) == 0


def test_complete_triage_and_enqueue_doctor(db_session: Session, sample_patient_id: str):
    # 1. Create a visit first
    result = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )
    visit = result["visit"]

    # 2. Verify triage queue is waiting
    q_triage = db_session.query(Queue).filter(
        Queue.visit_id == visit.visit_id,
        Queue.queue_type == "triage"
    ).first()
    assert q_triage is not None
    assert q_triage.status == "waiting"

    # 3. Complete triage and enqueue doctor
    from app.services.visit_service import complete_triage_and_enqueue_doctor
    triage_result = complete_triage_and_enqueue_doctor(
        db=db_session,
        visit_id=visit.visit_id,
        priority="urgent"
    )

    # 4. Assertions
    assert triage_result["visit"].status == "triaged"
    assert triage_result["queue_number"].startswith("D-")

    # Verify triage queue is completed
    db_session.refresh(q_triage)
    assert q_triage.status == "completed"

    # Verify doctor queue is waiting and urgent
    q_doctor = db_session.query(Queue).filter(
        Queue.visit_id == visit.visit_id,
        Queue.queue_type == "doctor"
    ).first()
    assert q_doctor is not None
    assert q_doctor.status == "waiting"
    assert q_doctor.priority == "urgent"
    assert q_doctor.queue_number == triage_result["queue_number"]


def test_doctor_queue_priority_ordering(db_session: Session, sample_patient_id: str):
    from app.services.visit_service import complete_triage_and_enqueue_doctor, get_ordered_doctor_queue

    # Create 3 visits
    # Visit 1: non_urgent
    v1 = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )["visit"]

    # Visit 2: emergency
    v2 = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )["visit"]

    # Visit 3: urgent
    v3 = create_visit(
        db=db_session,
        hospital_id="hosp-001",
        patient_id=sample_patient_id,
        visit_type="outpatient",
        payment_type="cash",
        registered_by=str(uuid.uuid4()),
    )["visit"]

    # Complete triage in different orders
    complete_triage_and_enqueue_doctor(db_session, visit_id=v1.visit_id, priority="non_urgent")
    complete_triage_and_enqueue_doctor(db_session, visit_id=v3.visit_id, priority="urgent")
    complete_triage_and_enqueue_doctor(db_session, visit_id=v2.visit_id, priority="emergency")

    # Get ordered queue
    ordered = get_ordered_doctor_queue(db_session)
    assert len(ordered) == 3

    # Order must be: emergency (v2) -> urgent (v3) -> non_urgent (v1)
    assert ordered[0].visit_id == v2.visit_id
    assert ordered[1].visit_id == v3.visit_id
    assert ordered[2].visit_id == v1.visit_id
