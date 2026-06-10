"""Async RabbitMQ event subscriber."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

import aio_pika

from app.messaging.connection import declare_exchange, get_connection

logger = logging.getLogger(__name__)

Handler = Callable[[str, dict[str, Any]], Awaitable[None]]


async def start_consumer(
    service_name: str,
    routing_keys: list[str],
    handler: Handler,
) -> None:
    """Start a durable topic consumer for this service.

    Args:
        service_name: Used to build the queue name, e.g. "triage-service"
        routing_keys: List of routing key patterns to bind, e.g. ["visit.created"]
        handler: Async callback receiving (routing_key, payload_dict)
    """
    connection = await get_connection()
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)

    exchange = await declare_exchange(channel)
    queue = await channel.declare_queue(f"{service_name}_events", durable=True)

    for key in routing_keys:
        await queue.bind(exchange, routing_key=key)
        logger.info("Bound queue %s to routing key %s", queue.name, key)

    async with queue.iterator() as queue_iter:
        async for message in queue_iter:
            async with message.process():
                try:
                    payload = json.loads(message.body.decode("utf-8"))
                    await handler(message.routing_key, payload)
                except Exception as exc:
                    logger.exception("Error processing message: %s", exc)


async def run_consumer_task(
    service_name: str,
    routing_keys: list[str],
    handler: Handler,
) -> asyncio.Task:
    """Return an asyncio.Task running the consumer loop.

    Use this in a FastAPI lifespan to start / cancel the consumer.
    """
    return asyncio.create_task(
        start_consumer(service_name, routing_keys, handler),
        name=f"rabbitmq-consumer-{service_name}",
    )
