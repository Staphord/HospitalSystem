"""Hospital settings key/value store (admin configuration extras)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.models.admin import HospitalSetting
from app.services import audit_service


def ensure_settings_table(db: Session) -> None:
    """Create hospital_settings if tenant DB has not been migrated yet."""
    try:
        bind = db.get_bind()
        inspector = inspect(bind)
        if "hospital_settings" in inspector.get_table_names():
            return
        db.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS hospital_settings (
                    key VARCHAR(100) PRIMARY KEY,
                    value TEXT,
                    updated_by VARCHAR(255),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def list_settings(db: Session) -> list[HospitalSetting]:
    ensure_settings_table(db)
    return db.query(HospitalSetting).order_by(HospitalSetting.key).all()


def upsert_settings(
    db: Session,
    settings: dict[str, str | None],
    *,
    actor_sub: str,
    ip: str | None = None,
) -> list[HospitalSetting]:
    ensure_settings_table(db)
    now = datetime.now(timezone.utc)
    changed: dict[str, str | None] = {}
    for key, value in settings.items():
        if not key or len(key) > 100:
            continue
        row = db.query(HospitalSetting).filter(HospitalSetting.key == key).first()
        if row:
            if row.value != value:
                changed[key] = value
            row.value = value
            row.updated_by = actor_sub
            row.updated_at = now
        else:
            changed[key] = value
            db.add(
                HospitalSetting(
                    key=key,
                    value=value,
                    updated_by=actor_sub,
                    updated_at=now,
                )
            )
    db.commit()
    if changed:
        audit_service.log_change(
            db,
            user_id=actor_sub,
            action="UPDATE",
            table_name="hospital_settings",
            record_id="bulk",
            new_values=changed,
            ip_address=ip,
        )
    return list_settings(db)
