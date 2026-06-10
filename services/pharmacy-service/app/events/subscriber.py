"""
Event Subscriber for Pharmacy Service.

Consumes:
- prescription.issued: Triggers dispensing workflow when a prescription is issued.
- payment.received: Confirms payment before dispensing drugs.
"""

from app.messaging.subscriber import start_consumer

async def handle_prescription_issued(prescription_id: str, tenant_id: str) -> None:
    """Placeholder: Handle prescription.issued event."""
    pass

async def handle_payment_received(payment_id: str, tenant_id: str) -> None:
    """Placeholder: Handle payment.received event."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "prescription.issued":
        await handle_prescription_issued(payload["prescription_id"], payload["tenant_id"])
    elif routing_key == "payment.received":
        await handle_payment_received(payload["payment_id"], payload["tenant_id"])

async def start_subscriber() -> None:
    await start_consumer(
        service_name="pharmacy-service",
        routing_keys=["prescription.issued", "payment.received"],
        handler=_dispatch,
    )
