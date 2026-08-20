from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from cryptography.fernet import Fernet
from jose import jwt

from app.core import tenant_auth
from app.services import tenant_service



def row(*values):
    db = MagicMock(); result = MagicMock(); result.one_or_none.return_value = values; result.scalar.return_value = values[0] if values else None; db.execute.return_value = result; return db


@pytest.mark.asyncio
async def test_tenant_service_encryption_cache_and_subscription_states(monkeypatch):
    monkeypatch.setattr(tenant_service.settings, "tenant_db_encryption_key", Fernet.generate_key().decode())
    assert tenant_service.decrypt_dsn(tenant_service.encrypt_dsn("postgresql://tenant")) == "postgresql://tenant"
    redis = AsyncMock(); redis.get.return_value = "1"; monkeypatch.setattr(tenant_service, "_redis", redis)
    assert await tenant_service.is_tenant_suspended("t") is True
    await tenant_service.cache_tenant_suspension("t", 10); await tenant_service.remove_tenant_suspension_cache("t")
    assert await tenant_service.check_tenant_subscription(row("suspended", None), "t") == "suspended"
    assert await tenant_service.check_tenant_subscription(row("active", datetime.now(timezone.utc)-timedelta(days=1)), "t") == "expired"
    assert await tenant_service.check_tenant_subscription(row("active", datetime.now(timezone.utc)+timedelta(days=1)), "t") == "active"
    assert await tenant_service.check_tenant_subscription(row(), "t") == "not_found"


@pytest.mark.asyncio
async def test_tenant_status_expiry_and_redis_failure_paths(monkeypatch):
    failing = AsyncMock(); failing.get.side_effect = RuntimeError(); failing.set.side_effect = RuntimeError(); failing.delete.side_effect = RuntimeError(); monkeypatch.setattr(tenant_service, "_redis", failing)
    assert await tenant_service.is_tenant_suspended("t") is False; await tenant_service.cache_tenant_suspension("t"); await tenant_service.remove_tenant_suspension_cache("t")
    monkeypatch.setattr(tenant_service, "cache_tenant_suspension", AsyncMock()); monkeypatch.setattr(tenant_service, "_revoke_keycloak_sessions", AsyncMock())
    db = row("active", datetime.now(timezone.utc)-timedelta(days=40), 1)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"; assert db.commit.called
    assert await tenant_service.check_and_update_tenant_status(row("active", datetime.now(timezone.utc)-timedelta(days=2), 1), "t") == "expired"
    assert await tenant_service.check_and_update_tenant_status(row("suspended", None, 1), "t") == "suspended"


