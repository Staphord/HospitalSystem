import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import billing, tenant_service
from app.core import security, tenant_auth, middleware, database
from app import exceptions
from app.messaging import connection, publisher as msg_publisher, subscriber as msg_subscriber
from app.events import subscriber as event_subscriber
from app.api.v1 import router as billing_router
from app.api.v1.schemas import BillItemCreate, BillAdjustmentIn, PaymentIn
from app.models.billing import _utcnow


@pytest.mark.asyncio
async def test_ward_charge_creates_bill_and_uses_configured_default(monkeypatch):
    db = AsyncMock(); db.add = MagicMock()
    bill_result = MagicMock(); bill_result.scalars.return_value.first.return_value = None
    existing = MagicMock(); existing.scalars.return_value.first.return_value = None
    db.execute.side_effect = [bill_result, existing]
    monkeypatch.setattr(billing.settings, "default_ward_day_rate", 42.5)
    monkeypatch.setattr(billing, "_ward_day_unit_price", AsyncMock(return_value=Decimal("42.5")))
    result = await billing.apply_ward_charge_on_discharge(db, admission_id=str(uuid4()), patient_id=str(uuid4()), tenant_id="t", length_of_stay_days=2, visit_id=str(uuid4()))
    assert result.line_total == Decimal("85.00")
    db.flush.assert_awaited_once(); db.commit.assert_awaited_once(); db.refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_drug_charge_missing_visit_or_visit_record():
    db = AsyncMock()
    row = MagicMock(); row.fetchone.return_value = (None, 2, "Drug", "D", 5, None)
    db.execute.return_value = row
    assert await billing.apply_drug_charge(db, dispensing_id=str(uuid4()), tenant_id="t") is None
    row2 = MagicMock(); row2.fetchone.return_value = ("visit", 2, None, None, None, "Prescription")
    visit_missing = MagicMock(); visit_missing.fetchone.return_value = None
    db.execute.side_effect = [row2, visit_missing]
    assert await billing.apply_drug_charge(db, dispensing_id=str(uuid4()), tenant_id="t") is None


@pytest.mark.asyncio
async def test_drug_charge_creates_bill_and_is_idempotent():
    db = AsyncMock(); db.add = MagicMock()
    dispensing = MagicMock(); dispensing.fetchone.return_value = ("visit", 3, None, None, None, "Prescription")
    visit = MagicMock(); visit.fetchone.return_value = ("patient",)
    bill_missing = MagicMock(); bill_missing.scalars.return_value.first.return_value = None
    existing = MagicMock(); existing.scalars.return_value.first.return_value = None
    db.execute.side_effect = [dispensing, visit, bill_missing, existing]
    item = await billing.apply_drug_charge(db, dispensing_id=str(uuid4()), tenant_id="t")
    assert item.line_total == Decimal("30.00")
    db.commit.assert_awaited_once()


def _db_row(*values):
    db = MagicMock(); result = MagicMock(); result.one_or_none.return_value = values; result.scalar.return_value = values[0] if values else None; db.execute.return_value = result; return db


