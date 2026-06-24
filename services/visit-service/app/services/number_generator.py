from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.orm import Session


def _is_sqlite(db: Session) -> bool:
    return db.get_bind().dialect.name == "sqlite"


def generate_visit_number(db: Session) -> str:
    now = datetime.now(timezone.utc)
    date_key = now.strftime("%Y%m%d")

    if _is_sqlite(db):
        existing = db.execute(
            text("SELECT counter FROM visit_number_sequences WHERE date_key = :d"),
            {"d": date_key},
        ).scalar()

        if existing is None:
            db.execute(
                text("INSERT INTO visit_number_sequences (date_key, counter) VALUES (:d, 1)"),
                {"d": date_key},
            )
            counter = 1
        else:
            db.execute(
                text("UPDATE visit_number_sequences SET counter = counter + 1 WHERE date_key = :d"),
                {"d": date_key},
            )
            counter = existing + 1
        db.commit()
    else:
        result = db.execute(
            text("""
                INSERT INTO visit_number_sequences (date_key, counter)
                VALUES (:date_key, 1)
                ON CONFLICT (date_key)
                DO UPDATE SET counter = visit_number_sequences.counter + 1
                RETURNING counter
            """),
            {"date_key": date_key},
        )
        counter = result.scalar()
        db.commit()

    return f"VIS-{date_key}-{counter:04d}"


def generate_queue_number(db: Session, queue_type: str) -> str:
    now = datetime.now(timezone.utc)
    date_key = now.strftime("%Y%m%d")

    if _is_sqlite(db):
        existing = db.execute(
            text("SELECT counter FROM queue_number_sequences WHERE queue_type = :qt AND date_key = :d"),
            {"qt": queue_type, "d": date_key},
        ).scalar()

        if existing is None:
            db.execute(
                text("INSERT INTO queue_number_sequences (queue_type, date_key, counter) VALUES (:qt, :d, 1)"),
                {"qt": queue_type, "d": date_key},
            )
            counter = 1
        else:
            db.execute(
                text("UPDATE queue_number_sequences SET counter = counter + 1 WHERE queue_type = :qt AND date_key = :d"),
                {"qt": queue_type, "d": date_key},
            )
            counter = existing + 1
        db.commit()
    else:
        result = db.execute(
            text("""
                INSERT INTO queue_number_sequences (queue_type, date_key, counter)
                VALUES (:queue_type, :date_key, 1)
                ON CONFLICT (queue_type, date_key)
                DO UPDATE SET counter = queue_number_sequences.counter + 1
                RETURNING counter
            """),
            {"queue_type": queue_type, "date_key": date_key},
        )
        counter = result.scalar()
        db.commit()

    prefix = queue_type[:1].upper()
    return f"{prefix}-{counter:03d}"
