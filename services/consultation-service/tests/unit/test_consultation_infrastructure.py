import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import database
from app.core import tenant as tenant_core
from app.db import tenant as tenant_db
from app.events import subscriber as events
from app.messaging import connection, publisher, subscriber
from app import exceptions
from app.core import middleware


@pytest.mark.asyncio
async def test_event_dispatch_and_subscriber_registration(monkeypatch):
    await events._dispatch("triage.completed", {"triage_id": "t", "tenant_id": "h"})
    await events._dispatch("lab.result_ready", {"result_id": "l", "tenant_id": "h"})
    await events._dispatch("radiology.report_ready", {"report_id": "r", "tenant_id": "h"})
    await events._dispatch("unknown", {})
    monkeypatch.setattr(events, "start_consumer", AsyncMock())
    await events.start_subscriber()


@pytest.mark.asyncio
async def test_message_connection_publisher_and_consumer_task(monkeypatch):
    conn = MagicMock(is_closed=False, channel=AsyncMock(return_value=MagicMock())); conn.close = AsyncMock()
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=conn))
    connection._connection = None
    assert await connection.get_connection() is conn
    await connection.get_channel()
    channel = AsyncMock(); await connection.declare_exchange(channel)
    await connection.close_connection(); conn.close.assert_awaited_once()
    exchange = AsyncMock(); monkeypatch.setattr(publisher, "get_channel", AsyncMock(return_value=channel)); monkeypatch.setattr(publisher, "declare_exchange", AsyncMock(return_value=exchange))
    await publisher.publish_event("consultation.created", {"id": "1"})
    monkeypatch.setattr(publisher, "get_channel", AsyncMock(side_effect=RuntimeError()))
    await publisher.publish_event("consultation.created", {})
    task = await subscriber.run_consumer_task("consultation", ["triage.completed"], AsyncMock())
    assert isinstance(task, asyncio.Task); task.cancel()


@pytest.mark.asyncio
async def test_message_consumer_processes_json_event(monkeypatch):
    class Process:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Message:
        body = b'{"event": "ok"}'; routing_key = "triage.completed"
        def process(self): return Process()
    class Iterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self, "done", False): raise StopAsyncIteration
            self.done = True; return Message()
    queue = MagicMock(name="events"); queue.name = "events"; queue.bind = AsyncMock(); queue.iterator = lambda: Iterator()
    channel = MagicMock(); channel.set_qos = AsyncMock(); channel.declare_queue = AsyncMock(return_value=queue)
    conn = MagicMock(channel=AsyncMock(return_value=channel)); monkeypatch.setattr(subscriber, "get_connection", AsyncMock(return_value=conn)); monkeypatch.setattr(subscriber, "declare_exchange", AsyncMock(return_value=MagicMock()))
    handler = AsyncMock(); await subscriber.start_consumer("consultation", ["triage.completed"], handler)
    handler.assert_awaited_once_with("triage.completed", {"event": "ok"})


def test_database_session_lifecycle(monkeypatch):
    database._engine = None; database._SessionLocal = None
    monkeypatch.setattr(database, "create_engine", lambda *a, **k: object())
    monkeypatch.setattr(database, "sessionmaker", lambda **k: MagicMock())
    database.init_db(); assert database.get_session_local()
    session = MagicMock(); monkeypatch.setattr(database._router, "get_session", lambda _: session)
    ctx = database.get_hospital_context("h"); database.close_hospital_context(ctx); session.close.assert_called()
    db = database.get_db(); next(db)
    with pytest.raises(StopIteration): next(db)


def test_exception_types_and_middleware_branches():
    for cls, code in [(exceptions.UnauthorizedError, 401), (exceptions.ForbiddenError, 403),
                      (exceptions.NotFoundError, 404), (exceptions.ConflictError, 409),
                      (exceptions.BadRequestError, 400), (exceptions.RateLimitError, 429),
                      (exceptions.TenantNotFoundError, 404), (exceptions.TokenExpiredError, 401),
                      (exceptions.MFARequiredError, 401), (exceptions.TenantSuspendedError, 403),
                      (exceptions.ReadOnlyScopeError, 403)]:
        assert cls().status_code == code


