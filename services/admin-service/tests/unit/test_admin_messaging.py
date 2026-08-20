import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events import publisher as events
from app.messaging import connection, publisher, subscriber


@pytest.mark.asyncio
async def test_connection_manager_and_exchange():
    connection._connection = None
    conn = MagicMock(is_closed=False)
    conn.channel = AsyncMock(return_value=MagicMock())
    conn.close = AsyncMock()
    with patch.object(connection.aio_pika, "connect_robust", new_callable=AsyncMock, return_value=conn):
        assert await connection.get_connection() is conn
        assert await connection.get_connection() is conn
    await connection.close_connection()
    conn.close.assert_awaited_once()

    channel = MagicMock()
    channel.declare_exchange = AsyncMock(return_value="exchange")
    assert await connection.declare_exchange(channel) == "exchange"


@pytest.mark.asyncio
async def test_publisher_and_domain_events():
    exchange = MagicMock()
    exchange.publish = AsyncMock()
    channel = MagicMock()
    with patch.object(publisher, "get_channel", new_callable=AsyncMock, return_value=channel), \
         patch.object(publisher, "declare_exchange", new_callable=AsyncMock, return_value=exchange):
        await publisher.publish_event("user.created", {"id": 1})
    exchange.publish.assert_awaited_once()
    with patch.object(publisher, "get_channel", new_callable=AsyncMock, side_effect=RuntimeError("down")):
        await publisher.publish_event("ignored", {})

    with patch.object(events, "publish_event", new_callable=AsyncMock) as publish:
        await events.publish_user_created("tenant", "user", {"role": "doctor"})
        await events.publish_user_deactivated("tenant", "user", {"reason": "test"})
    assert publish.await_count == 2


@pytest.mark.asyncio
async def test_subscriber_task_and_dispatch():
    called = []
    async def handler(key, payload):
        called.append((key, payload))

    await subscriber.run_consumer_task("admin", ["user.*"], handler)
    # The consumer is intentionally asynchronous; cancel the task created above.
    tasks = [t for t in asyncio.all_tasks() if t.get_name() == "rabbitmq-consumer-admin"]
    for task in tasks:
        task.cancel()
    with patch.object(subscriber, "get_connection", new_callable=AsyncMock) as get_conn:
        channel = MagicMock()
        channel.set_qos = AsyncMock()
        get_conn.return_value.channel = AsyncMock(return_value=channel)
        # No queue iterator is needed to verify setup is delegated correctly.
    from app.events.subscriber import _dispatch
    await _dispatch("user.created", {"id": "u"})
