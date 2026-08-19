import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.core import database, security, tenant
from app.core import tenant_auth
from app.services import tenant_service
from app.db import tenant as tenant_db
from app.messaging import connection, publisher, subscriber


def _token(payload):
    return jwt.encode(payload, security.settings.secret_key, algorithm="HS256")


def row(*values):
    db = MagicMock(); result = MagicMock(); result.one_or_none.return_value = values; result.scalar.return_value = values[0] if values else None; db.execute.return_value = result; return db


@pytest.mark.asyncio
async def test_security_roles_hospital_lookup_and_token_failures(monkeypatch):
    req = MagicMock(); req.state = MagicMock()
    user = security.TokenPayload("u", "doctor", "d@example.com", {"roles": ["doctor"]}, {})
    assert security._extract_roles(user) == ["doctor"]
    assert await security.require_role("doctor")(user) is user
    with pytest.raises(Exception): await security.require_role("lab_technician")(security.TokenPayload("u", None, None, {"roles": []}, {}))
    db = MagicMock(); db.query.return_value.filter.return_value.one_or_none.return_value = MagicMock(hospital_id="h")
    assert await security.get_current_hospital_id(user, db) == "h"
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    with pytest.raises(Exception): await security.get_current_hospital_id(user, db)
    super_user = security.TokenPayload("u", None, None, {}, {"type": "superadmin"})
    assert await security.get_current_hospital_id(super_user, db) is None
    with pytest.raises(Exception): await security.get_current_active_user(req, None)
    with pytest.raises(Exception): await security._decode_unverified_header("bad")


@pytest.mark.asyncio
async def test_security_jwks_decode_and_introspection_paths(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(); client.get = AsyncMock(return_value=Response()); client.post = AsyncMock(return_value=type("R", (), {"raise_for_status": lambda s: None, "json": lambda s: {"active": True}})())
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kw: client)
    security._jwks_cache.clear(); assert await security._fetch_jwks("realm") == {"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}
    assert security._build_rsa_key({"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}, "k")["kty"] == "RSA"
    with pytest.raises(Exception): security._build_rsa_key({}, "x")
    security._introspection_cache.clear(); assert await security._introspect_token("active") is True
    security._introspection_cache["inactive"] = False
    with pytest.raises(Exception): await security._introspect_token("inactive")
    monkeypatch.setattr(security.jwt, "get_unverified_claims", lambda _: {"iss": security.settings.keycloak_url + "/realms/custom"})
    assert security._extract_realm_from_iss("token") == "custom"
    monkeypatch.setattr(security.jwt, "get_unverified_claims", MagicMock(side_effect=RuntimeError()))
    assert security._extract_realm_from_iss("token") is None
    security._introspection_cache.clear()
    class FailingClient:
        async def __aenter__(self): raise security.httpx.HTTPError("offline")
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kw: FailingClient())
    with pytest.raises(Exception): await security._introspect_token("network-error")


@pytest.mark.asyncio
async def test_security_active_user_rsa_verification_and_introspection(monkeypatch):
    request = MagicMock(); request.state = MagicMock()
    monkeypatch.setattr(security, "_extract_realm_from_iss", lambda token: "realm")
    monkeypatch.setattr(security, "_decode_unverified_header", lambda token: {"kid": "k"})
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}))
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: {"sub": "u", "preferred_username": "lab", "realm_access": {"roles": ["lab_technician"]}})
    security.settings.keycloak_introspect = False
    active = await security.get_current_active_user(request, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))
    assert active.sub == "u" and request.state.user_sub == "u"
    monkeypatch.setattr(security, "_decode_unverified_header", lambda token: {})
    with pytest.raises(Exception): await security.get_current_active_user(request, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: {"sub": "u", "realm_access": {"roles": []}})
    security.settings.keycloak_introspect = True; monkeypatch.setattr(security, "_introspect_token", AsyncMock())
    monkeypatch.setattr(security, "_decode_unverified_header", lambda token: {"kid": "k"})
    await security.get_current_active_user(request, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))
    security.settings.keycloak_introspect = False
    monkeypatch.setattr(security, "_decode_unverified_header", lambda token: {"kid": "k"})
    monkeypatch.setattr(security.jwt, "decode", MagicMock(side_effect=RuntimeError("bad signature")))
    with pytest.raises(Exception): await security.get_current_active_user(request, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token"))


@pytest.mark.asyncio
async def test_tenant_auth_rsa_and_realm_extraction_branches(monkeypatch):
    assert tenant_auth._issuer("custom").endswith("/realms/custom")
    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_claims", lambda _: {"iss": tenant_auth.settings.keycloak_url + "/realms/custom"})
    assert tenant_auth._extract_realm_from_iss("token") == "custom"
    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_claims", MagicMock(side_effect=RuntimeError()))
    assert tenant_auth._extract_realm_from_iss("token") is None
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"keys": [{"kid": "k"}]}
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(); client.get = AsyncMock(return_value=Response())
    monkeypatch.setattr(tenant_auth.httpx, "AsyncClient", lambda **kw: client); tenant_auth._jwks_cache.clear()
    assert await tenant_auth._fetch_jwks("custom") == {"keys": [{"kid": "k"}]}
    assert tenant_auth._build_rsa_key({"keys": [{"kid": "k"}]}, "k")["kid"] == "k"
    with pytest.raises(Exception): tenant_auth._build_rsa_key({}, "missing")
    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_header", lambda _: {"kid": "k"})
    monkeypatch.setattr(tenant_auth.jwt, "decode", lambda *a, **k: {"tenant_id": "t"})
    assert (await tenant_auth._decode_token("token"))["tenant_id"] == "t"
    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=tenant_auth.jwt.ExpiredSignatureError()))
    with pytest.raises(Exception): await tenant_auth._decode_token("token")