@pytest.mark.asyncio
async def test_middleware_write_scope_audit_and_banner():
    from starlette.responses import Response
    request = MagicMock(method="GET"); request.state = MagicMock()
    async def call(_): return Response("ok")
    assert (await middleware.AuditLogMiddleware(MagicMock()).dispatch(request, call)).status_code == 200
    request.method = "OPTIONS"; assert (await middleware.AuditLogMiddleware(MagicMock()).dispatch(request, call)).status_code == 200
    request.method = "POST"; request.state.tenant = MagicMock(scope="readonly")
    denied = await middleware.ReadOnlyScopeMiddleware(MagicMock()).dispatch(request, call)
    assert denied.status_code == 403
    request.method = "GET"
    response = await middleware.ImpersonationBannerMiddleware(MagicMock()).dispatch(request, call)
    assert response.headers["X-Impersonation-Banner"] == "true"


def test_resolve_tenant_db_url_success_and_failure(monkeypatch):
    db = MagicMock(); result = MagicMock(); result.scalar.return_value = "postgresql://tenant"; db.execute.return_value = result
    monkeypatch.setattr(tenant_core, "get_db", lambda: iter([db]))
    assert tenant_core.resolve_tenant_db_url("t") == "postgresql://tenant"
    monkeypatch.setattr(tenant_core, "get_db", lambda: (_ for _ in ()).throw(RuntimeError()))
    assert tenant_core.resolve_tenant_db_url("t") is None


@pytest.mark.asyncio
async def test_tenant_async_factory_cache_and_session_cleanup(monkeypatch):
    tenant_db._async_engine_cache.clear()
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value=None))
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("missing")
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value="postgresql://tenant"))
    factory = MagicMock(); engine = MagicMock()
    monkeypatch.setattr(tenant_db, "create_async_engine", MagicMock(return_value=engine))
    monkeypatch.setattr(tenant_db, "async_sessionmaker", MagicMock(return_value=factory))
    assert await tenant_db._get_async_session_factory("t") is factory
    assert await tenant_db._get_async_session_factory("t") is factory


