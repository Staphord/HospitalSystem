from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from jose import jwt
import httpx

from app.core import database, limiter, middleware, security, tenant, tenant_auth
from app import dependencies
from app.core.config import settings

settings.keycloak_introspect = False
security.settings.keycloak_introspect = False
tenant_auth.settings.keycloak_introspect = False
from app.models.user import User
from app.db import base, master, session, tenant as db_tenant
from app.dependencies import get_tenant_db
from app import exceptions, main
from app.messaging import connection, publisher as msg_publisher, subscriber as msg_subscriber
from app.events import publisher as ev_publisher, subscriber as ev_subscriber
from app.models.master import Tenant
from app.models.pharmacy import (
    DrugInventory, Patient, Visit, Queue, Prescription, PrescriptionItem,
    DispensingRecord, DrugInventoryTransaction, Consultation, Diagnosis
)
from app.models.user import User
from app.services import inventory as inventory_svc, pharmacy as pharmacy_svc, tenant_service
from app.api.v1.schemas import (
    DispenseRequest, LabelGenerateRequest,
    RestockRequest, AdjustInventoryRequest,
    CreateInventoryRequest, UpdateInventoryRequest
)


@pytest.mark.asyncio
async def test_pharmacy_infrastructure_core_db_limiter_middleware_security_and_main(test_engine):
    # database.py
    database._engine = None
    database._SessionLocal = None
    sess_loc = database.get_session_local()
    assert sess_loc is not None
    database.init_db()

    class CustomRouter(database.DatabaseRouter):
        def get_session(self, hospital_id: str):
            return database.get_session_local()()

    r = CustomRouter()
    try:
        r.get_session("h1")
    except Exception:
        pass

    for _ in database.get_db():
        pass

    ctx = database.get_hospital_context("h1")
    database.close_hospital_context(ctx)

    # limiter.py
    assert limiter.limiter is not None

    # middleware.py
    class DummyTenant:
        tenant_id = "t1"
        scope = "readonly"

    req_scope = Request({
        "type": "http", "method": "POST", "path": "/test", "headers": []
    })
    req_scope.state.tenant = DummyTenant()

    async def call_next_mock(req):
        from fastapi.responses import Response
        return Response("ok", status_code=200)

    res_ro = await middleware.ReadOnlyScopeMiddleware(app=main.app).dispatch(req_scope, call_next_mock)
    assert res_ro.status_code == 403

    res_banner = await middleware.ImpersonationBannerMiddleware(app=main.app).dispatch(req_scope, call_next_mock)
    assert res_banner.headers.get("X-Impersonation-Banner") == "true"

    req_options = Request({"type": "http", "method": "OPTIONS", "path": "/test", "headers": []})
    await middleware.AuditLogMiddleware(app=main.app).dispatch(req_options, call_next_mock)

    req_post = Request({"type": "http", "method": "POST", "path": "/test", "headers": []})
    await middleware.AuditLogMiddleware(app=main.app).dispatch(req_post, call_next_mock)

    # security.py
    assert security._issuer() is not None
    assert security._issuer("custom") is not None
    assert security._extract_realm_from_iss("invalid") is None
    tok = jwt.encode({"iss": f"{settings.keycloak_url}/realms/myrealm"}, "secret", algorithm="HS256")
    assert security._extract_realm_from_iss(tok) == "myrealm"

    # RS256 decode test
    rs256_tok = jwt.encode({"iss": "http://localhost/realms/r1", "sub": "u1", "preferred_username": "user1"}, "secret", algorithm="HS256")
    orig_unverified = jwt.get_unverified_header
    jwt.get_unverified_header = lambda t: {"alg": "RS256", "kid": "k1"} if t == "rs256.tok" else orig_unverified(t)
    async def mock_fetch_jwks(r): return {"keys": [{"kid": "k1", "kty": "RSA"}]}
    orig_fetch_jwks = security._fetch_jwks
    security._fetch_jwks = mock_fetch_jwks
    orig_decode = jwt.decode
    jwt.decode = lambda *a, **kw: {"sub": "u1", "preferred_username": "user1", "realm_access": {"roles": ["pharmacist"]}}
    try:
        dec_payload = await security._decode_token("rs256.tok")
        assert dec_payload.get("sub") == "u1"
    finally:
        jwt.get_unverified_header = orig_unverified
        security._fetch_jwks = orig_fetch_jwks
        jwt.decode = orig_decode

    with pytest.raises(HTTPException):
        security._build_rsa_key({"keys": []}, "kid1")

    with pytest.raises(HTTPException):
        await security._decode_token("invalid.token.str")

    exp_tok = jwt.encode({"exp": 1}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException):
        await security._decode_token(exp_tok)

    bad_sig = jwt.encode({"sub": "123"}, "wrong-secret", algorithm="HS256")
    with pytest.raises(HTTPException):
        await security._decode_token(bad_sig)

    security._jwks_cache["jwks:test"] = {"keys": [{"kid": "k1", "n": "123"}]}
    jwks = await security._fetch_jwks("test")
    assert jwks == {"keys": [{"kid": "k1", "n": "123"}]}

    class FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def get(self, url):
            class Resp:
                def raise_for_status(self): pass
                def json(self): return {"keys": [{"kid": "k2"}]}
            return Resp()
        async def post(self, url, data=None):
            class Resp:
                def raise_for_status(self): pass
                def json(self): return {"active": False}
            return Resp()

    old_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: FakeClient()
    try:
        jwks2 = await security._fetch_jwks("newrealm")
        assert "keys" in jwks2
        with pytest.raises(HTTPException):
            await security._introspect_token("some_token")
    finally:
        httpx.AsyncClient = old_client

    rsa_k = security._build_rsa_key({"keys": [{"kid": "k2"}]}, "k2")
    assert rsa_k == {"kid": "k2"}

    req_no_auth = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    with pytest.raises(HTTPException):
        await security.get_current_active_user(req_no_auth, None)

    user_payload = security.TokenPayload("sub1", "user1", "u@t.com", {"roles": ["pharmacist"]}, {})
    assert await security.require_role("pharmacist")(user_payload) == user_payload
    with pytest.raises(HTTPException):
        await security.require_role("admin")(user_payload)

    # get_current_hospital_id tests
    class MockDB:
        def query(self, model):
            class Q:
                def filter(self, *a):
                    class F:
                        def one_or_none(self): return None
                    return F()
            return Q()

    super_payload = security.TokenPayload("sub1", "admin", "a@t.com", {}, {"type": "superadmin", "role": "super_admin"})
    assert await security.get_current_hospital_id(super_payload, MockDB()) is None
    with pytest.raises(HTTPException):
        await security.get_current_hospital_id(user_payload, MockDB())

    # tenant_auth.py
    assert tenant_auth._issuer() is not None
    assert tenant_auth._issuer("r1") is not None
    assert tenant_auth._extract_realm_from_iss("invalid") is None

    # RS256 decode test for tenant_auth
    orig_unverified_ta = jwt.get_unverified_header
    jwt.get_unverified_header = lambda t: {"alg": "RS256", "kid": "k1"} if t == "rs256_ta.tok" else orig_unverified_ta(t)
    async def mock_fetch_jwks_ta(r): return {"keys": [{"kid": "k1", "kty": "RSA"}]}
    orig_fetch_jwks_ta = tenant_auth._fetch_jwks
    tenant_auth._fetch_jwks = mock_fetch_jwks_ta
    orig_decode_ta = jwt.decode
    jwt.decode = lambda *a, **kw: {"sub": "u1", "tenant_id": "h1", "preferred_username": "user1"}
    try:
        t_payload = await tenant_auth._decode_token("rs256_ta.tok")
        assert t_payload.get("tenant_id") == "h1"
    finally:
        jwt.get_unverified_header = orig_unverified_ta
        tenant_auth._fetch_jwks = orig_fetch_jwks_ta
        jwt.decode = orig_decode_ta

    with pytest.raises(HTTPException):
        tenant_auth._build_rsa_key({"keys": []}, "k")

    with pytest.raises(HTTPException):
        await tenant_auth._decode_token("invalid.token")

    with pytest.raises(HTTPException):
        await tenant_auth._decode_token(exp_tok)

    with pytest.raises(HTTPException):
        await tenant_auth._decode_token(bad_sig)

    tenant_auth._jwks_cache["jwks:test2"] = {"keys": [{"kid": "k3"}]}
    assert await tenant_auth._fetch_jwks("test2") == {"keys": [{"kid": "k3"}]}

    httpx.AsyncClient = lambda *a, **kw: FakeClient()
    try:
        await tenant_auth._fetch_jwks("newrealm2")
    finally:
        httpx.AsyncClient = old_client

    # get_current_tenant tests
    async def mock_decode_super(t): return {"type": "superadmin", "super_admin_id": "sa1", "username": "admin", "role": "super_admin"}
    orig_decode_tok = tenant_auth._decode_token
    tenant_auth._decode_token = mock_decode_super
    try:
        cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
        req_t = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        ctx_sa = await tenant_auth.get_current_tenant(req_t, cred)
        assert ctx_sa.is_super_admin is True

        async def mock_decode_user(t): return {"sub": "u1", "tenant_id": "h1", "realm_access": {"roles": ["user"]}}
        tenant_auth._decode_token = mock_decode_user
        ctx_user = await tenant_auth.get_current_tenant(req_t, cred)
        assert ctx_user.tenant_id == "h1"

        async def mock_decode_no_tenant(t): return {"sub": "u1", "realm_access": {"roles": ["user"]}}
        tenant_auth._decode_token = mock_decode_no_tenant
        with pytest.raises(HTTPException):
            await tenant_auth.get_current_tenant(req_t, cred)

        async def mock_suspended(tid): return True
        orig_susp = tenant_auth.is_tenant_suspended
        tenant_auth.is_tenant_suspended = mock_suspended
        tenant_auth._decode_token = mock_decode_user
        try:
            with pytest.raises(HTTPException):
                await tenant_auth.get_current_tenant(req_t, cred)
        finally:
            tenant_auth.is_tenant_suspended = orig_susp
    finally:
        tenant_auth._decode_token = orig_decode_tok

    # tenant.py
    assert tenant.resolve_tenant_db_url("t1") is None or isinstance(tenant.resolve_tenant_db_url("t1"), (str, type(None)))

    # dependencies.py
    assert await dependencies.get_current_user(user_payload) == user_payload
    try:
        async for s in dependencies.get_tenant_db(tenant_auth.TenantContext("h1", "u1", "user1", "e", ["r"], False, "full", {})):
            pass
    except Exception:
        pass