@pytest.mark.asyncio
async def test_tenant_service_crypto_cache_and_statuses(monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(tenant_service.settings, "tenant_db_encryption_key", key)
    encrypted = tenant_service.encrypt_dsn("postgresql://tenant")
    assert tenant_service.decrypt_dsn(encrypted) == "postgresql://tenant"
    redis = AsyncMock(); monkeypatch.setattr(tenant_service, "_redis", redis)
    redis.get.return_value = "1"
    assert await tenant_service.is_tenant_suspended("t") is True
    await tenant_service.cache_tenant_suspension("t", ttl=10)
    await tenant_service.remove_tenant_suspension_cache("t")
    redis.get.return_value = None
    assert await tenant_service.is_tenant_suspended("t") is False


@pytest.mark.asyncio
async def test_tenant_service_subscription_transitions(monkeypatch):
    db = _db_row("suspended", None)
    assert await tenant_service.check_tenant_subscription(db, "t") == "suspended"
    db = _db_row("active", datetime.now(timezone.utc) - timedelta(days=1))
    assert await tenant_service.check_tenant_subscription(db, "t") == "expired"
    db = _db_row("active", datetime.now(timezone.utc) + timedelta(days=1))
    assert await tenant_service.check_tenant_subscription(db, "t") == "active"
    db = _db_row()
    assert await tenant_service.check_tenant_subscription(db, "t") == "not_found"


@pytest.mark.asyncio
async def test_tenant_service_update_and_dsn(monkeypatch):
    monkeypatch.setattr(tenant_service, "cache_tenant_suspension", AsyncMock())
    monkeypatch.setattr(tenant_service, "_revoke_keycloak_sessions", AsyncMock())
    db = _db_row("suspended", None, 1)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"
    db = _db_row("active", datetime.now(timezone.utc) - timedelta(days=40), 2)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"
    db = _db_row("active", datetime.now(timezone.utc) - timedelta(days=2), 2)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "expired"
    db = _db_row("active", datetime.now(timezone.utc) + timedelta(days=2), 2)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "active"
    db = MagicMock(); db.execute.return_value.scalar.return_value = None
    assert await tenant_service.get_tenant_db_dsn(db, "t") is None


def test_exceptions_have_expected_defaults():
    for cls, code in [(exceptions.UnauthorizedError, 401), (exceptions.ForbiddenError, 403),
                      (exceptions.NotFoundError, 404), (exceptions.ConflictError, 409),
                      (exceptions.BadRequestError, 400), (exceptions.RateLimitError, 429),
                      (exceptions.TenantNotFoundError, 404), (exceptions.TokenExpiredError, 401),
                      (exceptions.MFARequiredError, 401), (exceptions.TenantSuspendedError, 403),
                      (exceptions.ReadOnlyScopeError, 403)]:
        assert cls().status_code == code


def test_billing_model_timestamp():
    assert _utcnow().tzinfo is not None


@pytest.mark.asyncio
async def test_security_token_and_roles(monkeypatch):
    from jose import jwt
    token = jwt.encode({"sub": "u", "type": "superadmin", "role": "super_admin"}, security.settings.secret_key, algorithm="HS256")
    req = MagicMock(); req.state = MagicMock()
    user = await security.get_current_active_user(req, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert user.sub == "u" and security._extract_roles(user) == ["super_admin"]
    assert security._extract_roles(security.TokenPayload("u", None, None, {"roles": ["cashier"]}, {"realm_access": {"roles": ["cashier"]}})) == ["cashier"]
    with pytest.raises(Exception):
        await security.get_current_active_user(req, None)
    with pytest.raises(Exception):
        await security.require_role("admin")(security.TokenPayload("u", None, None, {}, {"realm_access": {"roles": []}}))


@pytest.mark.asyncio
async def test_security_decode_and_introspection_paths(monkeypatch):
    from jose import jwt
    with pytest.raises(Exception):
        await security._decode_token("bad")
    expired = jwt.encode({"sub": "u", "exp": 1}, security.settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception):
        await security._decode_token(expired)
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": []}))
    rsa = jwt.encode({"sub": "u", "iss": f"{security.settings.keycloak_url}/realms/x"}, "secret", algorithm="HS256", headers={"kid": "k"})
    with pytest.raises(Exception):
        await security._decode_token(rsa)
    security._introspection_cache.clear()
    security._introspection_cache["inactive"] = False
    with pytest.raises(Exception): await security._introspect_token("inactive")
    security._introspection_cache["cached"] = True
    await security._introspect_token("cached")


@pytest.mark.asyncio
async def test_tenant_auth_paths(monkeypatch):
    from jose import jwt
    req = MagicMock(); req.state = MagicMock()
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, None)
    token = jwt.encode({"type": "superadmin", "super_admin_id": "sa", "username": "root"}, tenant_auth.settings.secret_key, algorithm="HS256")
    ctx = await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert ctx.is_super_admin and ctx.tenant_id is None
    token = jwt.encode({"sub": "u", "tenant_id": "t", "scope": "readonly", "realm_access": {"roles": []}}, tenant_auth.settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=False))
    ctx = await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert ctx.scope == "readonly"
    token = jwt.encode({"sub": "u", "realm_access": {"roles": []}}, tenant_auth.settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    token = jwt.encode({"sub": "u", "tenant_id": "t"}, tenant_auth.settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))


