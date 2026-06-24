from sqlalchemy import text

from app.services.patient_number import generate_patient_number


def test_generate_patient_number_format(db_session):
    pn = generate_patient_number(db_session, "hosp-001")
    assert pn.startswith("PT-")
    parts = pn.split("-")
    assert len(parts) == 3
    assert len(parts[1]) == 8
    assert parts[1].isdigit()
    assert parts[2].isdigit()
    assert len(parts[2]) == 4


def test_generate_patient_number_increment(db_session):
    pn1 = generate_patient_number(db_session, "hosp-001")
    pn2 = generate_patient_number(db_session, "hosp-001")
    seq1 = int(pn1.split("-")[2])
    seq2 = int(pn2.split("-")[2])
    assert seq2 == seq1 + 1


def test_generate_patient_number_per_hospital(db_session):
    pn1 = generate_patient_number(db_session, "hosp-001")
    pn2 = generate_patient_number(db_session, "hosp-002")
    assert pn1.split("-")[2] == "0001"
    assert pn2.split("-")[2] == "0001"


def test_generate_sequence_column_exists(db_session):
    db_session.execute(text("""
        CREATE TABLE IF NOT EXISTS patient_number_sequences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hospital_id VARCHAR(50) NOT NULL,
            date_key VARCHAR(8) NOT NULL,
            counter INTEGER NOT NULL DEFAULT 0,
            UNIQUE(hospital_id, date_key)
        )
    """))
    db_session.commit()
    pn = generate_patient_number(db_session, "hosp-003")
    assert pn.startswith("PT-")
