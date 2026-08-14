"""Events published by visit-service."""

from __future__ import annotations

from app.messaging.publisher import publish_event


async def publish_visit_created(visit_id: str, patient_id: str, tenant_id: str) -> None:
    await publish_event(
        "visit.created",
        {
            "visit_id": visit_id,
            "patient_id": patient_id,
            "tenant_id": tenant_id,
        },
    )
