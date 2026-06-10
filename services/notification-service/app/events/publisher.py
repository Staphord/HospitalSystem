# Events published by notification-service (optional / placeholder)

from app.messaging.publisher import publish_event

async def publish_notification_sent(notification_id: str, tenant_id: str) -> None:
    """Stub: emit notification.sent event."""
    await publish_event("notification.sent", {"notification_id": notification_id, "tenant_id": tenant_id})