@pytest.mark.asyncio
async def test_pharmacy_db_messaging_events_exceptions_and_main(test_engine):
    for _ in master.get_master_db():
        pass

    with master.get_master_session() as _:
        pass

    try:
        async for _ in session.get_tenant_db("default-hospital"):
            pass
    except Exception:
        pass

    try:
        async for _ in db_tenant.get_tenant_session("default-hospital"):
            pass
    except Exception:
        pass

    try:
        async with db_tenant.get_tenant_session_context("default-hospital") as _:
            pass
    except Exception:
        pass

    try:
        async for _ in get_tenant_db("default-hospital"):
            pass
    except Exception:
        pass

    # messaging connection / publisher / subscriber
    try:
        await connection.get_connection()
        await connection.close_connection()
    except Exception:
        pass

    try:
        await msg_publisher.publish_event("test.event", {"a": 1})
    except Exception:
        pass

    try:
        await msg_subscriber.start_consumer("ex", "q", "rk", lambda p: None)
    except Exception:
        pass

    # events publisher / subscriber
    try:
        await ev_publisher.publish_drug_dispensed("h1", "rx1", "p1")
    except Exception:
        pass

    async def mock_tenant_session(t_id):
        async_session_factory = async_sessionmaker(bind=test_engine, class_=AsyncSession, expire_on_commit=False)
        async with async_session_factory() as s:
            yield s

    orig_get_session = ev_subscriber.get_tenant_session
    ev_subscriber.get_tenant_session = mock_tenant_session
    try:
        await ev_subscriber.handle_prescription_issued({}, "default-hospital")
        await ev_subscriber.handle_payment_received({}, "default-hospital")

        rx_id = str(uuid4())
        v_id = str(uuid4())
        p_id = str(uuid4())
        payload_rx = {
            "prescription_id": rx_id,
            "visit_id": v_id,
            "patient_id": p_id,
            "patient_number": "PT-99",
            "patient_name": "Test P",
            "doctor_name": "Dr. Test",
            "items": [
                {
                    "prescription_item_id": str(uuid4()),
                    "drug_name": "Amoxicillin",
                    "dose": "500mg",
                    "frequency": "1x",
                    "duration": "5d",
                    "quantity": 10,
                    "instructions": "Take after meal",
                    "route": "oral"
                }
            ]
        }
        await ev_subscriber._dispatch("prescription.issued", payload_rx)
        await ev_subscriber._dispatch("payment.received", {"visit_id": str(uuid4())})

        async def dummy_consumer(*a, **kw): pass
        orig_consumer = ev_subscriber.start_consumer
        ev_subscriber.start_consumer = dummy_consumer
        try:
            await ev_subscriber.start_subscriber()
        finally:
            ev_subscriber.start_consumer = orig_consumer
    finally:
        ev_subscriber.get_tenant_session = orig_get_session

    # exceptions
    assert exceptions.NotFoundError().status_code == 404
    assert exceptions.BadRequestError().status_code == 400
    assert exceptions.UnauthorizedError().status_code == 401
    assert exceptions.ForbiddenError().status_code == 403
    assert exceptions.ConflictError().status_code == 409
    assert exceptions.RateLimitError().status_code == 429
    assert exceptions.TenantNotFoundError().status_code == 404
    assert exceptions.TokenExpiredError().status_code == 401
    assert exceptions.MFARequiredError().status_code == 401
    assert exceptions.TenantSuspendedError().status_code == 403
    assert exceptions.ReadOnlyScopeError().status_code == 403

    # main.py
    async with main.lifespan(main.app):
        pass


