from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _is_sqlite(db: Session) -> bool:
    dialect = db.get_bind().dialect.name
    return dialect == "sqlite"


def generate_patient_number(db: Session, hospital_id: str) -> str:
    now = datetime.now(timezone.utc)
    date_key = now.strftime("%Y%m%d")

    if _is_sqlite(db):
        existing = db.execute(
            text("SELECT counter FROM patient_number_sequences WHERE hospital_id = :h AND date_key = :d"),
            {"h": hospital_id, "d": date_key},
        ).scalar()

        if existing is None:
            db.execute(
                text("INSERT INTO patient_number_sequences (hospital_id, date_key, counter) VALUES (:h, :d, 1)"),
                {"h": hospital_id, "d": date_key},
            )
            counter = 1
        else:
            db.execute(
                text("UPDATE patient_number_sequences SET counter = counter + 1 WHERE hospital_id = :h AND date_key = :d"),
                {"h": hospital_id, "d": date_key},
            )
            counter = existing + 1
        db.commit()
    else:
        result = db.execute(
            text("""
                INSERT INTO patient_number_sequences (hospital_id, date_key, counter)
                VALUES (:hospital_id, :date_key, 1)
                ON CONFLICT (hospital_id, date_key)
                DO UPDATE SET counter = patient_number_sequences.counter + 1
                RETURNING counter
            """),
            {"hospital_id": hospital_id, "date_key": date_key},
        )
        counter = result.scalar()
        db.commit()

    return f"PT-{date_key}-{counter:04d}"