@pytest.mark.asyncio
async def test_messaging_connection_publisher_and_subscriber(monkeypatch):
    fake = MagicMock(is_closed=False); fake.channel = AsyncMock(return_value=MagicMock()); fake.close = AsyncMock()
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(return_value=fake))
    connection._connection = None
    assert await connection.get_connection() is fake
    await connection.get_channel()
    channel = AsyncMock(); await connection.declare_exchange(channel); channel.declare_exchange.assert_awaited_once()
    await connection.close_connection(); fake.close.assert_awaited_once()
    monkeypatch.setattr(msg_publisher, "get_channel", AsyncMock(return_value=channel))
    exchange = AsyncMock(); monkeypatch.setattr(msg_publisher, "declare_exchange", AsyncMock(return_value=exchange))
    await msg_publisher.publish_event("x", {"a": 1}); exchange.publish.assert_awaited_once()
    monkeypatch.setattr(msg_publisher, "get_channel", AsyncMock(side_effect=RuntimeError()))
    await msg_publisher.publish_event("x", {})
    task = await msg_subscriber.run_consumer_task("x", ["a"], AsyncMock()); assert isinstance(task, asyncio.Task); task.cancel()


@pytest.mark.asyncio
async def test_event_dispatch_and_handlers(monkeypatch):
    async def sessions(_):
        yield AsyncMock()
    monkeypatch.setattr(event_subscriber, "get_tenant_session", sessions)
    monkeypatch.setattr(event_subscriber, "apply_drug_charge", AsyncMock())
    monkeypatch.setattr(event_subscriber, "apply_ward_charge_on_discharge", AsyncMock())
    await event_subscriber.handle_patient_admitted("a", "t")
    await event_subscriber.handle_drug_dispensed("d", "t")
    await event_subscriber.handle_patient_discharged({"admission_id": "a", "tenant_id": "t"})
    await event_subscriber._dispatch("patient.admitted", {"admission_id": "a", "tenant_id": "t"})
    await event_subscriber._dispatch("unknown", {})
    monkeypatch.setattr(event_subscriber, "start_consumer", AsyncMock())
    await event_subscriber.start_subscriber()


def _bill(status="open"):
    now = datetime.now(timezone.utc)
    return SimpleNamespace(bill_id=uuid4(), visit_id=uuid4(), admission_id=None,
                     patient_id=uuid4(), status=status, total_amount=Decimal("100"),
                     paid_amount=Decimal("0"), discount_amount=Decimal("0"), currency="TZS",
                     created_at=now, updated_at=now, patient_name=None, patient_number=None)


@pytest.mark.asyncio
async def test_billing_router_list_get_and_errors():
    bill = _bill(); item = MagicMock(item_id=uuid4(), bill_item_id=uuid4(), bill_id=bill.bill_id,
        item_code="X", item_type="service", description="x", quantity=1, unit_price=10,
        line_total=10, total_price=10, created_at=datetime.now(timezone.utc))
    db = AsyncMock()
    r1 = MagicMock(); r1.scalars.return_value.all.return_value = [bill]
    r2 = MagicMock(); r2.scalars.return_value.all.return_value = [item]
    pat = MagicMock(); pat.fetchone.return_value = ("Jane", "P1")
    db.execute.side_effect = [r1, r2, pat]
    out = await billing_router.list_pending_bills(db)
    assert len(out) == 1 and out[0].patient_name == "Jane"
    db.execute.side_effect = [r1, r2, pat]
    assert len(await billing_router.list_all_bills(db)) == 1
    one = MagicMock(); one.scalars.return_value.first.return_value = bill
    db.execute.side_effect = [one, r2, pat]
    assert (await billing_router.get_bill(bill.bill_id, db)).items
    missing = MagicMock(); missing.scalars.return_value.first.return_value = None
    with pytest.raises(Exception): await billing_router.get_bill(uuid4(), AsyncMock(execute=AsyncMock(return_value=missing)))


