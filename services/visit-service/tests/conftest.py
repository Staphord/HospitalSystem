import uuid

import pytest
from sqlalchemy import Column, String, UUID, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.models.visit import PatientInsurance


class MockPatient(Base):
    __tablename__ = "patients"
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hospital_id = Column(String(50), nullable=False)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def sample_patient(db_session):
    pat = MockPatient(id=uuid.uuid4(), hospital_id="hosp-001")
    db_session.add(pat)
    db_session.commit()
    return pat


@pytest.fixture
def sample_patient_id(sample_patient):
    return sample_patient.id


@pytest.fixture
def verified_insurance(db_session, sample_patient_id):
    from datetime import date, timedelta

    ins = PatientInsurance(
        insurance_id=uuid.uuid4(),
        patient_id=sample_patient_id,
        insurer_name="TestInsurer",
        policy_number="POL-001",
        coverage_limit=100000.00,
        expiry_date=date.today() + timedelta(days=365),
        verification_status="verified",
        is_active=True,
    )
    db_session.add(ins)
    db_session.commit()
    return ins


@pytest.fixture
def pending_insurance(db_session, sample_patient_id):
    from datetime import date, timedelta

    ins = PatientInsurance(
        insurance_id=uuid.uuid4(),
        patient_id=sample_patient_id,
        insurer_name="PendingInsurer",
        policy_number="POL-002",
        coverage_limit=50000.00,
        expiry_date=date.today() + timedelta(days=365),
        verification_status="pending",
        is_active=True,
    )
    db_session.add(ins)
    db_session.commit()
    return ins


@pytest.fixture
def expired_insurance(db_session, sample_patient_id):
    from datetime import date, timedelta

    ins = PatientInsurance(
        insurance_id=uuid.uuid4(),
        patient_id=sample_patient_id,
        insurer_name="ExpiredInsurer",
        policy_number="POL-003",
        coverage_limit=50000.00,
        expiry_date=date.today() - timedelta(days=1),
        verification_status="verified",
        is_active=True,
    )
    db_session.add(ins)
    db_session.commit()
    return ins
