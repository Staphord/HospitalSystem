import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.messaging import subscriber
from app.services import audit_service, settings_service


@pytest.mark.asyncio
async def test_subscriber_consumes_valid_and_invalid_messages():
    class Message:
        def __init__(self, body, key="event"): self.body = body; self.routing_key = key
        def process(self): return self
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
    class Iterator:
        def __init__(self): self.messages = iter([Message(b'{"id": 1}'), Message(b"bad")])
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        def __aiter__(self): return self
        async def __anext__(self):
            try: return next(self.messages)
            except StopIteration: raise StopAsyncIteration
    queue = SimpleNamespace(name="admin_events", bind=AsyncMock(), iterator=lambda: Iterator())
    channel = SimpleNamespace(set_qos=AsyncMock(), declare_queue=AsyncMock(return_value=queue))
    connection = SimpleNamespace(channel=AsyncMock(return_value=channel))
    handler = AsyncMock()
    with patch.object(subscriber, "get_connection", new_callable=AsyncMock, return_value=connection), patch.object(subscriber, "declare_exchange", new_callable=AsyncMock, return_value=object()):
        await subscriber.start_consumer("admin", ["event"], handler)
    handler.assert_awaited_once_with("event", {"id": 1})


@pytest.mark.asyncio
async def test_event_subscriber_delegates_to_consumer():
    with patch("app.events.subscriber.start_consumer", new_callable=AsyncMock) as consumer:
        from app.events.subscriber import start_subscriber
        await start_subscriber()
    consumer.assert_awaited_once()


@pytest.mark.asyncio
async def test_subscriber_task_wrapper_and_audit_failure_paths():
    task = await subscriber.run_consumer_task("admin", [], AsyncMock())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    db = MagicMock(); db.add.side_effect = RuntimeError("db")
    assert audit_service.log_change(db, user_id="u", action="X", table_name="t") is None
    query = db.query.return_value; query.filter.return_value = query; query.order_by.return_value = query; query.offset.return_value = query; query.limit.return_value = query; query.count.return_value = 0; query.all.return_value = []
    assert audit_service.list_audit_logs(db, from_dt=datetime.now(timezone.utc), to_dt=datetime.now(timezone.utc)) == ([], 0)


def test_audit_log_ignores_rollback_failure():
    db = MagicMock()
    db.add.side_effect = RuntimeError("db")
    db.rollback.side_effect = RuntimeError("rollback")
    assert audit_service.log_change(db, user_id="u", action="X", table_name="t") is None


def test_settings_table_creation_and_update_paths():
    db = MagicMock(); inspector = MagicMock(); inspector.get_table_names.return_value = []
    db.get_bind.return_value = object()
    with patch.object(settings_service, "inspect", return_value=inspector):
        settings_service.ensure_settings_table(db)
    db2 = MagicMock(); inspector2 = MagicMock(); inspector2.get_table_names.return_value = ["hospital_settings"]; db2.get_bind.return_value = object()
    q = db2.query.return_value; q.filter.return_value = q; q.order_by.return_value = q; q.all.return_value = []
    with patch.object(settings_service, "inspect", return_value=inspector2), patch.object(settings_service.audit_service, "log_change"):
        assert settings_service.upsert_settings(db2, {"timezone": "UTC", "" : "skip", "x" * 101: "skip"}, actor_sub="a") == []
    row = SimpleNamespace(key="timezone", value="UTC", updated_by=None, updated_at=None)
    db2.query.return_value.filter.return_value.first.return_value = row
    db2.query.return_value.order_by.return_value.all.return_value = [row]
    with patch.object(settings_service, "inspect", return_value=inspector2), patch.object(settings_service.audit_service, "log_change"):
        assert settings_service.upsert_settings(db2, {"timezone": "Africa/Dar"}, actor_sub="a")[0].value == "Africa/Dar"
    db3 = MagicMock(); db3.get_bind.side_effect = RuntimeError("inspect")
    db3.rollback.side_effect = RuntimeError("rollback")
    settings_service.ensure_settings_table(db3)
    db2.query.return_value.filter.return_value.first.return_value = row
    with patch.object(settings_service, "inspect", return_value=inspector2), patch.object(settings_service.audit_service, "log_change") as audit:
        settings_service.upsert_settings(db2, {"timezone": "Africa/Dar"}, actor_sub="a")
        audit.assert_not_called()
