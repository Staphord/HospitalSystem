"""Async RabbitMQ connection manager using aio-pika."""

from __future__ import annotations

import logging
import os

import aio_pika
from aio_pika import ExchangeType

logger = logging.getLogger(__name__)

_connection: aio_pika.RobustConnection | None = None

EXCHANGE_NAME = "hospital_events"
EXCHANGE_TYPE = ExchangeType.TOPIC


async def get_connection() -> aio_pika.RobustConnection:
    """Return a shared robust connection to RabbitMQ."""
    global _connection
    if _connection is None or _connection.is_closed:
        url = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
        _connection = await aio_pika.connect_robust(url)
        logger.info("RabbitMQ connection established")
    return _connection


async def close_connection() -> None:
    """Close the shared connection gracefully."""
    global _connection
    if _connection and not _connection.is_closed:
        await _connection.close()
        logger.info("RabbitMQ connection closed")
        _connection = None


async def get_channel() -> aio_pika.Channel:
    """Open and return a channel on the shared connection."""
    conn = await get_connection()
    return await conn.channel()


async def declare_exchange(channel: aio_pika.Channel) -> aio_pika.Exchange:
    """Declare the hospital_events topic exchange (idempotent)."""
    return await channel.declare_exchange(
        name=EXCHANGE_NAME,
        type=EXCHANGE_TYPE,
        durable=True,
    )
