"""Unit tests for messaging connection, publisher, subscriber, and events in master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import json

from app.messaging.connection import (
    get_connection,
    close_connection,
    get_channel,
    declare_exchange,
)
from app.messaging.publisher import publish_event
from app.messaging.subscriber import start_consumer, run_consumer_task
from app.events.publisher import (
    publish_tenant_created,
    publish_tenant_suspended,
    publish_tenant_activated,
    publish_tenant_reactivated,
    publish_announcement_created,
    publish_subscription_invoice_generated,
    publish_subscription_request_created,
    publish_subscription_request_processed,
    publish_subscription_invoice_overdue,
)
from app.events.subscriber import handle_tenant_created, handle_tenant_suspended, _dispatch, start_subscriber

@pytest.mark.asyncio
async def test_messaging_connection_and_publisher():
    mock_conn = AsyncMock(is_closed=False)
    mock_channel = AsyncMock()
    mock_exchange = AsyncMock()
    mock_conn.channel.return_value = mock_channel
    mock_channel.declare_exchange.return_value = mock_exchange

    with patch("aio_pika.connect_robust", AsyncMock(return_value=mock_conn)):
        conn = await get_connection()
        assert conn == mock_conn

        chan = await get_channel()
        assert chan == mock_channel

        ex = await declare_exchange(chan)
        assert ex == mock_exchange

        await close_connection()
        assert mock_conn.close.called

@pytest.mark.asyncio
async def test_publish_event_success_and_failure():
    mock_channel = AsyncMock()
    mock_exchange = AsyncMock()

    with patch("app.messaging.publisher.get_channel", AsyncMock(return_value=mock_channel)):
        with patch("app.messaging.publisher.declare_exchange", AsyncMock(return_value=mock_exchange)):
            await publish_event("tenant.created", {"tenant_id": "t1"})
            assert mock_exchange.publish.called

    with patch("app.messaging.publisher.get_channel", AsyncMock(side_effect=Exception("pika error"))):
        await publish_event("tenant.created", {"tenant_id": "t1"})

@pytest.mark.asyncio
async def test_all_event_publishers():
    with patch("app.events.publisher.publish_event", AsyncMock()) as mock_pub:
        await publish_tenant_created("t1", {"name": "H1"})
        await publish_tenant_suspended("t1", {"reason": "overdue"})
        await publish_tenant_activated("t1", {"reason": "active"})
        await publish_tenant_reactivated("t1", {"reason": "paid"})
        await publish_announcement_created({"title": "A1"})
        await publish_subscription_invoice_generated("t1", {"inv": "1"})
        await publish_subscription_request_created({"tenant_id": "t1"})
        await publish_subscription_request_processed("t1", {"status": "approved"})
        await publish_subscription_invoice_overdue("t1", {"inv": "1"})

        assert mock_pub.call_count == 9

@pytest.mark.asyncio
async def test_events_subscriber_handlers():
    with patch("app.events.subscriber.provision_tenant_database", AsyncMock()):
        await handle_tenant_created({"tenant_id": "t1", "name": "H1"})

    await handle_tenant_suspended({"tenant_id": "t1", "reason": "overdue"})

    with patch("app.events.subscriber.handle_tenant_created", AsyncMock()) as mock_c:
        await _dispatch("tenant.created", {"tenant_id": "t1"})
        assert mock_c.called

    with patch("app.events.subscriber.handle_tenant_suspended", AsyncMock()) as mock_s:
        await _dispatch("tenant.suspended", {"tenant_id": "t1"})
        assert mock_s.called

    with patch("app.events.subscriber.start_consumer", AsyncMock()) as mock_sub:
        await start_subscriber()
        assert mock_sub.called

@pytest.mark.asyncio
async def test_start_consumer_and_run_consumer_task():
    mock_conn = AsyncMock()
    mock_chan = AsyncMock()
    mock_ex = AsyncMock()
    mock_queue = AsyncMock()

    mock_conn.channel.return_value = mock_chan
    mock_chan.declare_queue.return_value = mock_queue

    mock_msg = MagicMock()
    mock_msg.body = json.dumps({"test": "data"}).encode("utf-8")
    mock_msg.routing_key = "test.key"

    class DummyContext:
        async def __aenter__(self):
            return None
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_msg.process = MagicMock(return_value=DummyContext())

    class MockAsyncContextManager:
        async def __aenter__(self):
            class MockQueueIter:
                def __init__(self, items):
                    self.items = items
                def __aiter__(self):
                    return self
                async def __anext__(self):
                    if not self.items:
                        raise StopAsyncIteration
                    return self.items.pop(0)
            return MockQueueIter([mock_msg])

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_queue.iterator = MagicMock(return_value=MockAsyncContextManager())

    handler = AsyncMock()
    with patch("app.messaging.subscriber.get_connection", AsyncMock(return_value=mock_conn)):
        with patch("app.messaging.subscriber.declare_exchange", AsyncMock(return_value=mock_ex)):
            task = await run_consumer_task("test_service", ["test.*"], handler)
            await task
            assert handler.called