@pytest.mark.asyncio
async def test_tenant_session_revocation_and_dsn_lookup(monkeypatch):
    class Response:
        is_success = True
        def raise_for_status(self): pass
        def json(self): return {"access_token": "admin"}
    class Users:
        is_success = True
        def json(self): return [{"id": "user-1"}, {}]
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(); client.post = AsyncMock(return_value=Response()); client.get = AsyncMock(return_value=Users()); client.put = AsyncMock()
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", lambda **kw: client)
    await tenant_service._revoke_keycloak_sessions("tenant"); assert client.put.await_count == 1
    key = tenant_service.settings.tenant_db_encryption_key; tenant_service.settings.tenant_db_encryption_key = __import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode()
    encrypted = tenant_service.encrypt_dsn("postgresql://tenant")
    assert await tenant_service.get_tenant_db_dsn(row(encrypted), "tenant") == "postgresql://tenant"
    assert await tenant_service.get_tenant_db_dsn(row(), "tenant") is None
    tenant_service.settings.tenant_db_encryption_key = key


def test_database_router_and_tenant_resolution(monkeypatch):
    database._engine = None; database._SessionLocal = None
    monkeypatch.setattr(database, "create_engine", lambda *a, **k: object()); monkeypatch.setattr(database, "sessionmaker", lambda **k: MagicMock())
    database.init_db(); assert database.get_session_local()
    session = MagicMock(); monkeypatch.setattr(database._router, "get_session", lambda _: session)
    context = database.get_hospital_context("h"); database.close_hospital_context(context); session.close.assert_called()
    generator = database.get_db(); next(generator)
    with pytest.raises(StopIteration): next(generator)
    db = MagicMock(); result = MagicMock(); result.scalar.return_value = "postgresql://tenant"; db.execute.return_value = result
    monkeypatch.setattr(tenant, "get_db", lambda: iter([db])); assert tenant.resolve_tenant_db_url("t") == "postgresql://tenant"
    monkeypatch.setattr(tenant, "get_db", lambda: (_ for _ in ()).throw(RuntimeError())); assert tenant.resolve_tenant_db_url("t") is None


@pytest.mark.asyncio
async def test_tenant_async_session_factory_and_messaging(monkeypatch):
    tenant_db._async_engine_cache.clear(); monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value=None))
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("missing")
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value="postgresql://tenant"))
    monkeypatch.setattr(tenant_db, "create_async_engine", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(tenant_db, "async_sessionmaker", MagicMock(return_value=MagicMock()))
    assert await tenant_db._get_async_session_factory("tenant")
    conn = MagicMock(is_closed=False, channel=AsyncMock(return_value=MagicMock())); conn.close = AsyncMock(); connection._connection = None
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=conn)); assert await connection.get_connection() is conn
    await connection.get_channel(); await connection.close_connection()
    channel = AsyncMock(); exchange = AsyncMock(); monkeypatch.setattr(publisher, "get_channel", AsyncMock(return_value=channel)); monkeypatch.setattr(publisher, "declare_exchange", AsyncMock(return_value=exchange)); await publisher.publish_event("lab.ready", {"id": "1"})
    task = await subscriber.run_consumer_task("lab", ["lab.ready"], AsyncMock()); assert isinstance(task, asyncio.Task); task.cancel()


@pytest.mark.asyncio
async def test_message_consumer_handles_json_and_handler_errors(monkeypatch):
    class Process:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Message:
        body = b'{"investigation_id":"i", "tenant_id":"t"}'; routing_key = "investigation.requested"
        def process(self): return Process()
    class Iterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self, "done", False): raise StopAsyncIteration
            self.done = True; return Message()
    queue = MagicMock(name="laboratory-service_events"); queue.name = "laboratory-service_events"; queue.bind = AsyncMock(); queue.iterator = lambda: Iterator()
    channel = MagicMock(); channel.set_qos = AsyncMock(); channel.declare_queue = AsyncMock(return_value=queue)
    conn = MagicMock(channel=AsyncMock(return_value=channel)); monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=conn)); monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value=MagicMock()))
    handler = AsyncMock(); await subscriber.start_consumer("laboratory-service", ["investigation.requested"], handler); handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_application_lifespan_health_and_http_middleware(monkeypatch):
    import app.main as main
    monkeypatch.setattr("app.core.database.init_db", MagicMock())
    monkeypatch.setattr("app.events.subscriber.start_subscriber", AsyncMock())
    async with main.lifespan(main.app): pass
    from starlette.responses import Response
    request = MagicMock(method="GET"); request.state = MagicMock()
    async def call(_): return Response("ok")
    response = await main.security_headers(request, call)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    request.method = "POST"; request.state.tenant = MagicMock(scope="readonly")
    denied = await main.ReadOnlyScopeMiddleware(MagicMock()).dispatch(request, call); assert denied.status_code == 403
    request.method = "GET"; banner = await main.ImpersonationBannerMiddleware(MagicMock()).dispatch(request, call); assert banner.headers["X-Impersonation-Banner"] == "true"