@pytest.mark.asyncio
async def test_billing_router_mutations_and_payment(monkeypatch):
    bill = _bill(); db = AsyncMock(); db.add = MagicMock(); found = MagicMock(); found.scalars.return_value.first.return_value = bill
    items = MagicMock(); items.scalars.return_value.all.return_value = []
    db.execute.side_effect = [found, items]
    out = await billing_router.add_bill_item(bill.bill_id, BillItemCreate(description="fee", unit_price=Decimal("25")), db)
    assert out.total_amount == Decimal("125")
    db.execute.side_effect = [found, items]
    out = await billing_router.adjust_bill(bill.bill_id, BillAdjustmentIn(discount_amount=Decimal("125"), reason="waiver"), db)
    assert out.status == "paid"
    paid = _bill("paid"); found_paid = MagicMock(); found_paid.scalars.return_value.first.return_value = paid
    with pytest.raises(Exception): await billing_router.add_bill_item(paid.bill_id, BillItemCreate(description="x", unit_price=1), AsyncMock(execute=AsyncMock(return_value=found_paid)))
    with pytest.raises(Exception): await billing_router.adjust_bill(paid.bill_id, BillAdjustmentIn(discount_amount=1, reason="x"), AsyncMock(execute=AsyncMock(return_value=found_paid)))
    monkeypatch.setattr(billing_router, "publish_payment_received", AsyncMock())
    monkeypatch.setattr(billing_router, "Payment", lambda **kw: SimpleNamespace(**kw, created_at=datetime.now(timezone.utc)))
    bill = _bill(); found.scalars.return_value.first.return_value = bill
    db.execute.side_effect = [found]
    ctx = MagicMock(tenant_id="t", user_sub="u")
    payment = await billing_router.record_payment(bill.bill_id, PaymentIn(amount=Decimal("40"), payment_method="", notes="n"), db, ctx)
    assert payment.status == "partial"
    bill2 = _bill(); bill2.total_amount = Decimal("30"); found.scalars.return_value.first.return_value = bill2
    db.execute.side_effect = [found]
    payment = await billing_router.record_payment(bill2.bill_id, PaymentIn(amount=Decimal("40")), db, ctx)
    assert payment.status == "paid"
    with pytest.raises(Exception): await billing_router.record_payment(paid.bill_id, PaymentIn(amount=1), AsyncMock(execute=AsyncMock(return_value=found_paid)), ctx)


@pytest.mark.asyncio
async def test_router_error_and_patient_enrichment_branches():
    bill = _bill(); missing = MagicMock(); missing.scalars.return_value.first.return_value = None
    for fn, args in [(billing_router.add_bill_item, (bill.bill_id, BillItemCreate(description="x", unit_price=1))),
                     (billing_router.adjust_bill, (bill.bill_id, BillAdjustmentIn(discount_amount=1, reason="x"))),
                     (billing_router.record_payment, (bill.bill_id, PaymentIn(amount=1)))]:
        db = AsyncMock(execute=AsyncMock(return_value=missing))
        with pytest.raises(Exception):
            await fn(*args, db, MagicMock(tenant_id="t", user_sub="u")) if fn is billing_router.record_payment else await fn(*args, db)
    db = AsyncMock(); result = MagicMock(); result.scalars.return_value.all.side_effect = RuntimeError(); db.execute.return_value = result
    assert await billing_router.list_pending_bills(db) == []
    db = AsyncMock(); bad = MagicMock(); bad.fetchone.side_effect = RuntimeError(); db.execute.side_effect = [MagicMock(scalars=MagicMock(return_value=MagicMock().all)), bad]
    await billing_router._enrich_patient_info(db, bill, MagicMock())