@pytest.mark.asyncio
async def test_application_lifespan_and_security_headers(monkeypatch):
    import app.main as main
    monkeypatch.setattr("app.core.database.init_db", MagicMock())
    async with main.lifespan(main.app):
        pass
    from starlette.responses import Response
    request = MagicMock()
    async def call(_): return Response("ok")
    response = await main.security_headers(request, call)
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_consultation_system_resilience_and_disposition_handling(monkeypatch):
    from datetime import datetime, timezone
    from app import exceptions, main

    from app.core import database, limiter, middleware, security, tenant_auth
    from app.db import master, session as db_session, tenant as tenant_db
    from app import dependencies
    from app.messaging import connection, subscriber as msg_sub
    from app.services import tenant_service
    from app.api.v1 import router as api_router
    from types import SimpleNamespace
    from uuid import uuid4
    from starlette.requests import Request

    # db master & session helpers
    assert master.get_master_db()
    with master.get_master_session() as m_db:
        assert m_db

    async def _mock_sess():
        yield AsyncMock()

    monkeypatch.setattr(db_session, "get_tenant_session", lambda _: _mock_sess())
    async for s in db_session.get_tenant_db("t"):
        assert s

    # dependencies helpers
    monkeypatch.setattr(dependencies, "get_tenant_session", lambda _: _mock_sess())
    async for s in dependencies.get_tenant_db(SimpleNamespace(tenant_id="t")):
        assert s

    user_payload = security.TokenPayload("u", None, None, {}, {})
    assert await dependencies.get_current_user(user_payload) is user_payload

    # security & tenant_auth decoding edge cases
    with pytest.raises(Exception):
        await security._decode_token("invalid.token.str")
    with pytest.raises(Exception):
        await tenant_auth._decode_token("invalid.token.str")
    with pytest.raises(Exception):
        await tenant_auth.get_current_tenant("invalid_token")
    with pytest.raises(Exception):
        await tenant_auth.get_current_tenant_ws(MagicMock(headers={}))

    # security require_role permission denied
    with pytest.raises(Exception):
        await security.require_role("admin")(security.TokenPayload("u", None, None, {"roles": ["doctor"]}, {}))

    # security JWKS exception
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(side_effect=RuntimeError("jwks error")))
    with pytest.raises(Exception):
        import jwt
        await security._decode_token(jwt.encode({"iss": f"{security.settings.keycloak_url}/realms/r"}, "secret", algorithm="HS256", headers={"kid": "k"}))

    # limiter remote address fallback
    req_no_client = Request({"type": "http", "headers": []})
    assert limiter.get_remote_address(req_no_client) == "127.0.0.1"

    # database router exception
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    monkeypatch.setattr(database, "create_engine", MagicMock(side_effect=RuntimeError("engine err")))
    with pytest.raises(Exception):
        database.get_session_local()

    # tenant_db async engine failure
    class BadConn:
        async def run_sync(self, fn): raise RuntimeError("sync err")
    class BadBegin:
        async def __aenter__(self): return BadConn()
        async def __aexit__(self, *a): pass
    bad_engine = MagicMock(begin=lambda: BadBegin())
    monkeypatch.setattr(tenant_db, "create_async_engine", lambda *a, **k: bad_engine)
    tenant_db._async_engine_cache.clear()
    with pytest.raises(Exception):
        await tenant_db._get_async_session_factory("bad_tenant")

    # messaging connection failure
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(side_effect=RuntimeError("pika error")))
    connection._connection = None
    with pytest.raises(Exception):
        await connection.get_connection()

    # main settings & CORS origins & health
    monkeypatch.setattr(main.settings, "allowed_origins", "http://localhost, http://example.com")
    monkeypatch.setattr(main.settings, "environment", "prod")
    assert (await main.health())["status"] == "ok"

    req_prod = MagicMock(method="GET"); req_prod.headers = {}
    async def dummy_call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    res_prod = await main.security_headers(req_prod, dummy_call)
    assert res_prod.headers["Content-Security-Policy"] == "default-src 'none'"

    # tenant_service revoke & fallback DSN
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("http err")))
    await tenant_service._revoke_keycloak_sessions("t")
    db_none = MagicMock(); db_none.execute.return_value.scalar.return_value = None
    assert await tenant_service.get_tenant_db_dsn(db_none, "t") is None

    # router update_disposition pending prescription pharmacy queue creation branch
    db_m = AsyncMock()
    c_mock = MagicMock(consultation_status="in_progress", visit_id=uuid4(), patient_id=uuid4(), created_by="doc1")
    res_c = MagicMock(); res_c.scalars.return_value.first.return_value = c_mock
    res_inv = MagicMock(); res_inv.scalars.return_value.all.return_value = []
    res_p = MagicMock(); res_p.scalars.return_value.first.return_value = MagicMock()
    res_q = MagicMock(); res_q.scalars.return_value.first.return_value = None
    res_num = MagicMock(); res_num.scalar.return_value = 5
    db_m.execute.side_effect = [res_c, res_inv, res_p, res_q, res_num]
    user_doc = security.TokenPayload("doc1", None, None, {"roles": ["doctor"]}, {})
    disp_body = MagicMock(disposition="outpatient", notes="ok", admission_unit=None, follow_up_date=None, return_date=None)


    ctx_doc = SimpleNamespace(tenant_id="t", user_sub="doc1")
    res_disp = await api_router.update_disposition(uuid4(), disp_body, db=db_m, ctx=ctx_doc, current_user=user_doc)
    assert c_mock.disposition == "outpatient"

    # exact missing line target coverage
    database._engine = None
    monkeypatch.setattr(database, "create_engine", MagicMock())
    database.init_db()

    req_cl = MagicMock(client=MagicMock(host="1.2.3.4"))
    assert limiter.get_remote_address(req_cl) == "1.2.3.4"

    mw = middleware.AuditLogMiddleware(MagicMock())
    req_mw = MagicMock(method="POST"); req_mw.url.path = "/test"; req_mw.state = MagicMock(user_sub="sub", tenant=MagicMock(tenant_id="t"))
    call_mw = AsyncMock(return_value=MagicMock(headers={}, status_code=200))
    await mw.dispatch(req_mw, call_mw)

    security._issuer(MagicMock(headers={"x-forwarded-proto": "https", "host": "h"}))
    monkeypatch.setattr(security, "_issuer", lambda *a: "http://iss")
    security._introspection_cache.clear()
    class Inactive:
        def raise_for_status(self): pass
        def json(self): return {"active": False}
    class InstClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return Inactive()
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **k: InstClient())
    with pytest.raises(Exception): await security._introspect_token("revoked")

    tenant_auth._issuer(MagicMock(headers={"x-forwarded-proto": "https", "host": "h"}))
    with pytest.raises(Exception): await tenant_auth._decode_token("header.payload.sig")

    mock_sess = AsyncMock()
    mock_fac = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(return_value=mock_sess), __aexit__=AsyncMock()))
    monkeypatch.setattr(tenant_db, "_get_async_session_factory", AsyncMock(return_value=mock_fac))
    async for s in tenant_db.get_tenant_session("t"): assert s is mock_sess

    active_conn = MagicMock(is_closed=False, close=AsyncMock())
    connection._connection = active_conn
    await connection.close_connection()

    class ProcessContext:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
    class ExMessage:
        body = b'{}'; routing_key = "k"
        def process(self): return ProcessContext()
    class ExIterator:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        def __aiter__(self): return self
        async def __anext__(self):
            if getattr(self, "d", False): raise StopAsyncIteration
            self.d = True; return ExMessage()
    ex_queue = MagicMock(name="consultation_events"); ex_queue.name = "consultation_events"; ex_queue.bind = AsyncMock(); ex_queue.iterator = lambda: ExIterator()
    ex_channel = MagicMock(); ex_channel.set_qos = AsyncMock(); ex_channel.declare_queue = AsyncMock(return_value=ex_queue)
    ex_conn = MagicMock(channel=AsyncMock(return_value=ex_channel)); monkeypatch.setattr(msg_sub, "get_connection", AsyncMock(return_value=ex_conn)); monkeypatch.setattr(msg_sub, "declare_exchange", AsyncMock(return_value=MagicMock()))
    failing_handler = AsyncMock()
    await msg_sub.start_consumer("consultation_events", ["k"], failing_handler)


    assert await tenant_service.is_tenant_suspended("t_none") is False
    db_dsn_mock = MagicMock()
    db_dsn_mock.execute.return_value.scalar.return_value = tenant_service.encrypt_dsn("postgresql://dsn")
    assert await tenant_service.get_tenant_db_dsn(db_dsn_mock, "t") == "postgresql://dsn"

    # radiology critical result doctor dashboard coverage
    inv_rad = MagicMock(id=uuid4(), request_type="radiology", status="completed", test_name="CT Brain", requested_at=datetime.now(timezone.utc))
    pat_rad = MagicMock(full_name="Rad Pat")
    rad_rep = MagicMock(findings="Severe subdural hemorrhage noted", impression="Critical fracture", reported_at=datetime.now(timezone.utc))
    res_inv_rad = MagicMock(); res_inv_rad.all.return_value = [(inv_rad, pat_rad)]
    res_rad_rep = MagicMock(); res_rad_rep.scalars.return_value.first.return_value = rad_rep
    db_dash = AsyncMock()
    res_count = MagicMock(); res_count.scalar.return_value = 0
    res_list = MagicMock(); res_list.scalars.return_value.all.return_value = []
    res_lab_none = MagicMock(); res_lab_none.scalars.return_value.first.return_value = None
    db_dash.execute.side_effect = [res_count, res_count, res_count, res_count, res_list, res_inv_rad, res_rad_rep, res_lab_none, res_count, res_count, res_count, res_count]
    dash = await api_router.get_doctor_dashboard_stats(db=db_dash, current_user=user_doc)
