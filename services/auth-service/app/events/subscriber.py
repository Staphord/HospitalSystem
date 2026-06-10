"""Event Subscriber for Auth Service."""
from app.messaging.subscriber import start_consumer

ROUTING_KEYS = ["tenant.suspended"]

async def handle_tenant_suspended(tenant_id: str, reason: str) -> None:
    """Placeholder: revoke tokens for suspended tenant."""
    pass

async def _dispatch(routing_key: str, payload: dict) -> None:
    if routing_key == "tenant.suspended":
        await handle_tenant_suspended(
            payload.get("tenant_id", ""), payload.get("reason", "")
        )

async def start_subscriber() -> None:
    await start_consumer(
        service_name="auth-service",
        routing_keys=ROUTING_KEYS,
        handler=_dispatch,
    )
