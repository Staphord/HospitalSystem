from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import tenant_service


class Result:
    def __init__(self, value): self.value = value
    def scalar(self): return self.value
    def one_or_none(self): return self.value


@pytest.mark.asyncio
async def test_tenant_suspension_cache_paths(monkeypatch):
    redis = AsyncMock()
    redis.get.return_value = "1"
    monkeypatch.setattr(tenant_service, "_redis", redis)
    assert await tenant_service.is_tenant_suspended("t") is True
    redis.get.return_value = None
    engine = MagicMock()
    conn = MagicMock()
    conn.execute.return_value.scalar.return_value = "suspended"
    engine.connect.return_value.__enter__.return_value = conn
    monkeypatch.setattr("sqlalchemy.create_engine", MagicMock(return_value=engine))
    assert await tenant_service.is_tenant_suspended("t") is True
    await tenant_service.cache_tenant_suspension("t", 30)
    await tenant_service.remove_tenant_suspension_cache("t")


@pytest.mark.asyncio
async def test_tenant_status_queries_and_dsn(monkeypatch):
    db = MagicMock()
    row = MagicMock()
    row.__iter__.return_value = iter(["active", datetime.now(timezone.utc) + timedelta(days=1)])
    db.execute.return_value.one_or_none.return_value = row
    assert await tenant_service.check_tenant_subscription(db, "t") == "active"
    db.execute.return_value.one_or_none.return_value = None
    assert await tenant_service.check_tenant_subscription(db, "t") == "not_found"
    db.execute.return_value.scalar.return_value = "encrypted"
    monkeypatch.setattr(tenant_service, "decrypt_dsn", lambda value: "postgresql://db")
    assert await tenant_service.get_tenant_db_dsn(db, "t") == "postgresql://db"
    db.execute.return_value.scalar.return_value = None
    assert await tenant_service.get_tenant_db_dsn(db, "t") is None


@pytest.mark.asyncio
async def test_tenant_status_transitions_and_keycloak_revocation(monkeypatch):
    db = MagicMock()
    db.execute.return_value.one_or_none.return_value = ("suspended", None, 7)
    monkeypatch.setattr(tenant_service, "cache_tenant_suspension", AsyncMock())
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"

    overdue = datetime.now(timezone.utc) - timedelta(days=31)
    db.execute.return_value.one_or_none.return_value = ("active", overdue, 7)
    monkeypatch.setattr(tenant_service, "_revoke_keycloak_sessions", AsyncMock())
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"
    db.execute.return_value.one_or_none.return_value = ("active", datetime.now(timezone.utc) - timedelta(days=2), 7)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "expired"


@pytest.mark.asyncio
async def test_revoke_keycloak_sessions_http_paths(monkeypatch):
    class Response:
        is_success = True
        def json(self): return {"access_token": "a"} if False else [{"id": "u1"}]
        def raise_for_status(self): pass
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            r = Response(); r.json = lambda: {"access_token": "a"}; return r
        async def get(self, *args, **kwargs): return Response()
        async def put(self, *args, **kwargs): return Response()
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", lambda **kwargs: Client())
    await tenant_service._revoke_keycloak_sessions("t")


@pytest.mark.asyncio
async def test_tenant_service_cache_failures_and_subscription_edges(monkeypatch):
    redis = AsyncMock()
    redis.set.side_effect = RuntimeError("redis down")
    redis.delete.side_effect = RuntimeError("redis down")
    monkeypatch.setattr(tenant_service, "_redis", redis)
    await tenant_service.cache_tenant_suspension("t")
    await tenant_service.remove_tenant_suspension_cache("t")

    db = MagicMock()
    db.execute.return_value.one_or_none.return_value = ("suspended", None)
    assert await tenant_service.check_tenant_subscription(db, "t") == "suspended"
    expired = datetime.now(timezone.utc) - timedelta(days=1)
    db.execute.return_value.one_or_none.return_value = ("active", expired)
    assert await tenant_service.check_tenant_subscription(db, "t") == "expired"
    db.execute.return_value.one_or_none.return_value = ("active", None, 1)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "active"

    class Response:
        is_success = False
        def json(self): return {"access_token": "a"}
        def raise_for_status(self): raise RuntimeError("bad")
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): return Response()
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", lambda **kwargs: Client())
    await tenant_service._revoke_keycloak_sessions("t")