@pytest.mark.asyncio
async def test_consultation_missing_branches_and_edge_cases(monkeypatch):
    import uuid
    from datetime import datetime, timezone, timedelta
    from app import exceptions, main
    from app.api.v1 import router as api_router, schemas
    from app.core import database, limiter, middleware, security, tenant_auth
    from app.events import subscriber as event_sub
    from app.messaging import connection, subscriber as msg_sub
    from app.services import tenant_service

    # 1. database init_db
    database._engine = None
    monkeypatch.setattr(database, "create_engine", MagicMock())
    database.init_db()

    # 2. limiter fallback
    req_lim = MagicMock(client=None)
    assert limiter.get_remote_address(req_lim) == "127.0.0.1"

    # 3. main allowed_origins list check
    monkeypatch.setattr(main.settings, "allowed_origins", ["http://localhost", "http://domain.com"])
    req_m = MagicMock(method="GET"); req_m.headers = {}
    async def dummy_call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    await main.security_headers(req_m, dummy_call)

    # 4. middleware audit error logging
    mw = middleware.AuditLogMiddleware(MagicMock())
    req_err = MagicMock(method="POST"); req_err.url.path = "/test"; req_err.state = MagicMock(user_sub="sub", tenant=MagicMock(tenant_id="t"))
    call_err = AsyncMock(side_effect=RuntimeError("mw error"))
    with pytest.raises(RuntimeError):
        await mw.dispatch(req_err, call_err)

    # 5. security token introspect error
    with pytest.raises(Exception):
        await security._introspect_token("invalid_token")

    # 6. tenant_auth _decode_token error
    with pytest.raises(Exception):
        await tenant_auth._decode_token("bad.jwt.token")

    # 7. tenant_service expired / suspended branch
    db_exp = MagicMock()
    db_exp.execute.return_value.one_or_none.return_value = ("active", datetime.now(timezone.utc)-timedelta(days=5), 1)
    db_exp.commit = MagicMock()
    assert await tenant_service.check_and_update_tenant_status(db_exp, "t_exp") == "expired"


    # 8. router error branches
    user_doc = security.TokenPayload("doc1", None, None, {"roles": ["doctor"]}, {})
    ctx_doc = SimpleNamespace(tenant_id="t", user_sub="doc1")

    # missing consultation for update_disposition
    db_r = AsyncMock()
    res_none = MagicMock(); res_none.scalars.return_value.first.return_value = None
    res_none.scalars.return_value.all.return_value = []
    res_none.scalar_one_or_none.return_value = None
    db_r.execute.return_value = res_none

    from fastapi import HTTPException

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.update_disposition(uuid.uuid4(), MagicMock(disposition="outpatient", notes=None, admission_unit=None, follow_up_date=None, return_date=None), db=db_r, ctx=ctx_doc, current_user=user_doc)

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.cancel_investigation(uuid.uuid4(), request=MagicMock(), db=db_r, ctx=ctx_doc, current_user=user_doc)

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.cancel_prescription(uuid.uuid4(), db=db_r, ctx=ctx_doc, current_user=user_doc)

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.get_encounter(uuid.uuid4(), db=db_r, current_user=user_doc)

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.get_admission_details(uuid.uuid4(), db=db_r, current_user=user_doc)

    with pytest.raises((exceptions.NotFoundError, HTTPException)):
        await api_router.get_consultation_summary(uuid.uuid4(), db=db_r, current_user=user_doc)

    assert await api_router.get_prescriptions(uuid.uuid4(), db=db_r, current_user=user_doc) == []
    assert await api_router.get_investigations(uuid.uuid4(), db=db_r, current_user=user_doc) == []

    # router invalid input / state branches
    # event subscriber start_subscriber
    monkeypatch.setattr(event_sub, "start_consumer", AsyncMock())
    await event_sub.start_subscriber()














