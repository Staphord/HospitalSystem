import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request
from starlette.responses import Response

from app import middleware, proxy, tenant
from app.config import settings


def request(path="/api/v1/reception/x", method="GET", headers=None):
    query = path.split("?", 1)[1] if "?" in path else ""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": method, "path": path.split("?", 1)[0], "query_string": query.encode(), "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]}, receive)


@pytest.mark.asyncio
async def test_gateway_middleware_public_options_and_missing_auth():
    mw = middleware.JWTVerificationMiddleware(MagicMock())
    call_next = AsyncMock(return_value=Response("ok"))
    assert (await mw.dispatch(request("/health"), call_next)).status_code == 200
    assert (await mw.dispatch(request(method="OPTIONS"), call_next)).status_code == 200
    assert (await mw.dispatch(request(), call_next)).status_code == 401


@pytest.mark.asyncio
async def test_gateway_middleware_token_suspension_and_state(monkeypatch):
    mw = middleware.JWTVerificationMiddleware(MagicMock())
    call_next = AsyncMock(return_value=Response("ok"))
    payload = {"sub": "u", "tenant_id": "t", "realm_access": {"roles": ["doctor"]}}
    monkeypatch.setattr(middleware, "_decode_token", AsyncMock(return_value=payload))
    monkeypatch.setattr(middleware, "_introspect_token", AsyncMock())
    monkeypatch.setattr(middleware, "is_tenant_suspended", AsyncMock(return_value=False))
    response = await mw.dispatch(request(headers={"Authorization": "Bearer token"}), call_next)
    assert response.status_code == 200
    assert call_next.await_count == 1
    monkeypatch.setattr(middleware, "is_tenant_suspended", AsyncMock(return_value=True))
    response = await mw.dispatch(request("/api/v1/reception/x", headers={"Authorization": "Bearer token"}), call_next)
    assert response.status_code == 403

    super_payload = {"type": "superadmin", "sub": "sa", "tenant_id": "t", "realm_access": {"roles": []}}
    monkeypatch.setattr(middleware, "_decode_token", AsyncMock(return_value=super_payload))
    assert (await mw.dispatch(request(headers={"Authorization": "Bearer token"}), call_next)).status_code == 200
    monkeypatch.setattr(middleware, "_decode_token", AsyncMock(return_value=payload))
    monkeypatch.setattr(middleware, "_introspect_token", AsyncMock(side_effect=HTTPException(401, detail="inactive")))
    monkeypatch.setattr(middleware, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr(middleware.settings, "keycloak_introspect", True)
    assert (await mw.dispatch(request(headers={"Authorization": "Bearer token"}), call_next)).status_code == 401


@pytest.mark.asyncio
async def test_gateway_jwt_helpers_and_introspection(monkeypatch):
    assert middleware._issuer("r").endswith("/realms/r")
    assert middleware._extract_realm_from_iss("bad") is None
    with pytest.raises(HTTPException): middleware._build_rsa_key({}, "x")
    with pytest.raises(HTTPException): await middleware._decode_token("bad")
    token = jwt.encode({"sub": "x"}, settings.secret_key, algorithm="HS256", headers={"kid": "superadmin-key"})
    assert (await middleware._decode_token(token))["sub"] == "x"
    cache = __import__("cachetools").TTLCache(maxsize=10, ttl=300)
    cache[f"jwks:{middleware.settings.keycloak_realm}"] = {"keys": []}
    setattr(middleware._fetch_jwks, "_cache", cache)
    assert await middleware._fetch_jwks() == {"keys": []}
    middleware._introspect_token._cache = {"cached": True, "inactive": False}
    await middleware._introspect_token("cached")
    with pytest.raises(HTTPException): await middleware._introspect_token("inactive")

    class Resp:
        def json(self): return {"keys": []}
        def raise_for_status(self): return None
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return Resp()
        async def post(self, *a, **k): return type("R", (), {"json": lambda s: {"active": False}, "raise_for_status": lambda s: None})()
    import app.middleware as m
    monkeypatch.setattr(m.httpx, "AsyncClient", lambda **k: Client())
    middleware._fetch_jwks._cache = {}
    assert await middleware._fetch_jwks("new") == {"keys": []}
    middleware._introspect_token._cache = {}
    with pytest.raises(HTTPException): await middleware._introspect_token("new")

    if hasattr(middleware._fetch_jwks, "_cache"):
        delattr(middleware._fetch_jwks, "_cache")
    class FreshClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return Resp()
    monkeypatch.setattr(middleware.httpx, "AsyncClient", lambda **k: FreshClient())
    assert await middleware._fetch_jwks("fresh") == {"keys": []}
    rsa_token = jwt.encode({"iss": f"{settings.keycloak_url}/realms/r", "sub": "x"}, settings.secret_key, algorithm="HS256", headers={"kid": "rsa"})
    monkeypatch.setattr(middleware, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "rsa"}]}))
    monkeypatch.setattr(middleware.jwt, "decode", lambda *a, **k: {"sub": "x"})
    assert (await middleware._decode_token(rsa_token))["sub"] == "x"
    monkeypatch.setattr(middleware.jwt, "decode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(HTTPException): await middleware._decode_token(rsa_token)
    expired = jwt.encode({"exp": 1}, settings.secret_key, algorithm="HS256", headers={"kid": "superadmin-key"})
    with pytest.raises(HTTPException): await middleware._decode_token(expired)
    if hasattr(middleware._introspect_token, "_cache"):
        delattr(middleware._introspect_token, "_cache")
    monkeypatch.setattr(middleware, "_fetch_jwks", AsyncMock(return_value={"keys": []}))


@pytest.mark.asyncio
async def test_access_log_and_proxy_paths(monkeypatch):
    access = middleware.AccessLogMiddleware(MagicMock())
    response = await access.dispatch(request(), AsyncMock(return_value=Response("ok")))
    assert "X-Request-ID" in response.headers
    assert (await access.dispatch(request(method="HEAD"), AsyncMock(return_value=Response("ok")))).status_code == 200
    assert proxy.resolve_service("/unknown") is None
    assert proxy.resolve_service("/api/v1/reception/x")
    proxy._http_client = None
    client = MagicMock()
    monkeypatch.setattr(proxy.httpx, "AsyncClient", lambda **kwargs: client)
    assert proxy._get_client() is proxy._get_client()
    response = await proxy.proxy(request("/health"), "health")
    assert response.status_code == 200
    response = await proxy.proxy(request("/unknown"), "unknown")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_proxy_forwarding_and_tenant_headers(monkeypatch):
    class Upstream:
        content = b"ok"; status_code = 201; headers = {"content-type": "application/json"}
        async def request(self, **kwargs):
            self.kwargs = kwargs
            return self
    upstream = Upstream()
    monkeypatch.setattr(proxy, "_get_client", lambda: upstream)
    monkeypatch.setattr(proxy, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr(proxy, "get_tenant_db_url", AsyncMock(return_value="postgresql://tenant"))
    req = request("/api/v1/reception/x?a=1", headers={"Authorization": "Bearer x", "X-Request-ID": "r"})
    req.state.tenant_id = "t"; req.state.token_payload = {}; req.state.request_id = "r"
    result = await proxy.proxy(req, "api/v1/reception/x")
    assert result.status_code == 201
    assert upstream.kwargs["headers"]["X-Tenant-DB"] == "postgresql://tenant"
    monkeypatch.setattr(proxy, "get_tenant_db_url", AsyncMock(return_value=None))
    req2 = request("/api/v1/reception/x")
    req2.state.tenant_id = "t"; req2.state.token_payload = {"type": "superadmin"}
    result = await proxy.proxy(req2, "api/v1/reception/x")
    assert result.status_code == 201


@pytest.mark.asyncio
async def test_tenant_cache_and_db_fallbacks(monkeypatch):
    redis = AsyncMock()
    monkeypatch.setattr(tenant, "_redis", redis)
    redis.get.return_value = '"cached-dsn"'
    assert await tenant.get_tenant_db_url("t") == "cached-dsn"
    assert await tenant.get_tenant_db_url("") is None
    redis.get.return_value = '["reception"]'
    assert await tenant.is_module_enabled("t", "reception") is True
    redis.get.return_value = "1"
    assert await tenant.is_tenant_suspended("t") is True
    assert await tenant.get_tenant_realm("") is None


@pytest.mark.asyncio
async def test_tenant_database_resolution_paths(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(tenant.settings, "tenant_db_encryption_key", key)
    dsn = "postgresql://db"
    encrypted = Fernet(key.encode()).encrypt(dsn.encode()).decode()
    redis = AsyncMock(); redis.get.return_value = None
    monkeypatch.setattr(tenant, "_redis", redis)
    result = MagicMock(); result.scalar.side_effect = [encrypted, "realm"]
    db = MagicMock(); db.execute.return_value = result
    monkeypatch.setattr(tenant, "get_master_db", lambda: db)
    assert await tenant.get_tenant_db_url("t") == dsn
    assert await tenant.get_tenant_realm("t") == "realm"

    result.scalar.side_effect = None
    result.scalar.return_value = "suspended"
    assert await tenant.is_tenant_suspended("t") is True
    result.fetchone.return_value = ("[\"pharmacy\"]",)
    assert await tenant.is_module_enabled("t", "pharmacy") is True
    redis.get.return_value = None
    result.scalar.return_value = None
    assert await tenant.get_tenant_db_url("missing") is None
    result.fetchone.return_value = None
    assert await tenant.is_module_enabled("t", "ward") is True


@pytest.mark.asyncio
async def test_tenant_failure_fallbacks(monkeypatch):
    redis = AsyncMock()
    redis.get.side_effect = RuntimeError("redis")
    redis.set.side_effect = RuntimeError("redis")
    monkeypatch.setattr(tenant, "_redis", redis)
    monkeypatch.setattr(tenant, "get_master_db", MagicMock(side_effect=RuntimeError("db")))
    assert await tenant.get_tenant_realm("t") is None
    assert await tenant.is_tenant_suspended("t") is False
    with pytest.raises(RuntimeError):
        await tenant.is_module_enabled("t", "ward")


@pytest.mark.asyncio
async def test_tenant_engine_redis_and_empty_module_paths(monkeypatch):
    tenant._master_engine = None
    session = object()
    factory = MagicMock(return_value=session)
    monkeypatch.setattr(tenant, "create_engine", MagicMock(return_value="engine"))
    monkeypatch.setattr(tenant, "sessionmaker", MagicMock(return_value=factory))
    assert tenant._get_master_engine() == ("engine", factory)
    assert tenant.get_master_db() is session
    tenant._redis = None
    redis = AsyncMock()
    monkeypatch.setattr(tenant.aioredis, "from_url", MagicMock(return_value=redis))
    assert await tenant._get_redis() is redis
    assert await tenant.is_module_enabled("", "ward") is False

    redis.get.side_effect = RuntimeError("cache")
    redis.set.side_effect = RuntimeError("cache")
    row = MagicMock(); row.scalar.return_value = None; row.fetchone.return_value = None
    db = MagicMock(); db.execute.return_value = row
    monkeypatch.setattr(tenant, "get_master_db", lambda: db)
    assert await tenant.get_tenant_db_url("missing") is None
    assert await tenant.is_module_enabled("t", "ward") is True


@pytest.mark.asyncio
async def test_gateway_messaging_helpers(monkeypatch):
    from app.messaging import connection, publisher, subscriber
    class Conn:
        is_closed = False
        async def channel(self): return MagicMock()
        async def close(self): self.is_closed = True
    conn = Conn()
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=conn))
    connection._connection = None
    assert await connection.get_connection() is conn
    assert await connection.get_channel()
    channel = MagicMock()
    channel.declare_exchange = AsyncMock(return_value=MagicMock())
    await connection.declare_exchange(channel)
    await connection.close_connection()

    exchange = MagicMock()
    exchange.publish = AsyncMock()
    monkeypatch.setattr(publisher, "get_channel", AsyncMock(return_value=channel))
    monkeypatch.setattr(publisher, "declare_exchange", AsyncMock(return_value=exchange))
    await publisher.publish_event("tenant.created", {"tenant": "t"})
    monkeypatch.setattr(publisher, "get_channel", AsyncMock(side_effect=RuntimeError("down")))
    await publisher.publish_event("tenant.failed", {})

    queue = MagicMock(name="queue"); queue.name = "events"; queue.bind = AsyncMock()
    class Iterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def __aiter__(self): return self
        async def __anext__(self): raise StopAsyncIteration
    queue.iterator.return_value = Iterator()
    chan = MagicMock(); chan.set_qos = AsyncMock(); chan.declare_queue = AsyncMock(return_value=queue)
    conn.channel = AsyncMock(return_value=chan)
    monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=conn))
    monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value=exchange))
    await subscriber.start_consumer("gateway", ["a"], AsyncMock())
    class BadMessage:
        body = b"not-json"; routing_key = "bad"
        class Process:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): pass
        def process(self): return self.Process()
    class One(Iterator):
        def __init__(self): self.done = False
        async def __anext__(self):
            if self.done: raise StopAsyncIteration
            self.done = True; return BadMessage()
    queue.iterator.return_value = One()
    await subscriber.start_consumer("gateway", [], AsyncMock())
    class GoodMessage(BadMessage):
        body = b'{"ok": true}'
    class Good(Iterator):
        def __init__(self): self.done = False
        async def __anext__(self):
            if self.done: raise StopAsyncIteration
            self.done = True; return GoodMessage()
    queue.iterator.return_value = Good()
    handler = AsyncMock()
    await subscriber.start_consumer("gateway", [], handler)
    handler.assert_awaited_once_with("bad", {"ok": True})
    task = await subscriber.run_consumer_task("gateway", [], AsyncMock())
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task


def test_tenant_crypto_and_url_helpers(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(settings, "tenant_db_encryption_key", Fernet.generate_key().decode())
    cipher = tenant._get_cipher()
    encrypted = cipher.encrypt(b"postgresql://x").decode()
    assert tenant.decrypt_dsn(encrypted) == "postgresql://x"


@pytest.mark.asyncio
async def test_gateway_health_direct(monkeypatch):
    from app.main import health_check
    import sys
    monkeypatch.setitem(sys.modules, "psutil", SimpleNamespace(
        cpu_percent=lambda interval: 1.0,
        cpu_count=lambda: 2,
        virtual_memory=lambda: SimpleNamespace(total=10, available=5, percent=50),
        disk_usage=lambda path: SimpleNamespace(total=20, free=10, percent=50),
    ))
    result = await health_check()
    assert result["service"] == "api-gateway"
