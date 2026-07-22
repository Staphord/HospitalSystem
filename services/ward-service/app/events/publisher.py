"""Events published by ward-service."""

from __future__ import annotations

from datetime import datetime

from app.messaging.publisher import publish_event


async def publish_patient_admitted(
    admission_id: str,
    patient_id: str,
    tenant_id: str,
    bed_id: str,
) -> None:
    await publish_event(
        "patient.admitted",
        {
            "admission_id": admission_id,
            "patient_id": patient_id,
            "tenant_id": tenant_id,
            "bed_id": bed_id,
        },
    )


async def publish_patient_discharged(
    admission_id: str,
    patient_id: str,
    tenant_id: str,
    discharge_date: datetime | None,
    length_of_stay_days: float = 0.0,
    visit_id: str | None = None,
) -> None:
    payload = {
        "admission_id": admission_id,
        "patient_id": patient_id,
        "tenant_id": tenant_id,
        "discharge_date": discharge_date.isoformat() if discharge_date else None,
        "length_of_stay_days": length_of_stay_days,
    }
    if visit_id:
        payload["visit_id"] = visit_id
    await publish_event("patient.discharged", payload)
