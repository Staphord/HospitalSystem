"""Standalone smoke test for the RabbitMQ event bus.

Usage:
    export RABBITMQ_URL=amqp://guest:guest@localhost:5672/
    python scripts/test_rabbitmq.py

Requires:
    pip install aio-pika
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

# Use the shared messaging module directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from shared.messaging.connection import close_connection, get_connection
from shared.messaging.publisher import publish_event
from shared.messaging.subscriber import start_consumer

TEST_ROUTING_KEY = "test.event"
_received: list[dict] = []


async def _handler(routing_key: str, payload: dict) -> None:
    print(f"  [Consumer] Received {routing_key}: {payload}")
    _received.append(payload)


async def _run_consumer(timeout: float = 5.0) -> None:
    await asyncio.wait_for(
        start_consumer(
            service_name="test-service",
            routing_keys=[TEST_ROUTING_KEY],
            handler=_handler,
        ),
        timeout=timeout,
    )


async def main() -> int:
    url = os.environ.get("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
    os.environ.setdefault("RABBITMQ_URL", url)

    print(f"Connecting to RabbitMQ at {url} ...")
    conn = await get_connection()
    print("  Connection OK")

    # Start consumer in background
    consumer_task = asyncio.create_task(_run_consumer(timeout=10.0))
    await asyncio.sleep(1)  # Give consumer time to bind

    payload = {"message": "hello from smoke test", "timestamp": str(asyncio.get_event_loop().time())}
    print(f"Publishing {TEST_ROUTING_KEY}: {payload}")
    await publish_event(TEST_ROUTING_KEY, payload)

    # Wait a bit for delivery
    await asyncio.sleep(2)

    # Cancel consumer
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await close_connection()

    if _received:
        received = _received[0]
        assert received["message"] == payload["message"], "Payload mismatch"
        print("\n✅ RabbitMQ smoke test PASSED")
        return 0
    else:
        print("\n❌ RabbitMQ smoke test FAILED — no message received")
        return 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except Exception as exc:
        print(f"\n❌ RabbitMQ smoke test FAILED with exception: {exc}")
        rc = 1
    sys.exit(rc)
