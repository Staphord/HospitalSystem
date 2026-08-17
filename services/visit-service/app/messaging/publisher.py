"""Async RabbitMQ event publisher."""

from __future__ import annotations

import json
import logging
from typing import Any

import aio_pika

from app.messaging.connection import declare_exchange, get_channel

logger = logging.getLogger(__name__)


async def publish_event(routing_key: str, payload: dict[str, Any]) -> None:
    """Publish a JSON event to the hospital_events topic exchange."""
    try:
        channel = await get_channel()
        exchange = await declare_exchange(channel)
        message_body = json.dumps(payload, default=str).encode("utf-8")
        await exchange.publish(
            aio_pika.Message(body=message_body, content_type="application/json"),
            routing_key=routing_key,
        )
        logger.debug("Published event %s", routing_key)
    except Exception as exc:
        logger.exception("Failed to publish event %s: %s", routing_key, exc)


publish = publish_event
