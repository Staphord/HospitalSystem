"""Unit and integration edge cases test suite for ward-service domain logic and security handlers."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException, Request, Response
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

@compiles(JSONB, "sqlite")
def compile_jsonb_sqlite(type_, compiler, **kw):
    return "JSON"

from app.api.v1 import schemas
from app.api.v1.router import router
from app.core import config, database, limiter, middleware, security, tenant_auth
from app.db import base, master, session as db_session_mod, tenant as db_tenant_mod
from app.dependencies import get_tenant_db_for_request
from app.events import publisher as ev_publisher
from app.exceptions import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    MFARequiredError,
    NotFoundError,
    RateLimitError,
    ReadOnlyScopeError,
    TenantNotFoundError,
    TenantSuspendedError,
    TokenExpiredError,
    UnauthorizedError,
)
from app.main import app
from app.messaging import connection, publisher as msg_publisher, subscriber as msg_subscriber
from app.models import master as master_models
from app.models.ward import (
    Admission,
    Bed,
    Consultation,
    InpatientOrder,
    NursingNote,
    ShiftHandover,
    Visit,
    VisitorLog,
)
from app.services import tenant_service, ward as ward_svc

TEST_CLINICAL_USER = security.TokenPayload(
    sub="test-user-sub",
    preferred_username="test.user",
    email="test@hospital.com",
    realm_access={"roles": ["nurse", "doctor", "clinician", "hospital_admin"]},
    raw={"type": "superadmin", "role": "super_admin"},
)

TEST_TENANT_CTX = tenant_auth.TenantContext(
    tenant_id="default-hospital",
    user_sub="test-user-sub",
    preferred_username="test.user",
    email="test@hospital.com",
    roles=["nurse", "doctor", "clinician", "hospital_admin"],
    is_super_admin=True,
)


@pytest.fixture
async def ward_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)

    @event.listens_for(engine.sync_engine, "connect")
    def add_sqlite_functions(dbapi_conn, record):
        dbapi_conn.create_function("now", 0, lambda: datetime.now(timezone.utc).isoformat())

    async with engine.begin() as conn:
        await conn.run_sync(base.Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.close()
    await engine.dispose()


@pytest.fixture
def ward_client(ward_db):
    async def override_db():
        yield ward_db

    async def override_tenant():
        return TEST_TENANT_CTX

    async def override_user():
        return TEST_CLINICAL_USER

    app.dependency_overrides[tenant_auth.get_current_tenant] = override_tenant
    app.dependency_overrides[security.get_current_active_user] = override_user
    app.dependency_overrides[get_tenant_db_for_request] = override_db
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_compute_los_days_timezone_handling():
    naive_start = datetime(2026, 1, 1, 10, 0, 0)
    naive_end = datetime(2026, 1, 2, 10, 0, 0)
    los = ward_svc.compute_los_days(naive_start, naive_end)
    assert los == Decimal("1.0")


@pytest.mark.asyncio
async def test_bed_assignment_and_transfer_flows(ward_db):
    # List beds empty
    beds = await ward_svc.list_beds(ward_db)
    assert beds == []

    # Create beds
    b1 = Bed(bed_id=uuid4(), ward_name="General", bed_number="G-101", bed_type="standard", is_available=True, is_active=True)
    b2 = Bed(bed_id=uuid4(), ward_name="ICU", bed_number="ICU-1", bed_type="icu", is_available=False, is_active=True)
    b3 = Bed(bed_id=uuid4(), ward_name="General", bed_number="G-102", bed_type="standard", is_available=True, is_active=False)
    ward_db.add_all([b1, b2, b3])
    await ward_db.commit()

    # List beds with filters
    res_filt = await ward_svc.list_beds(ward_db, ward_name="General", bed_type="standard", is_available=True, is_active=True)
    assert len(res_filt) == 1

    # Beds board
    board = await ward_svc.beds_board(ward_db)
    assert "wards" in board

    # Lock bed inactive error
    with pytest.raises(HTTPException) as exc:
        await ward_svc._lock_bed(ward_db, b3.bed_id)
    assert exc.value.status_code == 404

    # Lock non-existent bed
    with pytest.raises(HTTPException) as exc:
        await ward_svc._lock_bed(ward_db, uuid4())
    assert exc.value.status_code == 404

    # Assign occupied bed error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.assign_bed(ward_db, b2.bed_id)
    assert exc.value.status_code == 409

    # Release bed
    released = await ward_svc.release_bed(ward_db, b2.bed_id)
    assert released.is_available is True


@pytest.mark.asyncio
async def test_admission_lifecycle_and_transfers(ward_db):
    b1 = Bed(bed_id=uuid4(), ward_name="Male Ward", bed_number="M-1", bed_type="standard", is_available=True, is_active=True)
    b2 = Bed(bed_id=uuid4(), ward_name="Male Ward", bed_number="M-2", bed_type="standard", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    cs = Consultation(id=uuid4(), visit_id=v_id, patient_id=p_id, disposition="admission")
    ward_db.add_all([b1, b2, vt, cs])
    await ward_db.commit()

    # Create admission
    adm = await ward_svc.create_admission(
        ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="Malaria", doctor_sub="doc-123", tenant_id="t1"
    )
    assert adm.status == "active"

    # Create duplicate admission error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_admission(
            ward_db, visit_id=v_id, bed_id=b2.bed_id, admitting_diagnosis="Malaria", doctor_sub="doc-123", tenant_id="t1"
        )
    assert exc.value.status_code == 409

    # Transfer bed via assign_bed
    adm_assigned = await ward_svc.assign_bed(ward_db, b2.bed_id, admission_id=adm.admission_id)
    assert adm_assigned.bed_id == b2.bed_id

    # Assign bed with non-existent admission_id
    with pytest.raises(HTTPException) as exc:
        await ward_svc.assign_bed(ward_db, b1.bed_id, admission_id=uuid4())
    assert exc.value.status_code == 404

    # Update condition
    updated_adm = await ward_svc.update_admission_condition(ward_db, adm.admission_id, condition="monitoring", actor_sub="doc-123")
    assert updated_adm.condition == "monitoring"

    # List admissions
    admissions = await ward_svc.list_admissions(ward_db, status_filter="active", patient_id=p_id, ward_name="Male Ward")
    assert len(admissions) == 1

    # Get LOS
    los = await ward_svc.get_los(ward_db, adm.admission_id)
    assert "length_of_stay_days" in los

    # Discharge admission
    discharged = await ward_svc.discharge_admission(
        ward_db, adm.admission_id, discharge_diagnosis="Recovered", discharge_instructions="Take meds", doctor_sub="doc-123"
    )
    assert discharged.status == "discharged"

    # Already discharged error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.discharge_admission(
            ward_db, adm.admission_id, discharge_diagnosis="Recovered", discharge_instructions="Take meds", doctor_sub="doc-123"
        )
    assert exc.value.status_code == 400

    # Get LOS after discharge
    los_discharged = await ward_svc.get_los(ward_db, adm.admission_id)
    assert los_discharged["status"] == "discharged"


@pytest.mark.asyncio
async def test_admission_disposition_validation(ward_db):
    b1 = Bed(bed_id=uuid4(), ward_name="Female Ward", bed_number="F-1", bed_type="standard", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    cs = Consultation(id=uuid4(), visit_id=v_id, patient_id=p_id, disposition="discharge")
    ward_db.add_all([b1, vt, cs])
    await ward_db.commit()

    # Wrong disposition error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_admission(
            ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="Fever", doctor_sub="doc-123", tenant_id="t1", require_disposition=True
        )
    assert exc.value.status_code == 400

    # Non-existent visit error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_admission(
            ward_db, visit_id=uuid4(), bed_id=b1.bed_id, admitting_diagnosis="Fever", doctor_sub="doc-123", tenant_id="t1"
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_inpatient_orders_and_nursing_notes_lifecycle(ward_db):
    b1 = Bed(bed_id=uuid4(), ward_name="Pediatrics", bed_number="P-1", bed_type="standard", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    ward_db.add_all([b1, vt])
    await ward_db.commit()

    adm = await ward_svc.create_admission(
        ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="Cough", doctor_sub="doc-123", tenant_id="t1", require_disposition=False
    )

    # Invalid order_type
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_order(ward_db, adm.admission_id, {"order_type": "invalid_type", "order_detail": "test"}, "doc-123")
    assert exc.value.status_code == 400

    # Create order
    ord_row = await ward_svc.create_order(ward_db, adm.admission_id, {"order_type": "medication", "order_detail": "Amoxicillin 500mg"}, "doc-123")
    assert ord_row.order_type == "medication"

    # List orders
    orders = await ward_svc.list_orders(ward_db, adm.admission_id)
    assert len(orders) == 1

    # Update order invalid status
    with pytest.raises(HTTPException) as exc:
        await ward_svc.update_order(ward_db, adm.admission_id, ord_row.order_id, {"status": "bogus"})
    assert exc.value.status_code == 400

    # Update order valid
    ord_up = await ward_svc.update_order(ward_db, adm.admission_id, ord_row.order_id, {"status": "completed", "order_detail": "Updated detail"})
    assert ord_up.status == "completed"

    # Update non-existent order
    with pytest.raises(HTTPException) as exc:
        await ward_svc.update_order(ward_db, adm.admission_id, uuid4(), {"status": "completed"})
    assert exc.value.status_code == 404

    # Invalid note_type
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_nursing_note(ward_db, adm.admission_id, {"note_type": "bogus", "note_text": "text"}, "nurse-1")
    assert exc.value.status_code == 400

    # Create nursing note
    note = await ward_svc.create_nursing_note(ward_db, adm.admission_id, {"note_type": "observation", "note_text": "Patient resting"}, "nurse-1")
    assert note.note_type == "observation"

    # List nursing notes
    notes = await ward_svc.list_nursing_notes(ward_db, adm.admission_id)
    assert len(notes) == 1


@pytest.mark.asyncio
async def test_visitor_management_and_overstay_refresh(ward_db):
    b1 = Bed(bed_id=uuid4(), ward_name="Surgical", bed_number="S-1", bed_type="standard", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    ward_db.add_all([b1, vt])
    await ward_db.commit()

    adm = await ward_svc.create_admission(
        ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="Appendicitis", doctor_sub="doc-123", tenant_id="t1", require_disposition=False
    )

    # Create visitor approved
    vis_data = {
        "admission_id": adm.admission_id,
        "patient_name": "Jane Doe",
        "bed_label": "S-1",
        "visitor_name": "Mark Visitor",
        "relationship": "Brother",
        "approved": True,
        "allowed_duration_minutes": 1,
    }
    vis = await ward_svc.create_visitor(ward_db, vis_data, approved_by="nurse-1")
    assert vis.status == "active"

    # Create visitor denied
    vis_denied_data = {
        "patient_name": "Jane Doe",
        "bed_label": "S-1",
        "visitor_name": "Denied Visitor",
        "relationship": "Friend",
        "approved": False,
        "denial_reason": "Visiting hours ended",
    }
    vis_denied = await ward_svc.create_visitor(ward_db, vis_denied_data, approved_by="nurse-1")
    assert vis_denied.status == "denied"

    # Checkout denied visitor error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.checkout_visitor(ward_db, vis_denied.visitor_id)
    assert exc.value.status_code == 400

    # Checkout non-existent visitor
    with pytest.raises(HTTPException) as exc:
        await ward_svc.checkout_visitor(ward_db, uuid4())
    assert exc.value.status_code == 404

    # List visitors
    visitors = await ward_svc.list_visitors(ward_db, active_only=True)
    assert len(visitors) >= 1

    # Checkout visitor
    vis_out = await ward_svc.checkout_visitor(ward_db, vis.visitor_id)
    assert vis_out.status == "departed"

    # Checkout already departed returns row
    vis_out_again = await ward_svc.checkout_visitor(ward_db, vis.visitor_id)
    assert vis_out_again.status == "departed"


@pytest.mark.asyncio
async def test_shift_handover_creation_and_listing(ward_db):
    h_data = {
        "shift_label": "Morning Shift",
        "overall_summary": "Quiet shift",
        "incidents_summary": "None",
        "patient_notes": {"pt1": "Stable", "pt2": "Monitoring"},
        "ward_name": "General",
    }
    ho = await ward_svc.create_handover(ward_db, h_data, submitted_by="nurse-1")
    assert ho.patient_count == 2

    handovers = await ward_svc.list_handovers(ward_db)
    assert len(handovers) == 1


def test_ward_router_endpoint_handlers(ward_client, ward_db):
    # Pre-populate DB objects for test client session
    b1 = Bed(bed_id=uuid4(), ward_name="Male Ward", bed_number="MW-1", bed_type="standard", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    cs = Consultation(id=uuid4(), visit_id=v_id, patient_id=p_id, disposition="admission")
    
    async def seed():
        ward_db.add_all([b1, vt, cs])
        await ward_db.commit()

    asyncio.run(seed())

    # Beds endpoints
    r_beds = ward_client.get("/api/v1/ward/beds")
    assert r_beds.status_code == 200

    r_board = ward_client.get("/api/v1/ward/beds/board")
    assert r_board.status_code == 200

    r_assign = ward_client.post(f"/api/v1/ward/beds/{b1.bed_id}/assign")
    assert r_assign.status_code == 200

    r_release = ward_client.post(f"/api/v1/ward/beds/{b1.bed_id}/release")
    assert r_release.status_code == 200

    # Admissions endpoints
    r_adm_create = ward_client.post("/api/v1/ward/admissions", json={
        "visit_id": str(v_id),
        "bed_id": str(b1.bed_id),
        "admitting_diagnosis": "Malaria"
    })
    assert r_adm_create.status_code == 201
    adm_id = r_adm_create.json()["admission_id"]

    r_adms = ward_client.get("/api/v1/ward/admissions")
    assert r_adms.status_code == 200

    r_adm_get = ward_client.get(f"/api/v1/ward/admissions/{adm_id}")
    assert r_adm_get.status_code == 200

    r_cond = ward_client.patch(f"/api/v1/ward/admissions/{adm_id}/condition", json={"condition": "critical"})
    assert r_cond.status_code == 200

    r_los = ward_client.get(f"/api/v1/ward/admissions/{adm_id}/los")
    assert r_los.status_code == 200

    # Inpatient orders
    r_order_create = ward_client.post(f"/api/v1/ward/admissions/{adm_id}/orders", json={
        "order_type": "medication",
        "order_detail": "IV Paracetamol"
    })
    assert r_order_create.status_code == 201
    order_id = r_order_create.json()["order_id"]

    r_orders = ward_client.get(f"/api/v1/ward/admissions/{adm_id}/orders")
    assert r_orders.status_code == 200

    r_order_up = ward_client.patch(f"/api/v1/ward/admissions/{adm_id}/orders/{order_id}", json={
        "status": "completed"
    })
    assert r_order_up.status_code == 200

    # Nursing notes
    r_note_create = ward_client.post(f"/api/v1/ward/admissions/{adm_id}/nursing-notes", json={
        "note_type": "observation",
        "note_text": "Patient responsive"
    })
    assert r_note_create.status_code == 201

    r_notes = ward_client.get(f"/api/v1/ward/admissions/{adm_id}/nursing-notes")
    assert r_notes.status_code == 200

    # Visitors
    r_vis_create = ward_client.post("/api/v1/ward/visitors", json={
        "admission_id": adm_id,
        "patient_name": "John Doe",
        "bed_label": "MW-1",
        "visitor_name": "Jane Visitor",
        "relationship": "Sister",
        "approved": True
    })
    assert r_vis_create.status_code == 201
    vis_id = r_vis_create.json()["visitor_id"]

    r_visitors = ward_client.get("/api/v1/ward/visitors")
    assert r_visitors.status_code == 200

    r_act_visitors = ward_client.get("/api/v1/ward/visitors/active")
    assert r_act_visitors.status_code == 200

    r_vis_out = ward_client.post(f"/api/v1/ward/visitors/{vis_id}/checkout")
    assert r_vis_out.status_code == 200

    # Discharge admission
    r_disch = ward_client.post(f"/api/v1/ward/admissions/{adm_id}/discharge", json={
        "discharge_diagnosis": "Recovered",
        "discharge_instructions": "Rest at home"
    })
    assert r_disch.status_code == 200

    # Handovers
    r_ho_create = ward_client.post("/api/v1/ward/handovers", json={
        "shift_label": "Night Shift",
        "overall_summary": "All quiet"
    })
    assert r_ho_create.status_code == 201

    r_handovers = ward_client.get("/api/v1/ward/handovers")
    assert r_handovers.status_code == 200


@pytest.mark.asyncio
async def test_tenant_service_subscription_status_and_dsn():
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:")
    base.Base.metadata.create_all(eng)
    master_models.Base.metadata.create_all(eng)

    class FakeSyncDb:
        bind = eng
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
    assert dsn is not None

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


@pytest.mark.asyncio
async def test_security_token_and_auth_middleware_handlers():
    # HS256 token without kid
    hs_token = jwt.encode({"sub": "user1", "iss": f"{config.settings.keycloak_url}/realms/hospital"}, config.settings.secret_key, algorithm="HS256")
    decoded = await security._decode_token(hs_token)
    assert decoded["sub"] == "user1"

    # Issuer and extract realm
    assert security._issuer("realm1") == f"{config.settings.keycloak_url}/realms/realm1"
    assert security._extract_realm_from_iss(hs_token) == "hospital"

    # Build RSA key
    jwks = {"keys": [{"kid": "k1", "n": "abc"}]}
    rsa_k = security._build_rsa_key(jwks, "k1")
    assert rsa_k["kid"] == "k1"

    # Lock missing kid RSA key error
    with pytest.raises(HTTPException) as exc:
        security._build_rsa_key(jwks, "missing")
    assert exc.value.status_code == 401

    # Tenant Auth functions
    ctx = tenant_auth.TenantContext(
        tenant_id="t1", user_sub="sub1", preferred_username="u1",
        email="e1@h.com", roles=["doctor"], is_super_admin=False
    )
    assert ctx.tenant_id == "t1"

    # Middleware execution
    async def call_next(req):
        return Response(content="OK", status_code=200)

    req_options = Request({"type": "http", "method": "OPTIONS", "path": "/health", "headers": []})
    audit_mw = middleware.AuditLogMiddleware(app)
    resp_opt = await audit_mw.dispatch(req_options, call_next)
    assert resp_opt.status_code == 200

    req_post = Request({"type": "http", "method": "POST", "path": "/api/v1/ward/beds", "headers": []})
    req_post.state.tenant = tenant_auth.TenantContext(
        tenant_id="t1", user_sub="s1", preferred_username="u1", email="e1", roles=["doctor"], is_super_admin=False, scope="readonly"
    )
    ro_mw = middleware.ReadOnlyScopeMiddleware(app)
    resp_ro = await ro_mw.dispatch(req_post, call_next)
    assert resp_ro.status_code == 403

    banner_mw = middleware.ImpersonationBannerMiddleware(app)
    resp_banner = await banner_mw.dispatch(req_post, call_next)
    assert resp_banner.headers.get("X-Impersonation-Banner") == "true"

    # Exceptions
    assert UnauthorizedError("auth err").status_code == 401
    assert ForbiddenError("authz err").status_code == 403
    assert NotFoundError("nf err").status_code == 404
    assert ConflictError("conf err").status_code == 409
    assert BadRequestError("br err").status_code == 400
    assert RateLimitError("rl err").status_code == 429
    assert TenantSuspendedError("ts err").status_code == 403
    assert TenantNotFoundError().status_code == 404
    assert TokenExpiredError().status_code == 401
    assert MFARequiredError().status_code == 401
    assert ReadOnlyScopeError().status_code == 403


@pytest.mark.asyncio
async def test_events_and_messaging_publishers():
    # Events publisher
    await ev_publisher.publish_patient_admitted("adm-123", "p-123", "t1", "b-123")
    await ev_publisher.publish_patient_discharged("adm-123", "p-123", "t1", datetime.now(timezone.utc), 1.5, "v-123")

    # Messaging connection & publishers
    class FakeConn:
        async def channel(self):
            class FakeCh:
                async def declare_exchange(self, *a, **kw): pass
                async def declare_queue(self, *a, **kw):
                    class FakeQ:
                        async def bind(self, *a, **kw): pass
                        async def consume(self, callback, *a, **kw): pass
                    return FakeQ()
                async def publish(self, *a, **kw): pass
            return FakeCh()
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
async def test_tenant_auth_and_messaging_subscribers():
    # Tenant context from header
    tok_payload = {
        "sub": "user1",
        "preferred_username": "john",
        "email": "john@h.com",
        "tenant_id": "tenant-99",
        "realm_access": {"roles": ["doctor"]},
        "iss": f"{config.settings.keycloak_url}/realms/hospital",
    }
    encoded_tok = jwt.encode(tok_payload, config.settings.secret_key, algorithm="HS256")
    
    req_auth = Request({"type": "http", "method": "GET", "path": "/api/v1/ward/beds", "headers": [(b"authorization", f"Bearer {encoded_tok}".encode())]})
    orig_decode = tenant_auth._decode_token
    async def mock_decode(t): return tok_payload
    tenant_auth._decode_token = mock_decode
    try:
        ctx = await tenant_auth.get_current_tenant(
            request=req_auth,
            credentials=security.HTTPAuthorizationCredentials(scheme="Bearer", credentials=encoded_tok),
        )
        assert ctx.user_sub == "user1"
    finally:
        tenant_auth._decode_token = orig_decode


@pytest.mark.asyncio
async def test_ward_service_admission_and_visitor_edge_cases(ward_db):
    # compute_los_days end naive
    start_tz = datetime(2026, 1, 1, 10, 0, 0, tzinfo=timezone.utc)
    end_naive = datetime(2026, 1, 2, 10, 0, 0)
    los = ward_svc.compute_los_days(start_tz, end_naive)
    assert los == Decimal("1.0")

    # _visitor_time_left with check_in tzinfo
    v_log = VisitorLog(
        visitor_id=uuid4(), patient_name="P", bed_label="B", visitor_name="V",
        relationship="R", check_in_at=datetime.now(timezone.utc) - timedelta(minutes=45),
        approved_by="nurse", status="active", allowed_duration_minutes=30
    )
    time_left = ward_svc._visitor_time_left(v_log)
    assert time_left <= 0

    # list_visitors triggers _refresh_overstays (updating active overstayed to overstay)
    ward_db.add(v_log)
    await ward_db.commit()
    vis_list = await ward_svc.list_visitors(ward_db)
    assert any(v.status == "overstay" for v in vis_list)

    # Beds setup
    b1 = Bed(bed_id=uuid4(), ward_name="W1", bed_number="1", is_available=True, is_active=True)
    b2 = Bed(bed_id=uuid4(), ward_name="W1", bed_number="2", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    ward_db.add_all([b1, b2, vt])
    await ward_db.commit()

    adm = await ward_svc.create_admission(ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="x", doctor_sub="doc", tenant_id="t1", require_disposition=False)

    # assign_bed transfer bed logic
    adm_transferred = await ward_svc.assign_bed(ward_db, b2.bed_id, admission_id=adm.admission_id)
    assert adm_transferred.bed_id == b2.bed_id

    # Discharge admission
    await ward_svc.discharge_admission(ward_db, adm.admission_id, discharge_diagnosis="d", discharge_instructions="i", doctor_sub="doc")

    # Discharged admission assign_bed error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.assign_bed(ward_db, b1.bed_id, admission_id=adm.admission_id)
    assert exc.value.status_code == 400

    # Discharged admission update_admission_condition error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.update_admission_condition(ward_db, adm.admission_id, condition="stable", actor_sub="doc")
    assert exc.value.status_code == 400

    # Discharged admission create_order error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_order(ward_db, adm.admission_id, {"order_type": "medication", "order_detail": "d"}, "doc")
    assert exc.value.status_code == 400

    # Discharged admission create_nursing_note error
    with pytest.raises(HTTPException) as exc:
        await ward_svc.create_nursing_note(ward_db, adm.admission_id, {"note_type": "observation", "note_text": "t"}, "nurse")
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_tenant_service_subscription_status_flow():
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:")
    master_models.Base.metadata.create_all(eng)

    # Active status
    class FakeDbActive:
        bind = eng
        def execute(self, stmt, params=None):
            class FakeRes:
                def one_or_none(self):
                    sql = str(stmt)
                    if "SELECT status, subscription_end FROM" in sql:
                        return ("active", datetime.now(timezone.utc) + timedelta(days=10))
                    return ("active", datetime.now(timezone.utc) + timedelta(days=10), 1)
                def scalar(self): return tenant_service.encrypt_dsn("sqlite:///:memory:")
            return FakeRes()
        def commit(self): pass

    assert await tenant_service.check_tenant_subscription(FakeDbActive(), "t1") == "active"
    assert await tenant_service.check_and_update_tenant_status(FakeDbActive(), "t1") == "active"

    # Expired status
    class FakeDbExpired:
        bind = eng
        def execute(self, stmt, params=None):
            class FakeRes:
                def one_or_none(self):
                    sql = str(stmt)
                    if "SELECT status, subscription_end FROM" in sql:
                        return ("active", datetime.now(timezone.utc) - timedelta(days=10))
                    return ("active", datetime.now(timezone.utc) - timedelta(days=10), 1)
                def scalar(self): return tenant_service.encrypt_dsn("sqlite:///:memory:")
            return FakeRes()
        def commit(self): pass

    assert await tenant_service.check_tenant_subscription(FakeDbExpired(), "t1") == "expired"
    assert await tenant_service.check_and_update_tenant_status(FakeDbExpired(), "t1") == "expired"

    # Suspended status
    class FakeDbSuspended:
        bind = eng
        def execute(self, stmt, params=None):
            class FakeRes:
                def one_or_none(self):
                    sql = str(stmt)
                    if "SELECT status, subscription_end FROM" in sql:
                        return ("suspended", datetime.now(timezone.utc) + timedelta(days=10))
                    return ("suspended", datetime.now(timezone.utc) + timedelta(days=10), 1)
                def scalar(self): return tenant_service.encrypt_dsn("sqlite:///:memory:")
            return FakeRes()
        def commit(self): pass

    assert await tenant_service.check_tenant_subscription(FakeDbSuspended(), "t1") == "suspended"
    assert await tenant_service.check_and_update_tenant_status(FakeDbSuspended(), "t1") == "suspended"


@pytest.mark.asyncio
async def test_security_jwks_and_rsa_key_branches():
    # JWKS cache test
    security._jwks_cache["jwks:hospital"] = {"keys": [{"kid": "test-kid", "n": "123"}]}
    jwks = await security._fetch_jwks("hospital")
    assert jwks["keys"][0]["kid"] == "test-kid"

    # RSA Key match
    rsa_key = security._build_rsa_key(jwks, "test-kid")
    assert rsa_key["kid"] == "test-kid"

    # Missing RSA key raises 401
    with pytest.raises(HTTPException) as exc:
        security._build_rsa_key(jwks, "non-existent")
    assert exc.value.status_code == 401

    # Token Introspection cache test
    security._introspection_cache["bad-token"] = False
    with pytest.raises(HTTPException) as exc:
        await security._introspect_token("bad-token")
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_main_health_and_lifespan_handlers(ward_client):
    r_health = ward_client.get("/health")
    assert r_health.status_code == 200


@pytest.mark.asyncio
async def test_tenant_service_extended_coverage():
    from sqlalchemy import create_engine
    eng = create_engine("sqlite:///:memory:")
    master_models.Base.metadata.create_all(eng)

    class MockSession:
        bind = eng
        def execute(self, stmt, params=None):
            class Res:
                def one_or_none(self): return None
                def scalar(self): return None
            return Res()
        def commit(self): pass

    # check_tenant_subscription missing tenant
    sub = await tenant_service.check_tenant_subscription(MockSession(), "missing")
    assert sub == "not_found"

    # check_and_update_tenant_status missing tenant
    st = await tenant_service.check_and_update_tenant_status(MockSession(), "missing")
    assert st == "not_found"

    # DSN encryption/decryption
    encrypted = tenant_service.encrypt_dsn("postgresql+asyncpg://usr:pwd@localhost/db")
    decrypted = tenant_service.decrypt_dsn(encrypted)
    assert decrypted == "postgresql+asyncpg://usr:pwd@localhost/db"


@pytest.mark.asyncio
async def test_security_user_dependencies_and_introspect():
    # User dependencies via get_current_active_user with mocked decode
    orig_dec = security._decode_token
    async def mock_dec(t):
        return {
            "sub": "test-user-sub",
            "preferred_username": "user",
            "email": "user@h.com",
            "realm_access": {"roles": ["doctor"]},
        }
    security._decode_token = mock_dec
    try:
        req = Request({"type": "http", "method": "GET", "path": "/api/v1/ward/beds", "headers": []})
        creds = security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
        active_user = await security.get_current_active_user(request=req, credentials=creds)
        assert active_user.sub == "test-user-sub"
    finally:
        security._decode_token = orig_dec

    # Require role dependency success
    req_doctor = security.require_role("doctor")
    doc_user = await req_doctor(user=TEST_CLINICAL_USER)
    assert doc_user.sub == "test-user-sub"

    # Require role dependency failure
    req_admin = security.require_role("non_existent_role")
    non_doc_user = security.TokenPayload(
        sub="u2", preferred_username="u2", email="u2@h.com", realm_access={"roles": ["nurse"]}, raw={"type": "user"}
    )
    with pytest.raises(HTTPException) as exc:
        await req_admin(user=non_doc_user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_messaging_consumer_loop_execution():
    # Test start_consumer loop with fake connection and iterator
    class FakeProc:
        async def __aenter__(self): pass
        async def __aexit__(self, *a, **kw): pass

    class FakeMsg:
        body = b'{"event": "test"}'
        routing_key = "ward.admitted"
        def process(self):
            return FakeProc()

    class FakeQueueIter:
        async def __aenter__(self): return self
        async def __aexit__(self, *a, **kw): pass
        def __aiter__(self):
            self.yielded = False
            return self
        async def __anext__(self):
            if not self.yielded:
                self.yielded = True
                return FakeMsg()
            raise StopAsyncIteration

    class FakeQueue:
        name = "test_queue"
        async def bind(self, *a, **kw): pass
        def iterator(self): return FakeQueueIter()

    class FakeCh:
        async def set_qos(self, *a, **kw): pass
        async def declare_exchange(self, *a, **kw): pass
        async def declare_queue(self, *a, **kw): return FakeQueue()

    class FakeConn:
        async def channel(self): return FakeCh()

    orig_subscriber_conn = msg_subscriber.get_connection
    async def mock_get_conn(): return FakeConn()
    msg_subscriber.get_connection = mock_get_conn

    handled = []
    async def sample_h(key, payload):
        handled.append((key, payload))

    try:
        await msg_subscriber.start_consumer("ward_service", ["ward.admitted"], sample_h)
        assert len(handled) == 1
    finally:
        msg_subscriber.get_connection = orig_subscriber_conn


@pytest.mark.asyncio
async def test_ward_service_exception_rollback_branches(ward_db):
    b1 = Bed(bed_id=uuid4(), ward_name="W1", bed_number="101", is_available=True, is_active=True)
    v_id = uuid4()
    p_id = uuid4()
    vt = Visit(visit_id=v_id, patient_id=p_id, visit_type="outpatient", status="active")
    ward_db.add_all([b1, vt])
    await ward_db.commit()

    # Mock execute error during visit status update in create_admission
    orig_exec = ward_db.execute
    async def mock_exec(stmt, params=None):
        if "UPDATE visits SET status = 'admitted'" in str(stmt):
            raise Exception("DB Visit Status Update Error")
        return await orig_exec(stmt, params)

    ward_db.execute = mock_exec
    try:
        adm = await ward_svc.create_admission(
            ward_db, visit_id=v_id, bed_id=b1.bed_id, admitting_diagnosis="fever", doctor_sub="doc", tenant_id="t1", require_disposition=False
        )
        assert adm is not None
    finally:
        ward_db.execute = orig_exec