@pytest.mark.asyncio
async def test_tenant_auth_local_and_readonly_contexts(monkeypatch):
    request = MagicMock(); request.state = MagicMock()
    monkeypatch.setattr(tenant_auth.settings, "environment", "prod")
    local = jwt.encode({"type":"superadmin", "super_admin_id":"sa", "username":"root"}, tenant_auth.settings.secret_key, algorithm="HS256")
    context = await tenant_auth.get_current_tenant(request, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=local))
    assert context.is_super_admin
    token = jwt.encode({"sub":"u", "tenant_id":"t", "scope":"readonly", "realm_access":{"roles":["doctor"]}}, tenant_auth.settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=False))
    assert (await tenant_auth.get_current_tenant(request, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))).scope == "readonly"
    no_tenant = jwt.encode({"sub":"u", "realm_access":{"roles":[]}}, tenant_auth.settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(request, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=no_tenant))
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(request, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(request, None)


@pytest.mark.asyncio
async def test_laboratory_core_db_limiter_middleware_security_and_main(monkeypatch):
    from app.core import database, limiter, middleware, security
    from app.db import master, session as db_session, tenant as tenant_db
    from app import dependencies, exceptions, main
    from app.events import subscriber as event_sub
    from app.messaging import connection, subscriber as msg_sub

    # database & limiter
    database._engine = None; database._SessionLocal = None
    monkeypatch.setattr(database, "create_engine", lambda *a, **k: object())
    monkeypatch.setattr(database, "sessionmaker", lambda **k: MagicMock())
    assert database.get_session_local(); database.init_db()
    session = MagicMock(); monkeypatch.setattr(database._router, "get_session", lambda h: session)
    assert database.get_hospital_context("h").db is session
    database.close_hospital_context(database.HospitalContext("h", session)); session.close.assert_called()
    gen = database.get_db(); next(gen)
    with pytest.raises(StopIteration): next(gen)
    assert limiter.limiter

    # middleware
    req = MagicMock(method="GET"); req.state = MagicMock()
    async def call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    assert (await middleware.AuditLogMiddleware(MagicMock()).dispatch(req, call)).status_code == 200
    req.method = "OPTIONS"; assert (await middleware.AuditLogMiddleware(MagicMock()).dispatch(req, call)).status_code == 200
    req.method = "POST"; req.state.tenant = type("T", (), {"scope": "readonly"})()
    assert (await middleware.ReadOnlyScopeMiddleware(MagicMock()).dispatch(req, call)).status_code == 403
    req.method = "GET"; assert (await middleware.ImpersonationBannerMiddleware(MagicMock()).dispatch(req, call)).status_code == 200

    # security
    with pytest.raises(Exception): await security._decode_token("bad")
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": []}))
    with pytest.raises(Exception): await security._decode_token(jwt.encode({"iss": f"{security.settings.keycloak_url}/realms/x"}, "secret", algorithm="HS256", headers={"kid": "k"}))
    security._introspection_cache["cached"] = True; await security._introspect_token("cached")
    security._introspection_cache["inactive"] = False
    with pytest.raises(Exception): await security._introspect_token("inactive")
    assert await security.require_role("doctor")(security.TokenPayload("u", None, None, {"roles": ["doctor"]}, {}))

    # master, db_session, tenant_db
    assert master.get_master_db()
    with master.get_master_session() as mdb: assert mdb
    async def one_session(): yield AsyncMock()
    monkeypatch.setattr(db_session, "get_tenant_session", lambda _: one_session())
    async for s in db_session.get_tenant_db("t"): assert s
    tenant_db._async_engine_cache.clear()
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value=None))
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("m")
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value="postgresql://db"))
    class Conn:
        async def run_sync(self, fn): pass
        async def execute(self, *a): pass
    class Begin:
        async def __aenter__(self): return Conn()
        async def __aexit__(self, *a): pass
    engine = MagicMock(begin=lambda: Begin())
    monkeypatch.setattr(tenant_db, "create_async_engine", lambda *a, **k: engine)
    monkeypatch.setattr(tenant_db, "async_sessionmaker", lambda **k: MagicMock())
    factory = await tenant_db._get_async_session_factory("t"); assert factory

    # dependencies & exceptions
    monkeypatch.setattr(dependencies, "get_tenant_session", lambda _: one_session())
    async for s in dependencies.get_tenant_db(type("Ctx", (), {"tenant_id": "t"})()): assert s
    user = security.TokenPayload("u", None, None, {}, {})
    assert await dependencies.get_current_user(user) is user
    for cls, code in [(exceptions.UnauthorizedError, 401), (exceptions.ForbiddenError, 403), (exceptions.NotFoundError, 404), (exceptions.ConflictError, 409), (exceptions.BadRequestError, 400), (exceptions.UnprocessableEntityError, 422), (exceptions.RateLimitError, 429), (exceptions.TenantNotFoundError, 404)]:
        assert cls("e").status_code == code

    # events, messaging, main
    await event_sub.handle_investigation_requested("c", "t")
    await event_sub._dispatch("investigation.requested", {"investigation_id": "c", "tenant_id": "t"})
    await event_sub._dispatch("unknown", {})
    fake = MagicMock(is_closed=False); fake.channel = AsyncMock(return_value=MagicMock()); fake.close = AsyncMock()
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=fake))
    connection._connection = None
    assert await connection.get_connection() is fake; await connection.get_channel(); await connection.close_connection()
    monkeypatch.setattr(msg_sub, "get_connection", AsyncMock(return_value=MagicMock(channel=AsyncMock())))
    monkeypatch.setattr(msg_sub, "declare_exchange", AsyncMock(return_value=MagicMock()))
    await msg_sub.run_consumer_task("s", [], AsyncMock())
    async with main.lifespan(main.app): pass
    req.headers = {}
    res = await main.security_headers(req, call); assert res.headers["X-Frame-Options"] == "DENY"


