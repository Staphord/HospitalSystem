"""Integration test for the shared RabbitMQ messaging layer.

Prerequisites:
    RabbitMQ must be running and accessible via RABBITMQ_URL env var.

Run:
    export RABBITMQ_URL=amqp://guest:guest@localhost:5672/
    pytest tests/integration/test_messaging.py -v
"""

from __future__ import annotations

import asyncio
import os
import pytest

from shared.messaging.connection import close_connection, get_connection
from shared.messaging.publisher import publish_event
from shared.messaging.subscriber import start_consumer

pytestmark = pytest.mark.asyncio

TEST_ROUTING_KEY = "integration.test.message"


@pytest.fixture(autouse=True)
def _ensure_rabbitmq_url():
    os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")


@pytest.fixture
async def rabbitmq_conn():
    """Yield a connected RabbitMQ robust connection and clean up after."""
    conn = await get_connection()
    yield conn
    await close_connection()


async def test_publish_and_consume_round_trip(rabbitmq_conn) -> None:
    """Publish an event and verify a consumer receives it."""
    received: list[dict] = []

    async def handler(routing_key: str, payload: dict) -> None:
        if routing_key == TEST_ROUTING_KEY:
            received.append(payload)

    # Start consumer in background
    consumer_task = asyncio.create_task(
        asyncio.wait_for(
            start_consumer(
                service_name="integration-test-service",
                routing_keys=[TEST_ROUTING_KEY],
                handler=handler,
            ),
            timeout=10.0,
        )
    )
    await asyncio.sleep(1)  # Allow queue binding

    payload = {"test": "round_trip", "value": 42}
    await publish_event(TEST_ROUTING_KEY, payload)

    # Wait up to 5 seconds for delivery
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.1)

    # Teardown consumer
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert len(received) == 1, f"Expected 1 message, got {len(received)}"
    assert received[0]["test"] == "round_trip"
    assert received[0]["value"] == 42


async def test_publish_multiple_events(rabbitmq_conn) -> None:
    """Publish multiple events and verify all are received in order."""
    received: list[dict] = []

    async def handler(routing_key: str, payload: dict) -> None:
        if routing_key == TEST_ROUTING_KEY:
            received.append(payload)

    consumer_task = asyncio.create_task(
        asyncio.wait_for(
            start_consumer(
                service_name="integration-test-service-2",
                routing_keys=[TEST_ROUTING_KEY],
                handler=handler,
            ),
            timeout=10.0,
        )
    )
    await asyncio.sleep(1)

    for i in range(3):
        await publish_event(TEST_ROUTING_KEY, {"index": i})

    for _ in range(50):
        if len(received) == 3:
            break
        await asyncio.sleep(0.1)

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    assert len(received) == 3
    indices = [r["index"] for r in received]
    assert indices == [0, 1, 2]


async def test_topic_routing_key_filtering(rabbitmq_conn) -> None:
    """Ensure consumers only receive messages matching their routing keys."""
    received_a: list[dict] = []
    received_b: list[dict] = []

    async def handler_a(routing_key: str, payload: dict) -> None:
        received_a.append({"key": routing_key, "payload": payload})

    async def handler_b(routing_key: str, payload: dict) -> None:
        received_b.append({"key": routing_key, "payload": payload})

    task_a = asyncio.create_task(
        asyncio.wait_for(
            start_consumer(
                service_name="filter-service-a",
                routing_keys=["order.created"],
                handler=handler_a,
            ),
            timeout=10.0,
        )
    )
    task_b = asyncio.create_task(
        asyncio.wait_for(
            start_consumer(
                service_name="filter-service-b",
                routing_keys=["order.updated"],
                handler=handler_b,
            ),
            timeout=10.0,
        )
    )
    await asyncio.sleep(1)

    await publish_event("order.created", {"id": "o-1"})
    await publish_event("order.updated", {"id": "o-2"})

    for _ in range(50):
        if len(received_a) == 1 and len(received_b) == 1:
            break
        await asyncio.sleep(0.1)

    task_a.cancel()
    task_b.cancel()
    try:
        await task_a
        await task_b
    except asyncio.CancelledError:
        pass

    assert len(received_a) == 1
    assert received_a[0]["key"] == "order.created"
    assert len(received_b) == 1
    assert received_b[0]["key"] == "order.updated"
