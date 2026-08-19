"""Tests for auth-service database, messaging, and event infrastructure."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.db import tenant as tenant_db
from app.events import publisher as event_publisher
from app.events import subscriber as event_subscriber
from app.messaging import connection, publisher
from app.services import provision


@pytest.mark.asyncio
async def test_tenant_session_factory_cache_and_missing_tenant(monkeypatch):
    master_db = MagicMock()
    monkeypatch.setattr("app.db.master.get_master_db", lambda: master_db)
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value=None))
    with pytest.raises(Exception, match="not found"):
        await tenant_db._get_async_session_factory("missing")

    fake_engine = MagicMock()
    fake_factory = MagicMock()
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value="postgresql://db"))
    monkeypatch.setattr(tenant_db, "create_async_engine", MagicMock(return_value=fake_engine))
    monkeypatch.setattr(tenant_db, "async_sessionmaker", MagicMock(return_value=fake_factory))
    tenant_db._async_engine_cache.clear()
    assert await tenant_db._get_async_session_factory("t1") is fake_factory
    assert await tenant_db._get_async_session_factory("t1") is fake_factory


@pytest.mark.asyncio
async def test_messaging_connection_and_publisher(monkeypatch):
    conn = MagicMock(is_closed=False)
    conn.close = AsyncMock()
    channel = MagicMock()
    channel.declare_exchange = AsyncMock(return_value=MagicMock())
    conn.channel = AsyncMock(return_value=channel)
    monkeypatch.setattr(connection, "_connection", conn)
    assert await connection.get_connection() is conn
    channel = await connection.get_channel()
    await connection.declare_exchange(channel)
    await connection.close_connection()
    assert connection._connection is None

    exchange = MagicMock()
    exchange.publish = AsyncMock()
    monkeypatch.setattr(publisher, "get_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(publisher, "declare_exchange", AsyncMock(return_value=exchange))
    await publisher.publish_event("tenant.created", {"id": "t"})
    exchange.publish.assert_awaited_once()
    monkeypatch.setattr(publisher, "get_channel", AsyncMock(side_effect=RuntimeError("down")))
    await publisher.publish_event("tenant.created", {})


@pytest.mark.asyncio
async def test_event_publisher_and_subscriber(monkeypatch):
    publish = AsyncMock()
    monkeypatch.setattr(event_publisher, "publish_event", publish)
    await event_publisher.publish_tenant_created("t", "Hospital", "a@e.com", "admin", "h")
    publish.assert_awaited_once()
    handler = AsyncMock()
    monkeypatch.setattr(event_subscriber, "handle_tenant_suspended", handler)
    await event_subscriber._dispatch("tenant.suspended", {"tenant_id": "t", "reason": "x"})
    handler.assert_awaited_once_with("t", "x")
    await event_subscriber._dispatch("other", {})


@pytest.mark.asyncio
async def test_message_consumer_setup_and_task(monkeypatch):
    from app.messaging import subscriber
    class Iterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
    queue = MagicMock(name="events"); queue.name = "events"
    queue.bind = AsyncMock()
    queue.iterator.return_value = Iterator()
    channel = MagicMock(); channel.set_qos = AsyncMock(); channel.declare_queue = AsyncMock(return_value=queue)
    connection_obj = MagicMock(); connection_obj.channel = AsyncMock(return_value=channel)
    monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=connection_obj))
    monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value=MagicMock()))
    await subscriber.start_consumer("auth", ["tenant.created"], AsyncMock())
    task = await subscriber.run_consumer_task("auth", [], AsyncMock())
    await task


def test_provision_helpers(monkeypatch):
    monkeypatch.setattr(provision.settings, "tenant_db_template", "postgresql:///{tenant_id}")
    assert provision._build_tenant_dsn("t1") == "postgresql:///t1"
    engine = MagicMock()
    monkeypatch.setattr(provision, "create_engine", MagicMock(return_value=engine))
    assert provision._get_admin_engine() is engine


@pytest.mark.asyncio
async def test_subscriber_direct_handlers():
    await event_subscriber.handle_tenant_suspended("t1", "suspended")