def test_database_and_master_helpers(monkeypatch):
    database._engine = None; database._SessionLocal = None
    monkeypatch.setattr(database, "create_engine", lambda *a, **k: object())
    monkeypatch.setattr(database, "sessionmaker", lambda **k: MagicMock())
    assert database.get_session_local(); database.init_db()
    session = MagicMock(); monkeypatch.setattr(database._router, "get_session", lambda h: session)
    assert database.get_hospital_context("h").db is session
    database.close_hospital_context(database.HospitalContext("h", session)); session.close.assert_called()
    gen = database.get_db(); next(gen); 
    with pytest.raises(StopIteration): next(gen)


@pytest.mark.asyncio
async def test_tenant_db_factory_and_dependency(monkeypatch):
    from app.db import tenant as tenant_db
    tenant_db._async_engine_cache.clear()
    monkeypatch.setattr(tenant_db, "get_tenant_db_dsn", AsyncMock(return_value=None))
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("missing")
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
    assert await tenant_db._get_async_session_factory("t") is factory


@pytest.mark.asyncio
async def test_middleware_paths():
    from starlette.requests import Request
    from starlette.responses import Response
    req = MagicMock(method="GET"); req.state = MagicMock()
    async def call(_): return Response("ok")
    audit = middleware.AuditLogMiddleware(MagicMock())
    assert (await audit.dispatch(req, call)).status_code == 200
    req.method = "OPTIONS"; assert (await audit.dispatch(req, call)).status_code == 200
    ro = SimpleNamespace(scope="readonly"); req.method="POST"; req.state.tenant=ro
    assert (await middleware.ReadOnlyScopeMiddleware(MagicMock()).dispatch(req, call)).status_code == 403
    req.method="GET"; response = await middleware.ImpersonationBannerMiddleware(MagicMock()).dispatch(req, call); assert response.status_code == 200


@pytest.mark.asyncio
async def test_tenant_service_redis_failures_and_keycloak_revoke(monkeypatch):
    bad = AsyncMock(); bad.get.side_effect=RuntimeError(); bad.set.side_effect=RuntimeError(); bad.delete.side_effect=RuntimeError()
    monkeypatch.setattr(tenant_service, "_redis", bad)
    assert await tenant_service.is_tenant_suspended("x") is False
    await tenant_service.cache_tenant_suspension("x"); await tenant_service.remove_tenant_suspension_cache("x")
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"access_token": "a"} 
        is_success = True
    client = MagicMock(); client.__aenter__=AsyncMock(return_value=client); client.__aexit__=AsyncMock(); client.post=AsyncMock(return_value=Resp()); client.get=AsyncMock(return_value=Resp()); client.put=AsyncMock()
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", lambda **kw: client)
    await tenant_service._revoke_keycloak_sessions("t")


