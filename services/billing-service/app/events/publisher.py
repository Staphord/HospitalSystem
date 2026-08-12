# Events published by billing-service

from app.messaging.publisher import publish_event

async def publish_bill_created(bill_id: str, tenant_id: str) -> None:
    """Stub: emit bill.created event."""
    await publish_event("bill.created", {"bill_id": bill_id, "tenant_id": tenant_id})

async def publish_payment_received(payment_id: str, tenant_id: str, visit_id: str | None = None) -> None:
    """emit payment.received event."""
    payload = {"payment_id": payment_id, "tenant_id": tenant_id}
    if visit_id:
        payload["visit_id"] = visit_id
    await publish_event("payment.received", payload)
