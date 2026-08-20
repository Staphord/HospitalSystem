"""Tests for reception-service API routers, database layers,
infrastructure utilities, and all remaining service branches.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# App-level TestClient setup — overrides all authentication dependencies
# ---------------------------------------------------------------------------

from app.main import app
from app.core.tenant_auth import TenantContext, get_current_tenant
from app.core.security import TokenPayload, get_current_active_user


def _tenant_context(scope: str = "full") -> TenantContext:
    return TenantContext(
        tenant_id="test-tenant",
        user_sub="test-user-sub",
        preferred_username="testuser",
        email="test@hospital.com",
        roles=["receptionist", "hospital_admin"],
        is_super_admin=False,
        scope=scope,
    )


def _token_payload() -> TokenPayload:
    return TokenPayload(
        sub="test-user-sub",
        preferred_username="testuser",
        email="test@hospital.com",
        realm_access={"roles": ["receptionist"]},
        raw={},
    )


def _apply_auth_overrides(scope: str = "full"):
    async def _tenant():
        return _tenant_context(scope=scope)

    async def _user():
        return _token_payload()

    app.dependency_overrides[get_current_tenant] = _tenant
    app.dependency_overrides[get_current_active_user] = _user


def _clear_auth_overrides():
    app.dependency_overrides.clear()


# Helper fixtures for valid schemas
def _valid_patient_fixture(pid=None):
    return {
        "id": str(pid or uuid4()),
        "patient_number": "PAT-0001",
        "full_name": "Alice Mwangi",
        "date_of_birth": "1990-05-15",
        "gender": "female",
        "phone_primary": "0712345678",
        "created_at": "2026-01-01T08:00:00Z",
    }


def _valid_insurance_fixture(pid=None, iid=None):
    return {
        "insurance_id": str(iid or uuid4()),
        "patient_id": str(pid or uuid4()),
        "insurer_name": "AAR",
        "policy_number": "AAR-001",
        "verification_status": "pending",
        "is_active": True,
        "created_at": "2026-01-01T08:00:00Z",
    }


def _patient_summary_fixture(pid=None):
    return {
        "patient_id": str(pid or uuid4()),
        "patient_number": "PAT-0001",
        "full_name": "Alice Mwangi",
    }


def _visit_summary_fixture(vid=None):
    return {
        "visit_id": str(vid or uuid4()),
        "visit_number": "VIS-001",
        "queue_number": "T-001",
        "visit_type": "outpatient",
        "payment_type": "cash",
        "status": "registered",
    }


def _visit_response_fixture(vid=None, pid=None):
    return {
        "visit_id": str(vid or uuid4()),
        "patient_id": str(pid or uuid4()),
        "visit_number": "VIS-001",
        "visit_date": "2026-01-01",
        "visit_type": "outpatient",
        "payment_type": "cash",
        "insurance_id": None,
        "verification_flag": None,
        "queue_number": "T-001",
        "status": "registered",
        "registered_by": str(uuid4()),
        "created_at": "2026-01-01T08:00:00Z",
        "updated_at": "2026-01-01T08:00:00Z",
    }


def _queue_summary_fixture(qid=None, vid=None, pid=None):
    return {
        "queue_id": str(qid or uuid4()),
        "visit_id": str(vid or uuid4()),
        "patient_id": str(pid or uuid4()),
        "queue_type": "triage",
        "queue_number": "T-001",
        "priority": "normal",
        "status": "waiting",
        "created_at": "2026-01-01T08:00:00Z",
    }


# ---------------------------------------------------------------------------
# Patients API router tests
# ---------------------------------------------------------------------------

class TestPatientRouter:
    def setup_method(self):
        _apply_auth_overrides()

    def teardown_method(self):
        _clear_auth_overrides()

    def test_register_new_patient(self):
        payload = {
            "full_name": "Alice Mwangi",
            "date_of_birth": "1990-05-15",
            "gender": "female",
            "phone_primary": "0712345678",
        }
        patient_response = _valid_patient_fixture()
        with patch("app.api.v1.patients.router.register_patient", new=AsyncMock(return_value=patient_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/v1/reception/patients",
                    json=payload,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 201

    def test_register_patient_legacy_alias(self):
        payload = {
            "full_name": "Bob Omondi",
            "date_of_birth": "1985-03-10",
            "gender": "male",
            "phone_primary": "0722334455",
        }
        patient_response = _valid_patient_fixture()
        with patch("app.api.v1.patients.router.register_patient", new=AsyncMock(return_value=patient_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/v1/reception/patients/register",
                    json=payload,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 201

    def test_list_patients(self):
        search_response = {
            "patients": [_valid_patient_fixture()],
            "total": 1,
            "page": 1,
            "page_size": 20,
        }
        with patch("app.api.v1.patients.router.search_patients", new=AsyncMock(return_value=search_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/api/v1/reception/patients?search=Alice",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_list_patients_search_legacy_alias(self):
        search_response = {
            "patients": [],
            "total": 0,
            "page": 1,
            "page_size": 20,
        }
        with patch("app.api.v1.patients.router.search_patients", new=AsyncMock(return_value=search_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/api/v1/reception/patients/search?search=nobody",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_get_patient_by_id(self):
        pid = str(uuid4())
        patient_response = _valid_patient_fixture(pid=pid)
        with patch("app.api.v1.patients.router.get_patient", new=AsyncMock(return_value=patient_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    f"/api/v1/reception/patients/{pid}",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_delete_patient(self):
        pid = str(uuid4())
        with patch("app.api.v1.patients.router.delete_patient", new=AsyncMock(return_value={"status": "deleted"})):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.delete(
                    f"/api/v1/reception/patients/{pid}",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_add_insurance_to_patient(self):
        pid = str(uuid4())
        iid = str(uuid4())
        body = {"insurer_name": "AAR", "policy_number": "AAR-001"}
        insurance_response = _valid_insurance_fixture(pid=pid, iid=iid)
        with patch("app.api.v1.patients.router.add_insurance_policy", new=AsyncMock(return_value=insurance_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    f"/api/v1/reception/patients/{pid}/insurance",
                    json=body,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 201

    def test_list_patient_insurance(self):
        pid = str(uuid4())
        insurance_list = [_valid_insurance_fixture(pid=pid)]
        with patch("app.api.v1.patients.router.get_insurance_policies", new=AsyncMock(return_value=insurance_list)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    f"/api/v1/reception/patients/{pid}/insurance",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Visits API router tests
# ---------------------------------------------------------------------------

class TestVisitRouter:
    def setup_method(self):
        _apply_auth_overrides()

    def teardown_method(self):
        _clear_auth_overrides()

    def test_create_visit(self):
        pid = str(uuid4())
        vid = str(uuid4())
        qid = str(uuid4())
        body = {"patient_id": str(pid), "visit_type": "outpatient", "payment_type": "cash"}
        visit_response = {
            "visit": _visit_response_fixture(vid=vid, pid=pid),
            "queue": _queue_summary_fixture(qid=qid, vid=vid, pid=pid),
            "queue_number": "T-001",
            "verification_flag": None,
        }
        with patch("app.api.v1.visits.router.create_visit", new=AsyncMock(return_value=visit_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/v1/reception/visits",
                    json=body,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 201

    def test_get_visit_detail_by_id(self):
        vid = str(uuid4())
        pid = str(uuid4())
        visit_response = {
            "visit_id": vid,
            "patient_id": pid,
            "visit_number": "V001",
            "visit_date": "2026-01-01",
            "visit_type": "outpatient",
            "payment_type": "cash",
            "insurance_id": None,
            "verification_flag": None,
            "queue_number": "T-001",
            "status": "registered",
            "registered_by": str(uuid4()),
            "created_at": "2026-01-01T08:00:00Z",
            "updated_at": "2026-01-01T08:00:00Z",
            "patient": _patient_summary_fixture(pid=pid),
            "insurance": None,
        }
        with patch("app.api.v1.visits.router.get_visit_detail", new=AsyncMock(return_value=visit_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    f"/api/v1/reception/visits/{vid}",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_verify_insurance_policy(self):
        iid = str(uuid4())
        pid = str(uuid4())
        body = {"verification_status": "verified", "verified_by": str(uuid4())}
        verify_response = _valid_insurance_fixture(pid=pid, iid=iid)
        verify_response["verification_status"] = "verified"
        with patch("app.api.v1.visits.router.verify_insurance_policy", new=AsyncMock(return_value=verify_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.patch(
                    f"/api/v1/reception/insurance/{iid}/verify",
                    json=body,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_reception_worklist_queue(self):
        queue_response = [{
            "queue_id": str(uuid4()),
            "queue_number": "T-001",
            "queue_type": "triage",
            "priority": "normal",
            "status": "waiting",
            "created_at": "2026-01-01T08:00:00Z",
            "called_at": None,
            "completed_at": None,
            "patient": _patient_summary_fixture(),
            "visit": _visit_summary_fixture(),
        }]
        with patch("app.api.v1.visits.router.get_reception_queue", new=AsyncMock(return_value=queue_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/api/v1/reception/queue",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200

    def test_triage_queue_today_legacy(self):
        with patch("app.api.v1.visits.router.triage_queue_today", new=AsyncMock(return_value=[])):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.get(
                    "/api/v1/reception/visits/queues/triage/today",
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Combined register-and-visit endpoint
# ---------------------------------------------------------------------------

class TestCombinedVisitRouter:
    def setup_method(self):
        _apply_auth_overrides()

    def teardown_method(self):
        _clear_auth_overrides()

    def test_register_and_create_visit_combined(self):
        pid = str(uuid4())
        vid = str(uuid4())
        qid = str(uuid4())
        body = {
            "patient": {
                "full_name": "Frank Osei",
                "date_of_birth": "1995-07-20",
                "gender": "male",
                "phone_primary": "0701234567",
            },
            "visit": {"visit_type": "outpatient", "payment_type": "cash"},
        }
        combined_response = {
            "patient": _valid_patient_fixture(pid=pid),
            "visit": {
                "visit": _visit_response_fixture(vid=vid, pid=pid),
                "queue": _queue_summary_fixture(qid=qid, vid=vid, pid=pid),
                "queue_number": "T-001",
                "verification_flag": None,
            },
        }
        with patch("app.api.v1.router.register_and_create_visit", new=AsyncMock(return_value=combined_response)):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(
                    "/api/v1/reception/register-and-visit",
                    json=body,
                    headers={"Authorization": "Bearer tok"},
                )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Schema validators — CombinedVisitData
# ---------------------------------------------------------------------------

from app.api.v1.schemas import CombinedVisitData


def test_combined_visit_data_valid_outpatient_cash():
    v = CombinedVisitData(visit_type="outpatient", payment_type="cash")
    assert v.visit_type == "outpatient"
    assert v.payment_type == "cash"


def test_combined_visit_data_invalid_visit_type_raises():
    with pytest.raises(ValidationError):
        CombinedVisitData(visit_type="invalid", payment_type="cash")


def test_combined_visit_data_invalid_payment_type_raises():
    with pytest.raises(ValidationError):
        CombinedVisitData(visit_type="outpatient", payment_type="barter")


def test_combined_visit_data_case_normalised_to_lowercase():
    v = CombinedVisitData(visit_type="INPATIENT", payment_type="INSURANCE")
    assert v.visit_type == "inpatient"
    assert v.payment_type == "insurance"


def test_combined_visit_data_emergency_type():
    v = CombinedVisitData(visit_type="emergency", payment_type="cash")
    assert v.visit_type == "emergency"


# ---------------------------------------------------------------------------
# Core database module
# ---------------------------------------------------------------------------

from app.core import database as db_mod


def test_get_session_local_returns_factory():
    factory = db_mod.get_session_local()
    assert factory is not None


def test_init_db_idempotent():
    db_mod.init_db()
    db_mod.init_db()


def test_default_database_router_session():
    router = db_mod.DefaultDatabaseRouter()
    session = router.get_session("hospital-1")
    assert session is not None
    session.close()


def test_get_hospital_context_and_close():
    ctx = db_mod.get_hospital_context("hospital-abc")
    assert ctx.hospital_id == "hospital-abc"
    assert ctx.db is not None
    db_mod.close_hospital_context(ctx)


def test_get_db_generator_yields_session():
    gen = db_mod.get_db()
    db = next(gen)
    assert db is not None
    try:
        next(gen)
    except StopIteration:
        pass


def test_database_router_abstract_not_instantiable():
    with pytest.raises(TypeError):
        db_mod.DatabaseRouter()


# ---------------------------------------------------------------------------
# Core tenant module
# ---------------------------------------------------------------------------

from app.core import tenant as tenant_mod


def test_resolve_tenant_db_url_returns_none_on_error():
    result = tenant_mod.resolve_tenant_db_url("nonexistent-tenant")
    assert result is None


def test_resolve_tenant_db_url_returns_result_when_found():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = "some-connection-string"
    with patch.object(tenant_mod, "get_db", return_value=iter([mock_db])):
        result = tenant_mod.resolve_tenant_db_url("t1")
    assert result == "some-connection-string"


def test_resolve_tenant_db_url_returns_none_when_not_found():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = None
    with patch.object(tenant_mod, "get_db", return_value=iter([mock_db])):
        result = tenant_mod.resolve_tenant_db_url("t1")
    assert result is None


# ---------------------------------------------------------------------------
# DB master layer
# ---------------------------------------------------------------------------

from app.db import master as db_master_mod


def test_master_session_context_manager():
    with db_master_mod.get_master_session() as db:
        assert db is not None


def test_master_get_db_direct():
    db = db_master_mod.get_master_db()
    assert db is not None
    db.close()


# ---------------------------------------------------------------------------
# DB session layer
# ---------------------------------------------------------------------------

from app.db import session as db_session_mod


def test_session_module_get_master_db():
    with db_session_mod.get_master_db() as db:
        assert db is not None


@pytest.mark.asyncio
async def test_session_module_get_tenant_db():
    async def fake_tenant_session(tid):
        yield MagicMock()

    with patch.object(db_session_mod, "get_tenant_session", new=fake_tenant_session):
        async for sess in db_session_mod.get_tenant_db("test-tenant"):
            assert sess is not None
            break


# ---------------------------------------------------------------------------
# DB tenant layer
# ---------------------------------------------------------------------------

from app.db import tenant as db_tenant_mod
from app.exceptions import TenantNotFoundError


@pytest.mark.asyncio
async def test_tenant_db_cached_session_factory():
    mock_factory = MagicMock()
    db_tenant_mod._async_engine_cache["cached-tenant"] = mock_factory
    try:
        result = await db_tenant_mod._get_async_session_factory("cached-tenant")
        assert result is mock_factory
    finally:
        del db_tenant_mod._async_engine_cache["cached-tenant"]


@pytest.mark.asyncio
async def test_tenant_db_not_found_raises():
    with patch("app.db.tenant.get_tenant_db_dsn", new=AsyncMock(return_value=None)):
        with pytest.raises(TenantNotFoundError):
            await db_tenant_mod._get_async_session_factory("unknown-tenant")


@pytest.mark.asyncio
async def test_tenant_session_yields_and_closes():
    mock_session = AsyncMock()
    mock_factory = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=mock_session),
        __aexit__=AsyncMock(return_value=None),
    ))

    with patch.object(db_tenant_mod, "_get_async_session_factory", new=AsyncMock(return_value=mock_factory)):
        async for session in db_tenant_mod.get_tenant_session("t1"):
            assert session is not None
            break


# ---------------------------------------------------------------------------
# Events subscriber
# ---------------------------------------------------------------------------

from app.events import subscriber as evt_sub


@pytest.mark.asyncio
async def test_events_subscriber_placeholder_handles_any_event():
    await evt_sub.handle_event("reception.patient_registered", {"patient_id": "p1"})
    await evt_sub.handle_event("unknown.event.type", {})
    await evt_sub.handle_event("", {})


# ---------------------------------------------------------------------------
# Messaging connection module
# ---------------------------------------------------------------------------

from app.messaging import connection as conn_mod


@pytest.mark.asyncio
async def test_messaging_declare_exchange_calls_channel():
    mock_ch = AsyncMock()
    mock_exch = AsyncMock()
    mock_ch.declare_exchange = AsyncMock(return_value=mock_exch)
    result = await conn_mod.declare_exchange(mock_ch)
    assert result is mock_exch


@pytest.mark.asyncio
async def test_messaging_get_channel_when_connection_fails():
    orig_get_conn = conn_mod.get_connection
    conn_mod.get_connection = AsyncMock(side_effect=Exception("AMQP unavailable"))
    try:
        with pytest.raises(Exception, match="AMQP unavailable"):
            await conn_mod.get_channel()
    finally:
        conn_mod.get_connection = orig_get_conn


# ---------------------------------------------------------------------------
# Core limiter
# ---------------------------------------------------------------------------

from app.core import limiter as limiter_mod


def test_limiter_instance_exists():
    assert limiter_mod.limiter is not None


# ---------------------------------------------------------------------------
# Tenant service — expired subscription branches
# ---------------------------------------------------------------------------

from app.services import tenant_service as ts_mod


def test_tenant_subscription_expired_one_day_past():
    past = datetime.now(timezone.utc) - timedelta(days=1)
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("active", past)
    result = asyncio.run(ts_mod.check_tenant_subscription(mock_db, "t1"))
    assert result == "expired"


def test_tenant_subscription_update_expired_under_30_days():
    past = datetime.now(timezone.utc) - timedelta(days=10)
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("active", past, 1)
    result = asyncio.run(ts_mod.check_and_update_tenant_status(mock_db, "t1"))
    assert result == "expired"


def test_tenant_subscription_update_expired_over_30_days_suspends():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    ts_mod._redis = mock_redis
    past = datetime.now(timezone.utc) - timedelta(days=35)
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("active", past, 1)
    try:
        result = asyncio.run(ts_mod.check_and_update_tenant_status(mock_db, "t1"))
        assert result == "suspended"
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_cache_suspension_redis_error_silent():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock(side_effect=Exception("Redis unavailable"))
    ts_mod._redis = mock_redis
    try:
        await ts_mod.cache_tenant_suspension("t1")
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_remove_suspension_cache_redis_error_silent():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock(side_effect=Exception("Redis unavailable"))
    ts_mod._redis = mock_redis
    try:
        await ts_mod.remove_tenant_suspension_cache("t1")
    finally:
        ts_mod._redis = None


# ---------------------------------------------------------------------------
# Orchestrator — client singleton and header extraction
# ---------------------------------------------------------------------------

from app.services import orchestrator as orch_mod


def test_orchestrator_client_singleton():
    original = orch_mod._client
    orch_mod._client = None
    try:
        c1 = orch_mod._get_client()
        c2 = orch_mod._get_client()
        assert c1 is c2
    finally:
        orch_mod._client = original


def test_orchestrator_extract_headers_filters_forbidden():
    req = MagicMock()
    req.headers.items.return_value = [
        ("authorization", "Bearer tok"),
        ("x-tenant-id", "tenant-1"),
        ("host", "localhost"),
        ("content-length", "123"),
        ("x-tenant-db", "db-url"),
    ]
    headers = orch_mod._extract_headers(req)
    assert "authorization" in headers
    assert "x-tenant-id" in headers
    assert "host" not in headers
    assert "content-length" not in headers
    assert "x-tenant-db" not in headers


@pytest.mark.asyncio
async def test_orchestrator_forward_with_query_string():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"results": []}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        req = MagicMock()
        req.headers.items.return_value = [("x-tenant-id", "t1")]
        req.query_params.__str__ = MagicMock(return_value="page=1&limit=20")
        result = await orch_mod._forward("GET", "http://svc", "/path", req)
        assert result == {"results": []}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_forward_raw_with_params():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": []}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        sc, data = await orch_mod._forward_raw(
            "GET", "http://svc", "/path",
            {"x-tenant-id": "t1"},
            params={"page": 1},
        )
        assert sc == 200
        assert data == {"data": []}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_forward_raw_no_content_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 204
    mock_resp.json.side_effect = Exception("no body")
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        sc, data = await orch_mod._forward_raw("DELETE", "http://svc", "/path", {})
        assert sc == 204
        assert data == {}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_add_insurance_patient_not_found_raises():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(400, {"detail": "Bad Request"}))):
        req = MagicMock()
        req.headers.items.return_value = []
        with pytest.raises(HTTPException) as exc:
            await orch_mod.add_insurance_policy(
                "pid",
                MagicMock(model_dump=MagicMock(return_value={})),
                req,
            )
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_orchestrator_insurance_policies_non_list_returns_empty():
    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value={"error": "invalid"})):
        req = MagicMock()
        req.headers.items.return_value = []
        result = await orch_mod.get_insurance_policies("pid", req)
    assert result == []


@pytest.mark.asyncio
async def test_orchestrator_visit_with_uuid_insurance_id():
    import uuid
    ins_id = uuid.uuid4()
    body = MagicMock(model_dump=MagicMock(return_value={
        "patient_id": str(uuid4()),
        "visit_type": "outpatient",
        "payment_type": "insurance",
        "insurance_id": ins_id,
    }))
    expected = {"visit_id": str(uuid4()), "status": "registered"}
    req = MagicMock()
    req.headers.items.return_value = []
    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value=expected)):
        result = await orch_mod.create_visit(body, req)
    assert result == expected


@pytest.mark.asyncio
async def test_orchestrator_register_and_visit_with_insurance():
    patient_id = str(uuid4())
    insurance_id = str(uuid4())
    visit_id = str(uuid4())
    call_count = {"n": 0}

    async def side_effects(method, base, path, headers, body=None, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 201, {"id": patient_id}
        if call_count["n"] == 2:
            return 201, {"insurance_id": insurance_id}
        return 201, {"visit_id": visit_id}

    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"full_name": "Test Patient"},
        "visit": {"visit_type": "outpatient", "payment_type": "insurance"},
        "insurance": {"insurer_name": "AAR", "policy_number": "P1"},
    }))
    req = MagicMock()
    req.headers.items.return_value = []
    req.state = MagicMock()
    with patch.object(orch_mod, "_forward_raw", new=side_effects):
        result = await orch_mod.register_and_create_visit(body, req)
    assert result["patient"]["id"] == patient_id
    assert result["visit"]["visit_id"] == visit_id


@pytest.mark.asyncio
async def test_orchestrator_visit_detail_patient_fetch_fails():
    visit_id = str(uuid4())
    patient_id = str(uuid4())
    visit_data = {"visit_id": visit_id, "patient_id": patient_id, "insurance_id": None}
    req = MagicMock()
    req.headers.items.return_value = []

    async def mock_raw(method, base, path, headers, body=None, params=None):
        return 404, {}

    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value=visit_data)):
        with patch.object(orch_mod, "_forward_raw", new=mock_raw):
            result = await orch_mod.get_visit_detail(visit_id, req)
    assert "patient" not in result


@pytest.mark.asyncio
async def test_orchestrator_visit_detail_insurance_fetch_fails():
    visit_id = str(uuid4())
    patient_id = str(uuid4())
    insurance_id = str(uuid4())
    visit_data = {"visit_id": visit_id, "patient_id": patient_id, "insurance_id": insurance_id}
    patient_data = {"id": patient_id, "full_name": "Grace", "patient_number": "P1"}
    req = MagicMock()
    req.headers.items.return_value = []

    async def mock_raw(method, base, path, headers, body=None, params=None):
        if path.endswith("/insurance"):
            return 500, {}
        return 200, patient_data

    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value=visit_data)):
        with patch.object(orch_mod, "_forward_raw", new=mock_raw):
            result = await orch_mod.get_visit_detail(visit_id, req)
    assert result["patient"]["full_name"] == "Grace"
    assert "insurance" not in result


@pytest.mark.asyncio
async def test_orchestrator_visit_detail_insurance_no_matching_policy():
    visit_id = str(uuid4())
    patient_id = str(uuid4())
    insurance_id = str(uuid4())
    visit_data = {"visit_id": visit_id, "patient_id": patient_id, "insurance_id": insurance_id}
    patient_data = {"id": patient_id, "full_name": "Henry", "patient_number": "P2"}
    insurance_list = [{"insurance_id": str(uuid4()), "insurer_name": "Other"}]
    req = MagicMock()
    req.headers.items.return_value = []

    async def mock_raw(method, base, path, headers, body=None, params=None):
        if path.endswith("/insurance"):
            return 200, insurance_list
        return 200, patient_data

    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value=visit_data)):
        with patch.object(orch_mod, "_forward_raw", new=mock_raw):
            result = await orch_mod.get_visit_detail(visit_id, req)
    assert "insurance" not in result


@pytest.mark.asyncio
async def test_orchestrator_reception_queue_patient_fetch_fails():
    patient_id = str(uuid4())
    visit_id = str(uuid4())
    entry = {
        "queue_id": str(uuid4()), "queue_number": "T-002", "queue_type": "triage",
        "priority": "urgent", "status": "waiting", "created_at": "2026-01-01",
        "called_at": None, "completed_at": None, "patient_id": patient_id, "visit_id": visit_id,
    }
    req = MagicMock()
    req.headers.items.return_value = []

    async def mock_raw(method, base, path, headers, body=None, params=None):
        if "queues" in path:
            return 200, [entry]
        return 500, {}

    with patch.object(orch_mod, "_forward_raw", new=mock_raw):
        result = await orch_mod.get_reception_queue(req)
    assert len(result) == 1
    assert result[0]["patient"]["full_name"] == "Unknown"


# ---------------------------------------------------------------------------
# Middleware — audit log DB write branch
# ---------------------------------------------------------------------------

from app.core.middleware import AuditLogMiddleware


@pytest.mark.asyncio
async def test_audit_middleware_writes_db_on_post():
    middleware = AuditLogMiddleware(app=MagicMock())
    req = MagicMock()
    req.method = "POST"
    req.url.path = "/api/v1/reception/patients"
    req.state.user_sub = "test-user"
    req.state.tenant = None
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_response.status_code = 201
    call_next = AsyncMock(return_value=mock_response)
    mock_db = MagicMock()
    mock_session_local = MagicMock(return_value=mock_db)
    with patch("app.core.middleware.get_session_local", return_value=mock_session_local):
        result = await middleware.dispatch(req, call_next)
    assert result is mock_response


@pytest.mark.asyncio
async def test_audit_middleware_db_error_does_not_propagate():
    middleware = AuditLogMiddleware(app=MagicMock())
    req = MagicMock()
    req.method = "PUT"
    req.url.path = "/api/v1/reception/patients/123"
    req.state.user_sub = "u1"
    req.state.tenant = None
    mock_response = MagicMock()
    mock_response.headers = {}
    mock_response.status_code = 200
    call_next = AsyncMock(return_value=mock_response)
    mock_db = MagicMock()
    mock_db.execute.side_effect = Exception("DB write failed")
    mock_session_local = MagicMock(return_value=mock_db)
    with patch("app.core.middleware.get_session_local", return_value=mock_session_local):
        result = await middleware.dispatch(req, call_next)
    assert result is mock_response


# ---------------------------------------------------------------------------
# Security module — HS256 local token decode branches
# ---------------------------------------------------------------------------

from app.core import security as sec_mod


def _hs256_token(payload: dict) -> str:
    import os
    key = os.environ.get("SECRET_KEY", "ci-test-secret-key-for-testing-purposes-only")
    return jwt.encode(payload, key, algorithm="HS256")


@pytest.mark.asyncio
async def test_security_decodes_valid_hs256_token():
    token = _hs256_token({
        "sub": "sa1", "preferred_username": "admin",
        "realm_access": {"roles": ["super_admin"]},
        "exp": int(time.time()) + 3600,
    })
    result = await sec_mod._decode_token(token)
    assert result["sub"] == "sa1"


@pytest.mark.asyncio
async def test_security_rejects_expired_hs256_token():
    token = _hs256_token({"sub": "sa1", "exp": int(time.time()) - 10})
    with pytest.raises(HTTPException) as exc:
        await sec_mod._decode_token(token)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Tenant auth module — HS256 decode branches
# ---------------------------------------------------------------------------

from app.core import tenant_auth as ta_mod


@pytest.mark.asyncio
async def test_tenant_auth_decodes_valid_hs256_token():
    token = _hs256_token({
        "sub": "u-local", "preferred_username": "local",
        "exp": int(time.time()) + 3600,
    })
    result = await ta_mod._decode_token(token)
    assert result["sub"] == "u-local"


@pytest.mark.asyncio
async def test_tenant_auth_decodes_impersonation_key_token():
    import os
    key = os.environ.get("SECRET_KEY", "ci-test-secret-key-for-testing-purposes-only")
    token = jwt.encode(
        {"sub": "imp-user", "exp": int(time.time()) + 3600},
        key,
        algorithm="HS256",
        headers={"kid": "impersonation-key"},
    )
    result = await ta_mod._decode_token(token)
    assert result["sub"] == "imp-user"


@pytest.mark.asyncio
async def test_tenant_auth_rejects_expired_hs256_token():
    token = _hs256_token({"sub": "u-exp", "exp": int(time.time()) - 10})
    with pytest.raises(HTTPException) as exc:
        await ta_mod._decode_token(token)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_auth_rejects_completely_invalid_token():
    with pytest.raises(HTTPException) as exc:
        await ta_mod._decode_token("completely.invalid.token")
    assert exc.value.status_code == 401