@pytest.mark.asyncio
async def test_security_and_tenant_rsa_and_role_branches(monkeypatch):
    from jose import jwt
    monkeypatch.setattr(security.jwt, "get_unverified_header", lambda t: {"kid": "k"})
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k"}]}))
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: {"sub": "u"})
    assert (await security._decode_token("x"))["sub"] == "u"
    monkeypatch.setattr(security.jwt, "decode", MagicMock(side_effect=jwt.ExpiredSignatureError()))
    with pytest.raises(Exception): await security._decode_token("x")
    monkeypatch.setattr(security.jwt, "decode", MagicMock(side_effect=ValueError()))
    with pytest.raises(Exception): await security._decode_token("x")
    assert security._extract_realm_from_iss("bad") is None
    with pytest.raises(Exception): security._build_rsa_key({}, "x")
    user = security.TokenPayload("u", None, None, {}, {"type": "superadmin"})
    assert await security.require_role("superadmin")(user) is user
    db = MagicMock(); db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(hospital_id="h")
    assert await security.get_current_hospital_id(user, db) == "h"
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    with pytest.raises(Exception): await security.get_current_hospital_id(security.TokenPayload("u",None,None,{},{}), db)

    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_header", lambda t: {"kid": "k"})
    monkeypatch.setattr(tenant_auth, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k"}]}))
    monkeypatch.setattr(tenant_auth.jwt, "decode", lambda *a, **k: {"sub": "u", "tenant_id": "t"})
    assert (await tenant_auth._decode_token("x"))["tenant_id"] == "t"
    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=tenant_auth.jwt.ExpiredSignatureError()))
    with pytest.raises(Exception): await tenant_auth._decode_token("x")
    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=ValueError()))
    with pytest.raises(Exception): await tenant_auth._decode_token("x")
    with pytest.raises(Exception): tenant_auth._build_rsa_key({}, "x")
    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"keys": []}
    client = MagicMock(); client.__aenter__=AsyncMock(return_value=client); client.__aexit__=AsyncMock(); client.get=AsyncMock(return_value=Resp()); client.post=AsyncMock(return_value=type("R", (), {"raise_for_status":lambda s:None, "json":lambda s:{"active":True}})())
    importlib.reload(security)
    security._jwks_cache.clear(); monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kw: client)
    assert await security._fetch_jwks("realm") == {"keys": []}; assert await security._fetch_jwks("realm") == {"keys": []}
    security._introspection_cache.clear(); await security._introspect_token("new-token")
    importlib.reload(tenant_auth)
    tenant_auth._jwks_cache.clear(); monkeypatch.setattr(tenant_auth.httpx, "AsyncClient", lambda **kw: client)
    assert await tenant_auth._fetch_jwks("realm") == {"keys": []}; assert await tenant_auth._fetch_jwks("realm") == {"keys": []}
    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_claims", lambda t: {"iss": f"{tenant_auth.settings.keycloak_url}/realms/r"})
    assert tenant_auth._extract_realm_from_iss("x") == "r"


@pytest.mark.asyncio
async def test_event_full_paths_and_messaging_loop(monkeypatch):
    session = AsyncMock(); result = MagicMock(); result.scalars.return_value.first.return_value = None; session.execute = AsyncMock(return_value=result)
    async def sessions(_): yield session
    monkeypatch.setattr(event_subscriber, "get_tenant_session", sessions)
    await event_subscriber.handle_visit_created(str(uuid4()), "t", str(uuid4()))
    monkeypatch.setattr(event_subscriber, "apply_drug_charge", AsyncMock(side_effect=RuntimeError()))
    await event_subscriber.handle_drug_dispensed("d", "t")
    await event_subscriber.handle_patient_discharged({"admission_id":"a", "tenant_id":"t", "patient_id":"p", "length_of_stay_days": 2, "visit_id":"v"})
    await event_subscriber._dispatch("visit.created", {"visit_id":str(uuid4()), "tenant_id":"t"})
    await event_subscriber._dispatch("drug.dispensed", {"dispensing_id":"d", "tenant_id":"t"})
    await event_subscriber._dispatch("patient.discharged", {"admission_id":"a", "tenant_id":"t"})
    monkeypatch.setattr(msg_subscriber, "get_connection", AsyncMock(return_value=MagicMock(channel=AsyncMock())))
    monkeypatch.setattr(msg_subscriber, "declare_exchange", AsyncMock(return_value=MagicMock()))
    await msg_subscriber.run_consumer_task("s", [], AsyncMock())


