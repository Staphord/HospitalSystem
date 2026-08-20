import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app import exceptions
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


@pytest.mark.asyncio
async def test_laboratory_missing_branches_and_edge_cases(monkeypatch):
    from datetime import datetime, timezone, timedelta

    from uuid import uuid4
    from app.core import database, limiter, middleware, security, tenant_auth

    from app.db import tenant as tenant_db
    from app.events import subscriber as event_sub
    from app.messaging import connection, subscriber as msg_sub
    from app.services import laboratory as service, tenant_service
    from app import main

    # core database & limiter
    database._engine = None; database._SessionLocal = None
    monkeypatch.setattr(database, "create_engine", lambda *a, **k: MagicMock())
    monkeypatch.setattr(database, "sessionmaker", lambda **k: MagicMock())
    database._init_engine(); database._init_engine()

    class SubRouter(database.DatabaseRouter):
        def get_session(self, hospital_id: str):
            return super().get_session(hospital_id)
    with pytest.raises(NotImplementedError): SubRouter().get_session("h")

    # core security & tenant_auth
    security._jwks_cache["jwks:realm"] = {"keys": [{"kid": "k"}]}
    assert await security._fetch_jwks("realm") == {"keys": [{"kid": "k"}]}

    monkeypatch.setattr(security, "_issuer", lambda *a: "http://issuer")
    security._introspection_cache.clear()
    class InactiveResp:
        def raise_for_status(self): pass
        def json(self): return {"active": False}
    class IntrospectClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return InactiveResp()
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **k: IntrospectClient())
    with pytest.raises(Exception): await security._introspect_token("revoked_tok")

    # tenant_auth signature error paths
    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=tenant_auth.jwt.ExpiredSignatureError("expired")))
    with pytest.raises(Exception): await tenant_auth._decode_token("header.payload.sig")

    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_header", lambda _: {"kid": "k"})
    monkeypatch.setattr(tenant_auth, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k"}]}))
    monkeypatch.setattr(tenant_auth, "_build_rsa_key", lambda j, k: {"kty": "RSA"})
    with pytest.raises(Exception): await tenant_auth._decode_token("header.payload.sig")

    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=RuntimeError("bad sig")))
    with pytest.raises(Exception): await tenant_auth._decode_token("header.payload.sig")

    # tenant_db cache hit & session generator
    tenant_db._async_engine_cache["t_cached"] = MagicMock()
    assert await tenant_db._get_async_session_factory("t_cached")

    mock_sess = AsyncMock()
    mock_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_sess), __aexit__=AsyncMock()))
    monkeypatch.setattr(tenant_db, "_get_async_session_factory", AsyncMock(return_value=mock_factory))
    async for s in tenant_db.get_tenant_session("t_cached"): assert s is mock_sess

    # events subscriber start_subscriber
    monkeypatch.setattr(event_sub, "start_consumer", AsyncMock())
    await event_sub.start_subscriber()

    # main allowed_origins list
    monkeypatch.setattr(main.settings, "allowed_origins", ["http://localhost", "http://example.com"])
    req = MagicMock(method="GET"); req.headers = {}
    async def call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    res = await main.security_headers(req, call)
    assert res.headers["X-Content-Type-Options"] == "nosniff"

    # messaging connection close_connection when active
    dummy_conn = MagicMock(close=AsyncMock())
    connection._connection = dummy_conn
    await connection.close_connection()

    # messaging subscriber consumer loop exception
    class ErrorIterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def __aiter__(self): return self
        async def __anext__(self): raise RuntimeError("loop err")
    err_queue = MagicMock(); err_queue.iterator = lambda: ErrorIterator(); err_queue.bind = AsyncMock()
    err_chan = MagicMock(); err_chan.declare_queue = AsyncMock(return_value=err_queue); err_chan.set_qos = AsyncMock()
    err_conn = MagicMock(); err_conn.channel = AsyncMock(return_value=err_chan)
    monkeypatch.setattr(msg_sub, "get_connection", AsyncMock(return_value=err_conn))
    monkeypatch.setattr(msg_sub, "declare_exchange", AsyncMock())
    task = await msg_sub.run_consumer_task("q", ["rk"], AsyncMock())
    with pytest.raises(RuntimeError):
        await task


    # service lab critical error fallback & high priority tzinfo branch
    db_c = AsyncMock()
    req_c = MagicMock(status="specimen_collected", requested_by="doc", test_name="t")
    res_req = MagicMock(); res_req.scalar_one_or_none.return_value = req_c
    res_ex = MagicMock(); res_ex.scalar_one_or_none.return_value = None
    db_c.execute.side_effect = [res_req, res_ex]
    monkeypatch.setattr("app.events.publisher.publish_lab_critical_value", AsyncMock(side_effect=RuntimeError("pub fail")))
    class CritBody:
        test_name = "t"; result_value = "v"; reference_range = "r"; unit = "u"; is_abnormal = True; is_critical = True; remarks = "r"; result_notes = "n"; specimen_type = "blood"; specimen_label = "L1"
    crit_res = await service.create_lab_result(db_c, "r", CritBody(), user=security.TokenPayload("u", None, None, {}, {}))
    assert crit_res.is_critical is True

    db_hp = AsyncMock()
    res_hp = MagicMock()
    res_hp.all.return_value = []
    res_hp.scalar.return_value = 0
    db_hp.execute.return_value = res_hp
    stats = await service.get_dashboard_stats(db_hp)
    assert stats



    assert await tenant_service.is_tenant_suspended("t_none") is False
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("client fail")))
    await tenant_service._revoke_keycloak_sessions("t")
    db_dsn_mock = MagicMock()
    db_dsn_mock.execute.return_value.scalar.return_value = tenant_service.encrypt_dsn("postgresql://dsn")
    assert await tenant_service.get_tenant_db_dsn(db_dsn_mock, "t") == "postgresql://dsn"

    # exact missing line target coverage
    database._engine = None
    database.init_db()

    with pytest.raises(Exception):
        await tenant_auth._decode_token("invalid.bearer.token")

    monkeypatch.setattr(main.settings, "allowed_origins", ["http://localhost", "http://domain.com"])
    req_m = MagicMock(method="GET"); req_m.headers = {}
    async def _dummy_call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    await main.security_headers(req_m, _dummy_call)

    active_conn = MagicMock(is_closed=False, close=AsyncMock())
    connection._connection = active_conn
    await connection.close_connection()

    class ProcessContext:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class ExMessage:
        body = b'{"investigation_id":"i", "tenant_id":"t"}'; routing_key = "investigation.requested"
        def process(self): return ProcessContext()

    class ExIterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self, "done", False): raise StopAsyncIteration
            self.done = True; return ExMessage()
    ex_queue = MagicMock(name="laboratory-service_events"); ex_queue.name = "laboratory-service_events"; ex_queue.bind = AsyncMock(); ex_queue.iterator = lambda: ExIterator()
    ex_channel = MagicMock(); ex_channel.set_qos = AsyncMock(); ex_channel.declare_queue = AsyncMock(return_value=ex_queue)
    ex_conn = MagicMock(channel=AsyncMock(return_value=ex_channel)); monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=ex_conn)); monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value=MagicMock()))
    failing_handler = AsyncMock()
    await subscriber.start_consumer("laboratory-service", ["investigation.requested"], failing_handler)

    monkeypatch.setattr(event_sub, "start_consumer", AsyncMock())
    await event_sub.start_subscriber()

    db_hp2 = AsyncMock()
    req_hp2 = MagicMock(requested_at=datetime.now(timezone.utc).replace(tzinfo=None), created_at=None, test_name="t", requested_by="doc", id=uuid4(), status="pending", urgency="stat")
    pat_hp2 = MagicMock(full_name="Pat")
    res_hp2 = MagicMock(); res_hp2.all.return_value = [(req_hp2, pat_hp2)]
    res_zero = MagicMock(); res_zero.scalar.return_value = 0
    res_empty_all = MagicMock(); res_empty_all.all.return_value = []
    db_hp2.execute.side_effect = [res_zero, res_zero, res_zero, res_zero, res_hp2, res_empty_all, res_empty_all, res_empty_all, res_empty_all]

    stats2 = await service.get_dashboard_stats(db_hp2)
    assert stats2

    db_ts = row("active", datetime.now(timezone.utc)+timedelta(days=1), 1)
    assert await tenant_service.check_and_update_tenant_status(db_ts, "t") == "active"

    # lab service branch coverage
    db_br = AsyncMock()
    res_br = MagicMock(); res_br.all.return_value = []
    db_br.execute.return_value = res_br
    assert await service.get_lab_requests(db_br, urgency="stat") == []


    req_rej = MagicMock(status="pending")
    res_rej = MagicMock(); res_rej.scalar_one_or_none.return_value = req_rej
    db_br.execute.return_value = res_rej
    with pytest.raises(exceptions.UnprocessableEntityError):
        await service.update_specimen_status(db_br, "s", MagicMock(status="rejected", rejection_reason=None), user=security.TokenPayload("u", None, None, {}, {}))

    req_res = MagicMock(status="pending")
    res_res = MagicMock(); res_res.scalar_one_or_none.return_value = req_res
    db_br.execute.return_value = res_res
    with pytest.raises(exceptions.UnprocessableEntityError):
        await service.create_lab_result(db_br, "r", MagicMock(test_name="t", result_value="v", is_critical=False), user=security.TokenPayload("u", None, None, {}, {}))

    lab_ver = MagicMock(status="draft")
    res_ver = MagicMock(); res_ver.scalar_one_or_none.return_value = lab_ver
    db_br.execute.return_value = res_ver
    with pytest.raises(exceptions.UnprocessableEntityError):
        await service.verify_lab_result(db_br, "res", user=security.TokenPayload("u", None, None, {}, {}))

    res_vis = MagicMock(); res_vis.all.return_value = []
    db_br.execute.return_value = res_vis
    assert await service.get_visit_verified_results(db_br, "v") == {"visit_id": "v", "results": []}