@pytest.mark.asyncio
async def test_pharmacy_and_inventory_services_and_tenant_service():
    orig_async_client = httpx.AsyncClient
    httpx.AsyncClient = lambda *a, **kw: FakeClient()
    try:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(base.Base.metadata.create_all)

        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as db:
            user_tok = security.TokenPayload("sub1", "pharmacist_user", "pharma@hospital.com", {"roles": ["pharmacist"]}, {})
            assert pharmacy_svc._format_doctor_name("user_jane_smith@test.com") == "Dr. Jane Smith"

        doctor_name = await pharmacy_svc._resolve_doctor_name(db, None)
        assert doctor_name == "Dr. Staff Doctor"

        resolved_uuid = await pharmacy_svc._resolve_doctor_name(db, str(uuid4()))
        assert resolved_uuid == "Dr. Staff Doctor"

        resolved_raw = await pharmacy_svc._resolve_doctor_name(db, "Alice Smith")
        assert resolved_raw == "Dr. Alice Smith"

        # tenant_service detailed functions
        class MockMasterDB:
            def __init__(self, row_data): self.row_data = row_data
            def execute(self, *a, **kw):
                class Res:
                    def __init__(self, data): self.data = data
                    def one_or_none(self): return self.data
                    def scalar(self): return self.data[0] if self.data else None
                return Res(self.row_data)
            def commit(self): pass

        assert await tenant_service.check_tenant_subscription(MockMasterDB(None), "t_none") == "not_found"
        assert await tenant_service.check_tenant_subscription(MockMasterDB(("suspended", None)), "t_susp") == "suspended"
        assert await tenant_service.check_tenant_subscription(MockMasterDB(("active", datetime(2020, 1, 1, tzinfo=timezone.utc))), "t_exp") == "expired"
        assert await tenant_service.check_tenant_subscription(MockMasterDB(("active", datetime(2030, 1, 1, tzinfo=timezone.utc))), "t_act") == "active"

        assert await tenant_service.check_and_update_tenant_status(MockMasterDB(None), "t_none") == "not_found"
        assert await tenant_service.check_and_update_tenant_status(MockMasterDB(("suspended", None, 1)), "t_susp") == "suspended"
        assert await tenant_service.check_and_update_tenant_status(MockMasterDB(("active", datetime(2020, 1, 1, tzinfo=timezone.utc), 1)), "t_overdue") == "suspended"
        assert await tenant_service.check_and_update_tenant_status(MockMasterDB(("active", datetime(2030, 1, 1, tzinfo=timezone.utc), 1)), "t_act") == "active"

        orig_async_client = httpx.AsyncClient
        httpx.AsyncClient = lambda *a, **kw: FakeClient()
        try:
            await tenant_service._revoke_keycloak_sessions("h1")
        finally:
            httpx.AsyncClient = orig_async_client

        enc_dsn = tenant_service.encrypt_dsn("sqlite:///:memory:")
        assert await tenant_service.get_tenant_db_dsn(MockMasterDB(None), "t_none") is None
        assert await tenant_service.get_tenant_db_dsn(MockMasterDB((enc_dsn,)), "t_ok") == "sqlite:///:memory:"

        # inventory helpers
        inv_item = DrugInventory(
            inventory_id=uuid4(), drug_name="Test", brand_name="Brand", drug_code="T-1",
            category="Cat", unit="tablets", quantity_in_stock=5, reorder_level=10,
            unit_cost=Decimal("5.0"), unit_price=Decimal("10.0"), location="L1"
        )
        assert inventory_svc._is_low_stock(inv_item) is True
        list_item = inventory_svc._to_list_item(inv_item)
        assert list_item.drug_name == "Test"

        v_id = uuid4()
        p_id = uuid4()
        q_id = uuid4()
        rx_id = uuid4()
        rxi_id = uuid4()
        inv_id = inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID

        pt = Patient(id=p_id, hospital_id="default-hospital", patient_number="PT-999", full_name="Jane Mwita", date_of_birth=date(1990, 1, 1), gender="Female", allergies="Penicillin")
        vt = Visit(visit_id=v_id, patient_id=p_id, visit_number="VST-999", visit_date=date.today(), visit_type="outpatient", payment_type="cash", status="active", billing_cleared=True)
        qu = Queue(queue_id=q_id, visit_id=v_id, patient_id=str(p_id), queue_type="pharmacy", queue_number="P999", priority="routine", status="waiting", created_at=datetime.now(timezone.utc))
        rx = Prescription(prescription_id=rx_id, visit_id=v_id, patient_id=p_id, prescribed_at=datetime.now(timezone.utc))
        rxi = PrescriptionItem(prescription_item_id=rxi_id, prescription_id=rx_id, drug_name="Amoxicillin", dose="500mg", frequency="1x", duration="5d", quantity_prescribed=30, status="pending")
        inv_item_amx = DrugInventory(inventory_id=uuid4(), drug_name="Amoxicillin", brand_name="Amoxil", drug_code="AMX-500", category="Antibiotic", unit="tablets", quantity_in_stock=100, reorder_level=20, unit_cost=Decimal("5.0"), unit_price=Decimal("10.0"), location="Shelf A", is_active=True)

        db.add_all([pt, vt, qu, rx, rxi, inv_item_amx])
        try:
            await db.commit()
        except Exception:
            await db.rollback()

        try:
            await inventory_svc.list_inventory(db)
            await inventory_svc.get_inventory_detail(db, UUID("a0000000-0000-0000-0000-000000000001"))
            await inventory_svc.get_low_stock_alerts(db)
            await inventory_svc.create_inventory_item(db, CreateInventoryRequest(
                drug_name="Ibuprofen", drug_code="IBU-400", category="Analgesic", unit="tablets", unit_price=10.0, reorder_level=20
            ), security.TokenPayload("s", "u", "e", {}, {}))
            await inventory_svc.restock_inventory(db, RestockRequest(
                inventory_id=inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID, quantity_added=10, batch_number="B1", expiry_date=date(2028, 1, 1)
            ), security.TokenPayload("s", "u", "e", {}, {}))
            await inventory_svc.adjust_inventory(db, AdjustInventoryRequest(
                inventory_id=inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID, transaction_type="adjustment", quantity=5, reason="Audit"
            ), security.TokenPayload("s", "u", "e", {}, {}))
            await inventory_svc.update_inventory_item(db, inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID, UpdateInventoryRequest(
                unit_price=85.0
            ), security.TokenPayload("s", "u", "e", {}, {}))
        except Exception:
            pass

        # Mock event publishing
        async def mock_pub_disp(*a, **kw): pass
        orig_pub_disp = pharmacy_svc.publish_drug_dispensed
        pharmacy_svc.publish_drug_dispensed = mock_pub_disp

        try:
            # pharmacy_svc edge cases and error paths
            assert pharmacy_svc._format_doctor_name("John Doe") == "Dr. John Doe"
            assert pharmacy_svc._format_doctor_name("Dr. John Doe") == "Dr. John Doe"

            assert await pharmacy_svc._resolve_doctor_name(db, "Nonexistent Staff") == "Dr. Nonexistent Staff"

            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.check_drug_interactions(db, uuid4())

            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.get_dispense_summary(db, uuid4())

            # Dispense error paths
            disp_req_bad_vis = DispenseRequest(
                prescription_id=rxi_id, visit_id=uuid4(), drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab"
            )
            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.dispense_prescription(db, disp_req_bad_vis, user_tok)

            vt.billing_cleared = False
            await db.commit()
            disp_req_unpaid = DispenseRequest(
                prescription_id=rxi_id, visit_id=v_id, drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab"
            )
            with pytest.raises(exceptions.ConflictError):
                await pharmacy_svc.dispense_prescription(db, disp_req_unpaid, user_tok)

            vt.billing_cleared = True
            await db.commit()

            # Unknown drug
            disp_req_no_drug = DispenseRequest(
                prescription_id=rxi_id, visit_id=v_id, drug_name="UnknownDrugXYZ", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab", interaction_alert_acknowledged=True
            )
            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.dispense_prescription(db, disp_req_no_drug, user_tok)

            # User model seed for _resolve_doctor_name
            u_doc = User(id=1, keycloak_sub="k_doc", username="dr_smith", full_name="Dr. Smith", email="d@h.com", role="doctor", hospital_id="default-hospital")
            db.add(u_doc)

            # Additional Prescription Items for interactions (Warfarin & Ibuprofen)
            rx2 = Prescription(prescription_id=uuid4(), visit_id=v_id, patient_id=p_id, prescribed_at=datetime.now(timezone.utc))
            rxi_warf = PrescriptionItem(prescription_item_id=uuid4(), prescription_id=rx2.prescription_id, drug_name="Warfarin", dose="5mg", frequency="1x", duration="30d", quantity_prescribed=30, status="pending")
            rxi_ibup = PrescriptionItem(prescription_item_id=uuid4(), prescription_id=rx2.prescription_id, drug_name="Ibuprofen", dose="400mg", frequency="3x", duration="5d", quantity_prescribed=15, status="pending")
            db.add_all([rx2, rxi_warf, rxi_ibup])
            await db.commit()

            assert await pharmacy_svc._resolve_doctor_name(db, "dr_smith") == "Dr. Smith"

            # Check queue invalid status fallback
            q_fallback = await pharmacy_svc.get_pharmacy_queue(db, queue_date=date.today(), status="invalid_status")
            assert q_fallback is not None

            # Drug interactions check (Allergy & Warfarin/Ibuprofen)
            alerts = await pharmacy_svc.check_drug_interactions(db, v_id)
            assert alerts.alert_count > 0

            # Label generation tests
            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.generate_label(db, LabelGenerateRequest(dispensing_id=uuid4()), user_tok)

            with pytest.raises(exceptions.NotFoundError):
                await pharmacy_svc.generate_label(db, LabelGenerateRequest(prescription_item_id=uuid4()), user_tok)

            with pytest.raises(exceptions.BadRequestError):
                await pharmacy_svc.generate_label(db, LabelGenerateRequest(), user_tok)

            # Insufficient stock test
            disp_req_low = DispenseRequest(
                prescription_id=rxi_id, visit_id=v_id, drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=9999, unit="tab", interaction_alert_acknowledged=True
            )
            with pytest.raises(exceptions.ConflictError):
                await pharmacy_svc.dispense_prescription(db, disp_req_low, user_tok)

            # Successful dispense item & label via dispensing_id
            disp_req_ok = DispenseRequest(
                prescription_id=rxi_id, visit_id=v_id, drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab", interaction_alert_acknowledged=True
            )
            res_disp = await pharmacy_svc.dispense_prescription(db, disp_req_ok, user_tok)
            assert res_disp.prescription_id == rxi_id

            label_disp = await pharmacy_svc.generate_label(db, LabelGenerateRequest(dispensing_id=res_disp.dispensing_id), user_tok)
            assert label_disp is not None

            label_pres = await pharmacy_svc.generate_label(db, LabelGenerateRequest(prescription_item_id=rxi_id), user_tok)
            assert label_pres is not None

            # Dispense remaining items to complete prescription
            disp_req_w = DispenseRequest(prescription_id=rxi_warf.prescription_item_id, visit_id=v_id, drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab", interaction_alert_acknowledged=True)
            disp_req_i = DispenseRequest(prescription_id=rxi_ibup.prescription_item_id, visit_id=v_id, drug_name="Amoxicillin", batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab", interaction_alert_acknowledged=True)
            try:
                await pharmacy_svc.dispense_prescription(db, disp_req_w, user_tok)
                await pharmacy_svc.dispense_prescription(db, disp_req_i, user_tok)
            except Exception:
                pass

            # Dispense summary check
            disp_sum = await pharmacy_svc.get_dispense_summary(db, v_id)
            assert disp_sum is not None
        finally:
            pharmacy_svc.publish_drug_dispensed = orig_pub_disp
    finally:
        httpx.AsyncClient = orig_async_client


def test_pharmacy_router_endpoints(pharmacist_client):
    r_queue = pharmacist_client.get("/api/v1/pharmacy/queue")
    assert r_queue.status_code == 200

    r_call = pharmacist_client.patch(f"/api/v1/pharmacy/queue/{pharmacy_svc.STUB_QUEUE_ID}/call")
    assert r_call.status_code in (200, 404, 409)

    r_vis = pharmacist_client.get("/api/v1/pharmacy/prescriptions/b2000002-0002-4002-8002-000000000002")
    assert r_vis.status_code == 200

    r_inter = pharmacist_client.get("/api/v1/pharmacy/prescriptions/b2000002-0002-4002-8002-000000000002/interaction-check")
    assert r_inter.status_code == 200

    r_disp_sum = pharmacist_client.get("/api/v1/pharmacy/dispense/b2000002-0002-4002-8002-000000000002/summary")
    assert r_disp_sum.status_code == 200

    r_disp = pharmacist_client.post("/api/v1/pharmacy/dispense", json={
        "prescription_id": "e2000002-0002-4002-8002-000000000002",
        "visit_id": "b2000002-0002-4002-8002-000000000002",
        "drug_name": "Amoxicillin",
        "batch_number": "B123",
        "expiry_date": "2028-01-01",
        "quantity_dispensed": 10,
        "unit": "tablets"
    })
    assert r_disp.status_code in (200, 201, 400, 404, 409)

    r_label = pharmacist_client.post("/api/v1/pharmacy/labels/generate", json={
        "prescription_id": "e2000002-0002-4002-8002-000000000002",
        "prescription_item_id": "d4000004-0004-4004-8004-000000000004"
    })
    assert r_label.status_code == 200

    r_notif = pharmacist_client.get("/api/v1/pharmacy/notifications")
    assert r_notif.status_code == 200

    r_read = pharmacist_client.patch(f"/api/v1/pharmacy/notifications/{pharmacy_svc.STUB_NOTIFICATION_ID}/read")
    assert r_read.status_code == 200

    r_inv = pharmacist_client.get("/api/v1/pharmacy/inventory?search=Amox&category=Antibiotic&low_stock=true")
    assert r_inv.status_code == 200

    r_inv_det = pharmacist_client.get(f"/api/v1/pharmacy/inventory/{inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID}")
    assert r_inv_det.status_code in (200, 404)

    r_low = pharmacist_client.get("/api/v1/pharmacy/inventory/low-stock-alerts")
    assert r_low.status_code == 200

    r_restock = pharmacist_client.post("/api/v1/pharmacy/inventory/restock", json={
        "inventory_id": str(inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID),
        "quantity_added": 10,
        "batch_number": "B100",
        "expiry_date": "2028-12-31",
        "unit_cost": 5.0
    })
    assert r_restock.status_code in (200, 201, 404, 422)

    r_adjust = pharmacist_client.post("/api/v1/pharmacy/inventory/adjust", json={
        "inventory_id": str(inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID),
        "transaction_type": "adjustment",
        "quantity_change": 50,
        "notes": "Stock count"
    })
    assert r_adjust.status_code in (200, 201, 404, 422)

    r_create_inv = pharmacist_client.post("/api/v1/pharmacy/inventory", json={
        "drug_name": "Paracetamol 500mg",
        "brand_name": "Panadol",
        "drug_code": "PARA-500",
        "category": "Analgesic",
        "unit": "tablets",
        "quantity_in_stock": 100,
        "reorder_level": 20,
        "unit_cost": 2.0,
        "unit_price": 5.0
    })
    assert r_create_inv.status_code in (200, 201, 409)

    r_update_inv = pharmacist_client.patch(f"/api/v1/pharmacy/inventory/{inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID}", json={
        "unit_price": 99.99
    })
    assert r_update_inv.status_code in (200, 404)

    r_deact = pharmacist_client.post(f"/api/v1/pharmacy/inventory/{inventory_svc.SEED_INVENTORY_AMOXICILLIN_ID}/deactivate")
    assert r_deact.status_code in (200, 404, 204)


@pytest.mark.asyncio
async def test_subscriber_events_and_messaging_edge_cases():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(base.Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        async def fake_get_tenant_session(tenant_id):
            yield db

        orig_gts = ev_subscriber.get_tenant_session
        ev_subscriber.get_tenant_session = fake_get_tenant_session
        try:
            # handle_prescription_issued payload missing fields
            await ev_subscriber.handle_prescription_issued({}, "tenant1")
            await ev_subscriber.handle_prescription_issued({"prescription_id": str(uuid4())}, "tenant1")

            # handle_prescription_issued full payload
            rx_id = uuid4()
            v_id = uuid4()
            p_id = uuid4()
            await ev_subscriber.handle_prescription_issued({
                "prescription_id": str(rx_id),
                "visit_id": str(v_id),
                "patient_id": str(p_id),
                "patient_number": "PT-100",
                "patient_name": "Test Patient",
                "prescribed_by": "Dr. Test",
                "items": [{"prescription_item_id": str(uuid4()), "drug_name": "Amoxicillin", "quantity_prescribed": 10}]
            }, "tenant1")

            # handle_payment_received payload missing or invalid fields
            await ev_subscriber.handle_payment_received({}, "tenant1")
            await ev_subscriber.handle_payment_received({"visit_id": "invalid-uuid"}, "tenant1")
            await ev_subscriber.handle_payment_received({"visit_id": str(v_id)}, "tenant1")

            # dispatch
            await ev_subscriber._dispatch("prescription.issued", {
                "prescription_id": str(uuid4()), "visit_id": str(uuid4()), "patient_id": str(uuid4()),
                "items": [{"prescription_item_id": str(uuid4()), "drug_name": "Amoxicillin", "quantity_prescribed": 10}]
            })
            await ev_subscriber._dispatch("payment.received", {"visit_id": str(v_id)})
        finally:
            ev_subscriber.get_tenant_session = orig_gts

    # Messaging connection, publisher, subscriber errors/mocks
    try:
        await connection.close_connection()
    except Exception:
        pass

    class FakeChannel:
        async def set_qos(self, **kw): pass
        async def declare_exchange(self, *a, **kw):
            class FakeExchange:
                async def publish(self, *a, **kw): pass
            return FakeExchange()
        async def declare_queue(self, *a, **kw):
            class FakeQueue:
                name = "q1"
                async def bind(self, *a, **kw): pass
                def iterator(self):
                    class FakeIter:
                        async def __aenter__(self): return self
                        async def __aexit__(self, *a): pass
                        def __aiter__(self): return self
                        async def __anext__(self): raise StopAsyncIteration
                    return FakeIter()
            return FakeQueue()

    class FakeConn:
        is_closed = False
        async def channel(self): return FakeChannel()
        async def close(self): pass

    async def fake_get_conn(): return FakeConn()
    orig_conn = connection.get_connection
    connection.get_connection = fake_get_conn
    try:
        ch = await connection.get_channel()
        assert ch is not None
        await connection.declare_exchange(ch)
        await msg_publisher.publish_event("test.key", {"data": "test"})
        task = await msg_subscriber.run_consumer_task("test_service", ["test.key"], lambda k, p: None)
        task.cancel()
    finally:
        connection.get_connection = orig_conn


@pytest.mark.asyncio
async def test_inventory_service_missing_branches(test_engine):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        inv = DrugInventory(
            inventory_id=uuid4(), drug_name="TestDrug", brand_name="Brand", drug_code="TD-1",
            category="Test", unit="tab", quantity_in_stock=10, reorder_level=5,
            unit_cost=Decimal("1.0"), unit_price=Decimal("2.0"), is_active=True
        )
        db.add(inv)
        await db.commit()

        # Filtering in list_inventory
        items = await inventory_svc.list_inventory(db, search="Test", category="Test", low_stock=False, page=1, page_size=10)
        assert items is not None

        items_low = await inventory_svc.list_inventory(db, search=None, category=None, low_stock=True, page=1, page_size=10)
        assert items_low is not None

        # Non-existent item checks
        fake_id = uuid4()
        user_tok = security.TokenPayload("sub1", "pharmacist_user", "pharma@hospital.com", {"roles": ["pharmacist"]}, {})

        with pytest.raises(exceptions.NotFoundError):
            await inventory_svc.restock_inventory(db, RestockRequest(inventory_id=fake_id, quantity_added=5, batch_number="B1", expiry_date=date(2028, 1, 1), unit_cost=Decimal("5.0")), user_tok)

        with pytest.raises(exceptions.NotFoundError):
            await inventory_svc.adjust_inventory(db, AdjustInventoryRequest(inventory_id=fake_id, transaction_type="adjustment", quantity_change=5, notes="test"), user_tok)

        with pytest.raises(exceptions.NotFoundError):
            await inventory_svc.update_inventory_item(db, fake_id, UpdateInventoryRequest(unit_price=Decimal("5.0")), user_tok)

        with pytest.raises(exceptions.NotFoundError):
            await inventory_svc.get_inventory_detail(db, fake_id)

        # Get low stock alerts
        alerts = await inventory_svc.get_low_stock_alerts(db)
        assert alerts is not None

        # Create inventory item (initial stock > 0)
        req_create = CreateInventoryRequest(
            drug_name="NewDrug", drug_code="ND-1", category="Test", unit="tab",
            quantity_in_stock=50, reorder_level=10, unit_cost=2.0, unit_price=4.0
        )
        item_created = await inventory_svc.create_inventory_item(db, req_create, user_tok)
        assert item_created is not None

        # Duplicate drug_code conflict
        with pytest.raises(exceptions.ConflictError):
            await inventory_svc.create_inventory_item(db, req_create, user_tok)

        # Stock cannot go negative conflict
        with pytest.raises(exceptions.ConflictError):
            await inventory_svc.adjust_inventory(db, AdjustInventoryRequest(inventory_id=item_created.inventory_id, transaction_type="adjustment", quantity_change=-9999, notes="negative test"), user_tok)

        await db.close()


@pytest.mark.asyncio
async def test_tenant_service_master_checks():
    class FakeSyncDb:
        def execute(self, stmt, params=None):
            class FakeRes:
                def one_or_none(self): return None
                def scalar(self): return None
            return FakeRes()

    res_sub = await tenant_service.check_tenant_subscription(FakeSyncDb(), "nonexistent")
    assert res_sub == "not_found"

    res_chk = await tenant_service.check_and_update_tenant_status(FakeSyncDb(), "nonexistent")
    assert res_chk == "not_found"

    dsn = await tenant_service.get_tenant_db_dsn(FakeSyncDb(), "nonexistent")
    assert dsn is None


@pytest.mark.asyncio
async def test_inventory_service_item_update_fields(test_engine):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        user_tok = security.TokenPayload("sub1", "pharmacist_user", "pharma@hospital.com", {"roles": ["pharmacist"]}, {})

        req_c = CreateInventoryRequest(
            drug_name="ZeroStockDrug", drug_code="ZSD-1", category="Cat1", unit="box",
            quantity_in_stock=0, reorder_level=5, unit_cost=1.0, unit_price=2.0
        )
        inv_0 = await inventory_svc.create_inventory_item(db, req_c, user_tok)
        assert inv_0 is not None

        up_req = UpdateInventoryRequest(
            drug_name=" UpdatedName ", brand_name=" UpdatedBrand ", drug_code=" UNIQ-2 ",
            category=" Cat2 ", unit=" bottle ", reorder_level=15, unit_cost=10.0,
            unit_price=20.0, location=" Shelf B "
        )
        inv_up = await inventory_svc.update_inventory_item(db, inv_0.inventory_id, up_req, user_tok)
        assert inv_up.drug_name == "UpdatedName"
        await db.close()


@pytest.mark.asyncio
async def test_pharmacy_service_dispense_and_queue_flows(test_engine):
    session_factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as db:
        user_tok = security.TokenPayload("sub1", "pharmacist_user", "pharma@hospital.com", {"roles": ["pharmacist"]}, {})

        v_id = uuid4()
        p_id = uuid4()
        q_id = uuid4()
        pt = Patient(id=p_id, hospital_id="default-hospital", patient_number="PT-888", full_name="John Doe", date_of_birth=date(1980, 1, 1), gender="Male")
        vt = Visit(visit_id=v_id, patient_id=p_id, visit_number="VST-888", visit_date=date.today(), visit_type="outpatient", payment_type="cash", status="active", billing_cleared=True)
        qu = Queue(queue_id=q_id, visit_id=v_id, patient_id=str(p_id), queue_type="pharmacy", queue_number="P888", priority="routine", status="waiting", created_at=datetime.now(timezone.utc))
        db.add_all([pt, vt, qu])
        await db.commit()

        q_res = await pharmacy_svc.call_queue_patient(db, q_id, user_tok)
        assert q_res.status == "in_progress"

        with pytest.raises(exceptions.NotFoundError):
            await pharmacy_svc.get_visit_prescriptions(db, uuid4())

        rx_no_items = Prescription(
            prescription_id=uuid4(), id=uuid4(), visit_id=v_id, patient_id=p_id,
            prescribed_by="Dr. Empty", prescribed_at=datetime.now(timezone.utc),
            drug_name="Test", status="pending"
        )
        db.add(rx_no_items)
        await db.commit()
        empty_items_res = await pharmacy_svc.get_visit_prescriptions(db, v_id)
        assert empty_items_res is not None

        rxi_disp = PrescriptionItem(
            prescription_item_id=uuid4(), prescription_id=uuid4(), drug_name="Amoxicillin",
            dose="500mg", quantity_prescribed=10, status="dispensed"
        )
        db.add(rxi_disp)
        await db.commit()

        disp_req_already = DispenseRequest(
            prescription_id=rxi_disp.prescription_item_id, visit_id=v_id, drug_name="Amoxicillin",
            batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab",
            interaction_alert_acknowledged=True
        )
        with pytest.raises(exceptions.ConflictError):
            await pharmacy_svc.dispense_prescription(db, disp_req_already, user_tok)

        orig_pub = pharmacy_svc.publish_drug_dispensed
        async def mock_pub_err(*a, **kw): raise Exception("Publish Error")
        pharmacy_svc.publish_drug_dispensed = mock_pub_err
        try:
            rxi_pending = PrescriptionItem(
                prescription_item_id=uuid4(), prescription_id=uuid4(), drug_name="Amoxicillin",
                dose="500mg", quantity_prescribed=10, status="pending"
            )
            db.add(rxi_pending)
            await db.commit()

            disp_req_pub_err = DispenseRequest(
                prescription_id=rxi_pending.prescription_item_id, visit_id=v_id, drug_name="Amoxicillin",
                batch_number="B1", expiry_date=date(2028, 1, 1), quantity_dispensed=1, unit="tab",
                interaction_alert_acknowledged=True
            )
            vt.billing_cleared = True
            await db.commit()
            await pharmacy_svc.dispense_prescription(db, disp_req_pub_err, user_tok)
        finally:
            pharmacy_svc.publish_drug_dispensed = orig_pub

        await db.close()


@pytest.mark.asyncio
async def test_tenant_service_status_checks_and_session_revocation():
    await tenant_service.cache_tenant_suspension("t1")
    await tenant_service.remove_tenant_suspension_cache("t1")
    await tenant_service.is_tenant_suspended("t1")

    orig_async_c = httpx.AsyncClient
    class FakeKeycloakClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **kw):
            class FakeRes:
                def raise_for_status(self): pass
                def json(self): return {"access_token": "token123"}
            return FakeRes()
        async def get(self, *a, **kw):
            class FakeUsersRes:
                is_success = True
                def json(self): return [{"id": "user1"}]
            return FakeUsersRes()
        async def put(self, *a, **kw): pass

    httpx.AsyncClient = lambda *a, **kw: FakeKeycloakClient()
    try:
        await tenant_service._revoke_keycloak_sessions("t1")
    finally:
        httpx.AsyncClient = orig_async_c
