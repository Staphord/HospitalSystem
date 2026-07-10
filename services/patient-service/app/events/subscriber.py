"""
Event Subscriber for Patient Service.

Consumes:
- visit.registration_failed: Deletes the patient record when downstream visit registry fails.
"""

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.core.database import get_session_local
from app.messaging.subscriber import start_consumer
from app.models.patient import TenantPatient

logger = logging.getLogger(__name__)


async def handle_registration_failed(patient_id: str, tenant_id: str) -> None:
    """Delete a half-registered patient profile if the downstream visit creation failed."""
    logger.info("Starting rollback compensating transaction for patient %s (tenant: %s)", patient_id, tenant_id)

    SessionLocal = get_session_local()
    db: Session = SessionLocal()
    try:
        # Patient service database uses a single shared patients table in hospital_master.
        # Query matching both patient UUID and hospital (tenant) id.
        patient = (
            db.query(TenantPatient)
            .filter(TenantPatient.id == patient_id, TenantPatient.hospital_id == tenant_id)
            .first()
        )

        if patient:
            db.delete(patient)
            db.commit()
            logger.info("Successfully deleted patient %s (tenant: %s) due to visit failure", patient_id, tenant_id)
        else:
            logger.warning("Patient %s not found for tenant %s during rollback cleanup", patient_id, tenant_id)
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to delete patient %s during rollback cleanup: %s", patient_id, exc)
    finally:
        db.close()


async def _dispatch(routing_key: str, payload: dict[str, Any]) -> None:
    if routing_key == "visit.registration_failed":
        patient_id = payload.get("patient_id")
        tenant_id = payload.get("tenant_id")
        if patient_id and tenant_id:
            await handle_registration_failed(str(patient_id), str(tenant_id))
        else:
            logger.error("Received visit.registration_failed event with missing fields: %s", payload)


async def start_subscriber() -> None:
    await start_consumer(
        service_name="patient-service",
        routing_keys=["visit.registration_failed"],
        handler=_dispatch,
    )