@pytest.mark.asyncio
async def test_main_lifespan_security_and_dependencies(monkeypatch):
    import app.main as main
    monkeypatch.setattr("app.core.database.init_db", MagicMock())
    monkeypatch.setattr(main._sub, "start_subscriber", AsyncMock()) if hasattr(main, "_sub") else None
    async with main.lifespan(main.app): pass
    req = MagicMock(); req.state = MagicMock(); req.method="GET"
    async def call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    response = await main.security_headers(req, call); assert response.headers["X-Frame-Options"] == "DENY"
    from app import dependencies
    ctx = SimpleNamespace(tenant_id="t")
    monkeypatch.setattr(dependencies, "get_tenant_session", lambda _: _one_session())
    async for s in dependencies.get_tenant_db(ctx): assert s
    user = security.TokenPayload("u", None, None, {}, {})
    assert await dependencies.get_current_user(user) is user


async def _one_session():
    yield AsyncMock()


@pytest.mark.asyncio
async def test_remaining_small_helpers(monkeypatch):
    from app.db import master, session as db_session
    from app.events import publisher as event_pub
    assert master.get_master_db()
    with master.get_master_session() as db: assert db
    monkeypatch.setattr(event_pub, "publish_event", AsyncMock())
    await event_pub.publish_bill_created("b", "t")
    await event_pub.publish_payment_received("p", "t")
    await event_pub.publish_payment_received("p", "t", "v")
    monkeypatch.setattr(db_session, "get_tenant_session", lambda _: _one_session())
    async for s in db_session.get_tenant_db("t"): assert s