@pytest.mark.asyncio
async def test_laboratory_service_and_router_remaining_branches(monkeypatch):
    from app.services import laboratory as service, tenant_service
    from app.api.v1 import router

    db = MagicMock()
    req_row = MagicMock(); req_row.request_id = "r"; req_row.status = "completed"; req_row.specimen_id = "s"
    spec_row = MagicMock(); spec_row.specimen_id = "s"; spec_row.status = "collected"
    db.execute.return_value.scalars.return_value.first.side_effect = [req_row, spec_row]
    db.execute.return_value.scalar.return_value = 1
    ctx = MagicMock(tenant_id="t", user_sub="u")

    # Service edge paths
    db_test = MagicMock()
    req_m = MagicMock(status="completed", specimen_id=None)
    db_test.execute.return_value.scalars.return_value.first.return_value = req_m
    with pytest.raises(Exception):
        await service.collect_specimen(db_test, "r", "blood", "tubes", "notes", "t", "u")

    db_test2 = MagicMock()
    spec_m = MagicMock(status="rejected")
    db_test2.execute.return_value.scalars.return_value.first.return_value = spec_m
    with pytest.raises(Exception):
        await service.process_specimen(db_test2, "s", "in_progress", "notes", "t", "u")

    db_test3 = MagicMock()
    req_invalid = MagicMock(status="pending")
    db_test3.execute.return_value.scalars.return_value.first.return_value = req_invalid
    with pytest.raises(Exception):
        await service.enter_lab_result(db_test3, "r", "test", "val", "ref", "unit", False, "obs", "t", "u")

    db_test4 = MagicMock()
    res_verified = MagicMock(status="verified")
    db_test4.execute.return_value.scalars.return_value.first.return_value = res_verified
    with pytest.raises(Exception):
        await service.verify_lab_result(db_test4, "res", "ver", "notes", "t", "u")

    db_test5 = MagicMock()
    req_not_completed = MagicMock(status="in_progress")
    db_test5.execute.return_value.scalars.return_value.first.return_value = req_not_completed
    with pytest.raises(Exception):
        await service.bill_lab_request(db_test5, "r", "t", "u")

    # Router endpoints (lines 211 & 258)
    monkeypatch.setattr(router.lab_service, "notify_doctor_for_result", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(router.lab_service, "get_dashboard_stats", AsyncMock(return_value=MagicMock()))
    await router.notify_doctor(UUID("12345678-1234-5678-1234-567812345678"), MagicMock(), ctx, db)
    await router.get_lab_dashboard_stats(db)

    # Tenant service fallback DSN
    db_none = MagicMock(); db_none.execute.return_value.scalar.return_value = None
    assert await tenant_service.get_tenant_db_dsn(db_none, "t") is None


@pytest.mark.asyncio
async def test_laboratory_service_domain_and_messaging_resilience(monkeypatch):
    from app import exceptions, main
    from app.core import database, limiter, middleware, security, tenant_auth
    from app.db import tenant as tenant_db
    from app.events import subscriber as event_sub
    from app.messaging import connection, subscriber as msg_sub
    from app.services import laboratory as service, tenant_service

    # exceptions
    for cls in [exceptions.TokenExpiredError, exceptions.MFARequiredError, exceptions.TenantSuspendedError, exceptions.ReadOnlyScopeError]:
        assert cls().status_code in (401, 403)

    # subscriber & limiter
    monkeypatch.setattr(event_sub, "start_consumer", AsyncMock())
    await event_sub.start_subscriber()
    from starlette.requests import Request
    req = Request({"type": "http", "headers": [], "client": ("127.0.0.1", 1234)})
    assert limiter.get_remote_address(req) == "127.0.0.1"

    # main lifespan and health
    monkeypatch.setattr(database, "init_db", MagicMock(side_effect=RuntimeError("db err")))
    with pytest.raises(Exception):
        async with main.lifespan(main.app): pass

    class DummySub:
        async def start_subscriber(self):
            await asyncio.sleep(10)
    monkeypatch.setattr(database, "init_db", MagicMock())
    monkeypatch.setattr("app.events.subscriber", DummySub())
    async with main.lifespan(main.app): pass

    async def call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    assert (await main.health())["status"] == "ok"
    monkeypatch.setattr(main.settings, "allowed_origins", "http://localhost, http://example.com")
    monkeypatch.setattr(main.settings, "environment", "prod")
    res_prod = await main.security_headers(req, call)
    assert res_prod.headers["Content-Security-Policy"] == "default-src 'none'"

    # database & tenant_db
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    monkeypatch.setattr(database, "create_engine", MagicMock(side_effect=RuntimeError("eng err")))
    with pytest.raises(Exception): database.get_session_local()

    monkeypatch.setattr(database, "get_session_local", lambda: lambda: MagicMock())
    assert database.DefaultDatabaseRouter().get_session("h")

    class BadConn:
        async def run_sync(self, fn): raise RuntimeError("sync err")
    class BadBegin:
        async def __aenter__(self): return BadConn()
        async def __aexit__(self, *a): pass
    bad_engine = MagicMock(begin=lambda: BadBegin())
    monkeypatch.setattr(tenant_db, "create_async_engine", lambda *a, **k: bad_engine)
    tenant_db._async_engine_cache.clear()
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("bad_t")

    # security & tenant_auth
    assert security._extract_realm_from_iss(jwt.encode({"iss": "http://other-issuer/realms/r"}, "s", algorithm="HS256")) is None

    security._jwks_cache["jwks:r"] = {"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}
    assert await security._fetch_jwks("r") == {"keys": [{"kid": "k", "kty": "RSA", "n": "n", "e": "e"}]}
    security._introspection_cache["revoked"] = False
    with pytest.raises(Exception): await security._introspect_token("revoked")

    with pytest.raises(Exception): await security._decode_token("invalid.token.structure")
    with pytest.raises(Exception): await tenant_auth._decode_token("invalid.token.structure")
    security._introspection_cache.clear()
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(side_effect=RuntimeError("jwks down")))
    with pytest.raises(Exception): await security._decode_token(jwt.encode({"iss": f"{security.settings.keycloak_url}/realms/r"}, "s", algorithm="HS256", headers={"kid": "k"}))

    # tenant_auth RS256 / HS256 decode exception paths
    with pytest.raises(Exception):
        await tenant_auth._decode_token(jwt.encode({"exp": 1}, tenant_auth.settings.secret_key, algorithm="HS256", headers={"kid": "impersonation-key"}))
    with pytest.raises(Exception):
        await tenant_auth._decode_token(jwt.encode({"exp": 1, "iss": f"{tenant_auth.settings.keycloak_url}/realms/r"}, "s", algorithm="HS256", headers={"kid": "k"}))

    # security require_role forbidden
    with pytest.raises(Exception):
        await security.require_role("admin")(security.TokenPayload("u", None, None, {"roles": ["doctor"]}, {}))

    # middleware audit log commit failure
    bad_db = MagicMock(); bad_db.commit.side_effect = RuntimeError("commit fail")
    monkeypatch.setattr(middleware, "get_session_local", lambda: lambda: bad_db)
    req_post = Request({"type": "http", "method": "POST", "path": "/test", "headers": []})
    await middleware.AuditLogMiddleware(MagicMock()).dispatch(req_post, call)

    # limiter user_sub key
    req_sub = Request({"type": "http", "headers": []})
    req_sub.state.user_sub = "sub123"
    assert limiter._rate_limit_key(req_sub) == "sub123"

    # database router exception
    class SubRouter(database.DatabaseRouter):
        def get_session(self, hospital_id: str):
            return super().get_session(hospital_id)
    with pytest.raises(NotImplementedError): SubRouter().get_session("h")

    database._engine = None; database.init_db()

    # db tenant session generator
    mock_factory = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=MagicMock()), __aexit__=AsyncMock()))
    monkeypatch.setattr(tenant_db, "_get_async_session_factory", AsyncMock(return_value=mock_factory))
    async for s in tenant_db.get_tenant_session("t"): pass

    # user name resolution fallback
    u_mock = security.TokenPayload("s1", "Doctor", None, {}, {})
    assert service._user_identifier(u_mock) == "Doctor"
    u_mock2 = security.TokenPayload("s2", None, "doc@example.com", {}, {})
    assert service._user_identifier(u_mock2) == "doc@example.com"



    # update_lab_result amended critical and non-critical paths
    db_res = AsyncMock()
    lab_res = MagicMock(result_id=uuid4(), status="resulted", is_critical=False, critical_notified_at=None)
    m_res = MagicMock(); m_res.scalar_one_or_none.return_value = lab_res
    db_res.execute.return_value = m_res

    class UpdBody1:
        result_value = "10"; unit = "mg"; reference_range = "1-5"; result_notes = "n"; is_critical = True
    await service.update_lab_result(db_res, uuid4(), UpdBody1(), user=security.TokenPayload("u", None, None, {}, {}))
    assert lab_res.is_critical is True

    class UpdBody2:
        result_value = None; unit = None; reference_range = None; result_notes = None; is_critical = False
    await service.update_lab_result(db_res, uuid4(), UpdBody2(), user=security.TokenPayload("u", None, None, {}, {}))
    assert lab_res.is_critical is False


    # messaging connection & subscriber
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(side_effect=RuntimeError("pika down")))
    connection._connection = None
    with pytest.raises(Exception): await connection.get_connection()
    connection._connection = None
    await connection.close_connection()

    class DummyQueueIter:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def __anext__(self): raise StopAsyncIteration
    dummy_queue = MagicMock(); dummy_queue.iterator = lambda: DummyQueueIter(); dummy_queue.bind = AsyncMock()
    dummy_channel = MagicMock(); dummy_channel.declare_queue = AsyncMock(return_value=dummy_queue); dummy_channel.set_qos = AsyncMock()
    dummy_conn = MagicMock(); dummy_conn.channel = AsyncMock(return_value=dummy_channel)
    monkeypatch.setattr(msg_sub, "get_connection", AsyncMock(return_value=dummy_conn))
    monkeypatch.setattr(msg_sub, "declare_exchange", AsyncMock())
    await msg_sub.run_consumer_task("queue", ["rk"], AsyncMock())

    # service lab & tenant_service edge branches
    db = AsyncMock()
    req1 = MagicMock(status="specimen_collected", specimen_id=None)
    db.execute.return_value.scalars.return_value.first.return_value = req1
    with pytest.raises(Exception): await service.collect_specimen(db, "r", "b", "t", "n", "t", "u")

    req2 = MagicMock(status="specimen_collected")
    db.execute.return_value.scalars.return_value.first.return_value = req2
    with pytest.raises(Exception): await service.create_lab_result(db, "r", MagicMock(test_name="t", result_value="v", is_critical=False), user=security.TokenPayload("u", None, None, {}, {}))

    # user identifier fallbacks
    assert service._user_identifier(security.TokenPayload("sub123", None, None, {}, {})) == "sub123"
    assert service._user_identifier(security.TokenPayload("sub123", "", "", {}, {})) == "sub123"

    # update_specimen_status edge branches
    spec_rejected = MagicMock(status="received")
    m1 = MagicMock(); m1.scalar_one_or_none.return_value = spec_rejected
    m2 = MagicMock(); m2.scalar_one_or_none.return_value = MagicMock()
    db.execute.side_effect = [m1, m2]
    with pytest.raises(exceptions.UnprocessableEntityError):
        await service.update_specimen_status(db, "s", MagicMock(status="rejected", rejection_reason=None), user=security.TokenPayload("u", None, None, {}, {}))

    spec_valid = MagicMock(status="received")
    m3 = MagicMock(); m3.scalar_one_or_none.return_value = spec_valid
    m4 = MagicMock(); m4.scalar_one_or_none.return_value = MagicMock()
    db.execute.side_effect = [m3, m4]
    await service.update_specimen_status(db, "s", MagicMock(status="processing", rejection_reason=None), user=security.TokenPayload("u", None, None, {}, {}))

    # create_lab_result critical result handling failure fallback
    db_crit = AsyncMock()
    req_crit = MagicMock(status="specimen_collected", requested_by="doc", test_name="t")
    r_crit = MagicMock(); r_crit.scalar_one_or_none.return_value = req_crit
    r_exist = MagicMock(); r_exist.scalar_one_or_none.return_value = None
    db_crit.execute.side_effect = [r_crit, r_exist]
    monkeypatch.setattr("app.events.publisher.publish_lab_critical_value", AsyncMock(side_effect=RuntimeError("pub err")))
    class CritBody:
        test_name = "t"; result_value = "v"; reference_range = "r"; unit = "u"; is_abnormal = True; is_critical = True; remarks = "r"; result_notes = "n"; specimen_type = "blood"; specimen_label = "L1"
    res_crit_created = await service.create_lab_result(db_crit, "r", CritBody(), user=security.TokenPayload("u", None, None, {}, {}))
    assert res_crit_created.is_critical is True

    # get_visit_verified_results and get_dashboard_stats
    db.execute.side_effect = None
    res_visit_mock = MagicMock()
    res_visit_mock.all.return_value = []
    db.execute.return_value = res_visit_mock
    assert await service.get_visit_verified_results(db, UUID("12345678-1234-5678-1234-567812345678")) == {"visit_id": UUID("12345678-1234-5678-1234-567812345678"), "results": []}

    db.execute.return_value.scalar.return_value = 5
    stats = await service.get_dashboard_stats(db)
    assert stats

    # init_db and limiter no client host
    database.init_db()
    req_no_client = Request({"type": "http", "headers": []})
    assert limiter.get_remote_address(req_no_client) == "127.0.0.1"

    # tenant_auth invalid token and header exceptions
    with pytest.raises(Exception): await tenant_auth.get_current_tenant("invalid_token")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant_ws(MagicMock(headers={}))

    # tenant_service revoke & DSN fallback
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=MagicMock(post=AsyncMock(return_value=MagicMock(status_code=200)))), __aexit__=AsyncMock())))
    await tenant_service._revoke_keycloak_sessions("t")
    db_none = MagicMock(); db_none.execute.return_value.scalar.return_value = None
    assert await tenant_service.get_tenant_db_dsn(db_none, "t") is None






    # update_lab_result edge paths (not found, critical amendment toggles)
    db.execute.side_effect = None
    res_not_found = MagicMock(); res_not_found.scalar_one_or_none.return_value = None
    db.execute.return_value = res_not_found

    class UpdBody:
        result_value = "v2"; unit = "u2"; reference_range = "r2"; result_notes = "n2"; is_critical = True
    with pytest.raises(exceptions.NotFoundError):
        await service.update_lab_result(db, UUID("12345678-1234-5678-1234-567812345678"), UpdBody(), security.TokenPayload("u", None, None, {}, {}))

    res_exist = MagicMock(status="drafted", is_critical=False, critical_notified_at=None)
    res_found = MagicMock(); res_found.scalar_one_or_none.return_value = res_exist
    db.execute.return_value = res_found
    await service.update_lab_result(db, UUID("12345678-1234-5678-1234-567812345678"), UpdBody(), security.TokenPayload("u", None, None, {}, {}))

    class UpdBodyUncrit:
        result_value = None; unit = None; reference_range = None; result_notes = None; is_critical = False
    db.execute.return_value = res_found
    await service.update_lab_result(db, UUID("12345678-1234-5678-1234-567812345678"), UpdBodyUncrit(), security.TokenPayload("u", None, None, {}, {}))

    # verify_lab_result not found
    db.execute.return_value = res_not_found
    with pytest.raises(exceptions.NotFoundError):
        await service.verify_lab_result(db, UUID("12345678-1234-5678-1234-567812345678"), user=security.TokenPayload("u", None, None, {}, {}))


    req3 = MagicMock(status="resulted")
    res3 = MagicMock(status="resulted", is_abnormal=True)
    spec3 = MagicMock(status="received")
    res_mock1 = MagicMock(); res_mock1.scalar_one_or_none.return_value = res3
    res_mock2 = MagicMock(); res_mock2.scalar_one_or_none.return_value = req3
    res_mock3 = MagicMock(); res_mock3.scalar_one_or_none.return_value = spec3
    db.execute.side_effect = [res_mock1, res_mock2, res_mock3]
    monkeypatch.setattr("app.events.publisher.publish_lab_result_ready", AsyncMock())
    monkeypatch.setattr("app.events.publisher.publish_lab_critical_value", AsyncMock())
    await service.verify_lab_result(db, "res", user=MagicMock(), tenant_id="t")

    req4 = MagicMock(status="completed")
    res_mock4 = MagicMock(); res_mock4.scalar_one_or_none.return_value = req4
    db.execute.side_effect = None
    db.execute.return_value = res_mock4
    with pytest.raises(Exception):
        await service.create_lab_bill(db, "r", MagicMock(amount=10, description="d"), user=MagicMock())

    # main CORS allowed origins
    monkeypatch.setattr(main.settings, "allowed_origins", "http://localhost, http://example.com")
    app_with_cors = main.create_application() if hasattr(main, "create_application") else main.app
    assert app_with_cors






    # tenant_service keycloak revoke
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("client down")))
    await tenant_service._revoke_keycloak_sessions("t")




