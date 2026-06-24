from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.patient import TenantPatient
from app.services.patient_service import (
    delete_patient,
    get_patient_by_id,
    get_patient_count,
    register_patient,
    search_patients,
)


def test_register_patient(db_session):
    patient = register_patient(
        db=db_session,
        hospital_id="hosp-001",
        full_name="John Doe",
        date_of_birth=date(1990, 1, 15),
        gender="male",
        phone="+123456789",
        email="john@example.com",
        national_id="NAT-001",
        created_by="admin",
    )
    assert patient.id is not None
    assert patient.patient_number.startswith("PT-")
    assert patient.full_name == "John Doe"
    assert patient.gender == "male"


def test_register_patient_minimal_fields(db_session):
    patient = register_patient(
        db=db_session,
        hospital_id="hosp-001",
        full_name="Jane Smith",
        date_of_birth=date(1985, 5, 20),
        gender="female",
    )
    assert patient.patient_number is not None
    assert patient.full_name == "Jane Smith"


def test_search_by_name(db_session):
    register_patient(db_session, "hosp-001", "Alice Tan", date(1990, 1, 1), "female")
    register_patient(db_session, "hosp-001", "Bob Tan", date(1992, 3, 3), "male")
    register_patient(db_session, "hosp-001", "Charlie Brown", date(1980, 7, 7), "male")

    results = search_patients(db_session, "hosp-001", query="Tan")
    assert len(results) == 2

    results = search_patients(db_session, "hosp-001", query="Charlie")
    assert len(results) == 1
    assert results[0].full_name == "Charlie Brown"


def test_search_by_national_id(db_session):
    register_patient(
        db_session, "hosp-001", "Alice", date(1990, 1, 1), "female",
        national_id="NAT-ALICE",
    )
    results = search_patients(db_session, "hosp-001", national_id="NAT-ALICE")
    assert len(results) == 1
    assert results[0].full_name == "Alice"


def test_search_by_patient_number(db_session):
    patient = register_patient(
        db_session, "hosp-001", "Bob", date(1992, 3, 3), "male",
    )
    results = search_patients(db_session, "hosp-001", patient_number=patient.patient_number)
    assert len(results) == 1
    assert results[0].id == patient.id


def test_search_scoped_to_hospital(db_session):
    p1 = register_patient(db_session, "hosp-001", "Alice", date(1990, 1, 1), "female")
    p2 = register_patient(db_session, "hosp-002", "Alice", date(1990, 1, 1), "female")
    results = search_patients(db_session, "hosp-001", query="Alice")
    assert len(results) == 1
    assert results[0].id == p1.id


def test_get_patient_by_id(db_session):
    patient = register_patient(db_session, "hosp-001", "Alice", date(1990, 1, 1), "female")
    found = get_patient_by_id(db_session, "hosp-001", str(patient.id))
    assert found is not None
    assert found.id == patient.id

    not_found = get_patient_by_id(db_session, "hosp-001", "00000000-0000-0000-0000-000000000000")
    assert not_found is None


def test_delete_patient(db_session):
    patient = register_patient(db_session, "hosp-001", "ToDelete", date(2000, 1, 1), "male")
    pid = str(patient.id)
    assert get_patient_by_id(db_session, "hosp-001", pid) is not None
    assert delete_patient(db_session, "hosp-001", pid) is True
    assert get_patient_by_id(db_session, "hosp-001", pid) is None


def test_delete_patient_not_found(db_session):
    assert delete_patient(db_session, "hosp-001", "00000000-0000-0000-0000-000000000000") is False


def test_get_patient_count(db_session):
    assert get_patient_count(db_session, "hosp-001") == 0
    register_patient(db_session, "hosp-001", "Alice", date(1990, 1, 1), "female")
    assert get_patient_count(db_session, "hosp-001") == 1
    register_patient(db_session, "hosp-001", "Bob", date(1992, 3, 3), "male")
    assert get_patient_count(db_session, "hosp-001") == 2