@pytest.mark.asyncio
async def test_billing_push_to_100_coverage(monkeypatch):
    from app import exceptions, main
    from app.core import database, limiter, middleware, security, tenant_auth
    from app.db import tenant as tenant_db
    from app.events import subscriber as event_sub
    from app.messaging import connection, subscriber as msg_sub
    from app.services import billing as service, tenant_service
    from app.api.v1 import router as api_router

    # models & utcnow & default ward day rate
    assert _utcnow()
    monkeypatch.setattr(service.settings, "default_ward_day_rate", None)
    res_none = MagicMock(); res_none.scalars.return_value.first.return_value = None
    db_fee_none = AsyncMock(execute=AsyncMock(return_value=res_none))
    assert await service._ward_day_unit_price(db_fee_none) == Decimal("100.00")


    # router _enrich_patient_info exception path
    bill_obj = MagicMock(patient_id=uuid4())
    b_out = MagicMock()
    db_err = AsyncMock(); db_err.execute.side_effect = RuntimeError("db err")
    await api_router._enrich_patient_info(db_err, bill_obj, b_out)

    # router list_all_bills exception path
    db_all_err = AsyncMock(); db_all_err.execute.side_effect = RuntimeError("db all err")
    assert await api_router.list_all_bills(db_all_err) == []


    # router adjust_bill net_payable <= 0
    adj_bill = MagicMock(
        bill_id=uuid4(), visit_id=uuid4(), admission_id=None, patient_id=uuid4(),
        status="open", total_amount=Decimal("10.00"), discount_amount=Decimal("0.00"),
        paid_amount=Decimal("10.00"), currency="TZS", patient_name="Name", patient_number="P123",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)
    )
    adj_res = MagicMock(); adj_res.scalars.return_value.first.return_value = adj_bill
    items_res = MagicMock(); items_res.scalars.return_value.all.return_value = []
    db_adj = AsyncMock(); db_adj.execute.side_effect = [adj_res, items_res]
    res_adj = await api_router.adjust_bill(adj_bill.bill_id, MagicMock(discount_amount=Decimal("0.00")), db_adj)
    assert adj_bill.status == "paid"





    # services.billing apply_drug_charge missing bill creation and existing item check
    db_drug = AsyncMock()
    disp_row = MagicMock(); disp_row.fetchone.return_value = ("visit1", 2, "DrugA", "D1", 10.0, "PrescA")
    visit_row = MagicMock(); visit_row.fetchone.return_value = ("patient1",)
    bill_no = MagicMock(); bill_no.scalars.return_value.first.return_value = None
    existing_yes = MagicMock(); existing_yes.scalars.return_value.first.return_value = MagicMock()
    db_drug.execute.side_effect = [disp_row, visit_row, bill_no, existing_yes]
    assert await service.apply_drug_charge(db_drug, dispensing_id=str(uuid4()), tenant_id="t") is None

    # security & tenant_auth token decoding fallbacks
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(side_effect=RuntimeError("jwks down")))
    with pytest.raises(Exception):
        import jwt
        await security._decode_token(jwt.encode({"iss": f"{security.settings.keycloak_url}/realms/r"}, "s", algorithm="HS256", headers={"kid": "k"}))

    # tenant_db async engine cache hit
    tenant_db._async_engine_cache["cached_dsn"] = MagicMock()
    assert await tenant_db._get_async_session_factory("cached_dsn")


    # security & tenant_auth token decoding failures
    with pytest.raises(Exception): await security._decode_token("bad")
    with pytest.raises(Exception): await tenant_auth._decode_token("bad")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant("bad")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant_ws(MagicMock(headers={}))

    # security require_role permission denied
    with pytest.raises(Exception):
        await security.require_role("admin")(security.TokenPayload("u", None, None, {"roles": ["doctor"]}, {}))

    # database router & limiter
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    monkeypatch.setattr(database, "create_engine", MagicMock(side_effect=RuntimeError("eng err")))
    with pytest.raises(Exception): database.get_session_local()
    monkeypatch.setattr(database, "get_session_local", lambda: lambda: MagicMock())
    assert database.DefaultDatabaseRouter().get_session("h")

    from starlette.requests import Request
    req_no_client = Request({"type": "http", "headers": []})
    assert limiter.get_remote_address(req_no_client) == "127.0.0.1"

    # tenant_db async engine failure
    class BadConn:
        async def run_sync(self, fn): raise RuntimeError("sync err")
    class BadBegin:
        async def __aenter__(self): return BadConn()
        async def __aexit__(self, *a): pass
    bad_engine = MagicMock(begin=lambda: BadBegin())
    monkeypatch.setattr(tenant_db, "create_async_engine", lambda *a, **k: bad_engine)
    tenant_db._async_engine_cache.clear()
    with pytest.raises(Exception): await tenant_db._get_async_session_factory("bad_t")

    # messaging connection failure
    monkeypatch.setattr(connection.aio_pika, "connect_robust", AsyncMock(side_effect=RuntimeError("pika down")))
    connection._connection = None
    with pytest.raises(Exception): await connection.get_connection()

    # messaging subscriber consumer loop
    class DummyQueueIter:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def __anext__(self): raise StopAsyncIteration
    dummy_queue = MagicMock(); dummy_queue.iterator = lambda: DummyQueueIter(); dummy_queue.bind = AsyncMock()
    dummy_channel = MagicMock(); dummy_channel.declare_queue = AsyncMock(return_value=dummy_queue); dummy_channel.set_qos = AsyncMock()
    dummy_conn = MagicMock(); dummy_conn.channel = AsyncMock(return_value=dummy_channel)

    monkeypatch.setattr(msg_sub, "get_connection", AsyncMock(return_value=dummy_conn))
    monkeypatch.setattr(msg_sub, "declare_exchange", AsyncMock())
    await msg_sub.run_consumer_task("q", ["k"], AsyncMock())

    # main CORS allowed origins & lifespan
    monkeypatch.setattr(main.settings, "allowed_origins", "http://localhost, http://example.com")
    monkeypatch.setattr(main.settings, "environment", "prod")
    assert (await main.health())["status"] == "ok"
    req = MagicMock(method="GET"); req.headers = {}
    async def call(_): return __import__("starlette.responses", fromlist=["Response"]).Response("ok")
    res_prod = await main.security_headers(req, call)
    assert res_prod.headers["Content-Security-Policy"] == "default-src 'none'"

    # tenant_service revoke & fallback DSN
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", MagicMock(side_effect=RuntimeError("client err")))
    await tenant_service._revoke_keycloak_sessions("t")
    db_none = MagicMock(); db_none.execute.return_value.scalar.return_value = None


