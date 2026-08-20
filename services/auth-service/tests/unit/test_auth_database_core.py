from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError

from app.core import database


def test_database_init_and_context_helpers(monkeypatch, db_session):
    monkeypatch.setattr(database, "_SessionLocal", lambda: db_session)
    monkeypatch.setattr(database, "_engine", object())
    monkeypatch.setattr(database, "_router", type("Router", (), {"get_session": lambda self, _: db_session})())
    assert database.get_session_local() is not None
    database.init_db()
    context = database.get_hospital_context("h1")
    assert context.hospital_id == "h1"
    database.close_hospital_context(context)


def test_database_ensure_existing_and_create_paths(monkeypatch):
    monkeypatch.setattr(database.settings, "database_url", "postgresql://u:p@localhost/db")
    monkeypatch.setattr(database.settings, "db_admin_url", "postgresql://u:p@localhost/postgres")
    
    mock_conn = MagicMock()
    mock_conn.execute.return_value.scalar.return_value = None
    admin_engine = MagicMock()
    admin_engine.connect.return_value.__enter__.return_value = mock_conn
    
    test_engine = MagicMock()
    test_engine.connect.side_effect = OperationalError("select", {}, Exception())
    
    engines = [test_engine, admin_engine]
    monkeypatch.setattr(database, "create_engine", lambda *args, **kwargs: engines.pop(0))
    database._ensure_database_exists()
    assert admin_engine.dispose.called


def test_database_existing_and_invalid_urls(monkeypatch):
    """Exercise the no-op and validation sides of database bootstrap."""
    engine = MagicMock()
    engine.connect.return_value.__enter__.return_value.execute.return_value = MagicMock()
    monkeypatch.setattr(database, "create_engine", lambda *args, **kwargs: engine)
    database.settings.database_url = "postgresql://u:p@localhost/db"
    database._ensure_database_exists()
    engine.dispose.assert_called_once()

    monkeypatch.setattr(database, "create_engine", lambda *args, **kwargs: (_ for _ in ()).throw(
        OperationalError("select", {}, Exception())
    ))
    with pytest.raises(OperationalError):
        database._ensure_database_exists()
    monkeypatch.setattr(database.settings, "database_url", "postgresql://u:p@localhost/")
    with pytest.raises(ValueError):
        database._ensure_database_exists()
    with pytest.raises(NotImplementedError):
        database.DatabaseRouter.get_session(object(), "h")


def test_database_lazy_init_and_generator_cleanup(monkeypatch):
    engine = object()
    factory = MagicMock()
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_ensure_database_exists", lambda: None)
    monkeypatch.setattr(database, "create_engine", lambda *args, **kwargs: engine)
    monkeypatch.setattr(database, "sessionmaker", lambda **kwargs: factory)
    database._init_engine()
    assert database._engine is engine
    assert database.get_session_local() is factory
    session = MagicMock()
    factory.return_value = session
    gen = database.get_db()
    assert next(gen) is session
    gen.close()
    session.close.assert_called_once()


@pytest.mark.asyncio
async def test_messaging_connection_and_subscriber_branches(monkeypatch):
    from app.messaging import connection, subscriber

    class Conn:
        is_closed = False
        def __init__(self): self.channel_result = MagicMock()
        async def channel(self): return self.channel_result
        async def close(self): self.is_closed = True

    conn = Conn()
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=conn))
    connection._connection = None
    assert await connection.get_connection() is conn
    assert await connection.get_connection() is conn
    assert await connection.get_channel() is conn.channel_result
    channel = MagicMock()
    channel.declare_exchange = AsyncMock(return_value="exchange")
    assert await connection.declare_exchange(channel) == "exchange"
    await connection.close_connection()
    assert connection._connection is None

    class QueueIter:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
    queue = MagicMock(name="queue")
    queue.name = "events"
    queue.bind = AsyncMock()
    queue.iterator.return_value = QueueIter()
    channel = MagicMock()
    channel.set_qos = AsyncMock()
    channel.declare_queue = AsyncMock(return_value=queue)
    conn.channel = AsyncMock(return_value=channel)
    monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=conn))
    monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value="exchange"))
    await subscriber.start_consumer("auth", ["a", "b"], AsyncMock())
    assert queue.bind.await_count == 2

    class Message:
        body = b"not-json"
        routing_key = "bad"
        class Process:
            async def __aenter__(self): return self
            async def __aexit__(self, *args): return None
        def process(self): return self.Process()
    class OneQueue(QueueIter):
        def __init__(self): self.done = False
        async def __anext__(self):
            if self.done: raise StopAsyncIteration
            self.done = True
            return Message()
    queue.iterator.return_value = OneQueue()
    await subscriber.start_consumer("auth", [], AsyncMock())


@pytest.mark.asyncio
async def test_event_subscriber_dispatch_and_start(monkeypatch):
    from app.events import subscriber
    handler = AsyncMock()
    monkeypatch.setattr(subscriber, "handle_tenant_suspended", handler)
    await subscriber._dispatch("tenant.suspended", {"tenant_id": "t", "reason": "x"})
    await subscriber._dispatch("other", {})
    handler.assert_awaited_once_with("t", "x")
    consumer = AsyncMock()
    monkeypatch.setattr(subscriber, "start_consumer", consumer)
    await subscriber.start_subscriber()
    consumer.assert_awaited_once()


@pytest.mark.asyncio
async def test_tenant_db_session_retrieval(monkeypatch):
    from app.db import tenant as tenant_db
    mock_factory = MagicMock()
    mock_session = AsyncMock()
    mock_factory.return_value.__aenter__.return_value = mock_session
    monkeypatch.setattr(tenant_db, "_get_async_session_factory", AsyncMock(return_value=mock_factory))
    
    gen = tenant_db.get_tenant_session("tenant_test")
    session = await gen.__anext__()
    assert session == mock_session
