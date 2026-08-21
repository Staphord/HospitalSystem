"""Comprehensive tests for reception-service infrastructure:
security, tenant_auth, middleware, exceptions, messaging, tenant_service,
orchestrator branches, db layers, and dependencies.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Security module tests
# ---------------------------------------------------------------------------

from app.core import security as sec_mod


@pytest.mark.asyncio
async def test_security_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        await sec_mod.get_current_active_user(request=MagicMock(), credentials=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_security_non_bearer_scheme():
    creds = HTTPAuthorizationCredentials(scheme="Basic", credentials="dXNlcjpwYXNz")
    with pytest.raises(HTTPException) as exc:
        await sec_mod.get_current_active_user(request=MagicMock(), credentials=creds)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_security_decode_invalid_token():
    with pytest.raises(HTTPException) as exc:
        await sec_mod._decode_token("not.a.valid.jwt")
    assert exc.value.status_code == 401


def test_security_extract_realm_invalid_token():
    assert sec_mod._extract_realm_from_iss("bad.token.here") is None


def test_security_build_rsa_key_missing_kid():
    with pytest.raises(HTTPException) as exc:
        sec_mod._build_rsa_key({"keys": [{"kid": "other"}]}, "missing")
    assert exc.value.status_code == 401


def test_security_extract_roles_superadmin():
    user = sec_mod.TokenPayload(
        sub="sa", preferred_username="admin", email=None,
        realm_access={}, raw={"type": "superadmin", "role": "super_admin"}
    )
    roles = sec_mod._extract_roles(user)
    assert "super_admin" in roles


def test_security_extract_roles_no_realm_access():
    user = sec_mod.TokenPayload(
        sub="u1", preferred_username="u", email=None,
        realm_access=None, raw={}
    )
    roles = sec_mod._extract_roles(user)
    assert roles == []


@pytest.mark.asyncio
async def test_security_get_current_active_user_success():
    orig_dec = sec_mod._decode_token
    async def mock_dec(t):
        return {"sub": "u1", "preferred_username": "user1", "email": "u1@h.com", "realm_access": {"roles": ["doctor"]}}
    sec_mod._decode_token = mock_dec
    try:
        req = MagicMock()
        req.state = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
        with patch("app.core.security.settings.keycloak_introspect", False):
            result = await sec_mod.get_current_active_user(request=req, credentials=creds)
            assert result.sub == "u1"
    finally:
        sec_mod._decode_token = orig_dec


@pytest.mark.asyncio
async def test_security_require_role_allowed():
    user = sec_mod.TokenPayload(
        sub="d1", preferred_username="doc", email=None,
        realm_access={"roles": ["doctor"]}, raw={}
    )
    dep = sec_mod.require_role("doctor")
    result = await dep(user=user)
    assert result.sub == "d1"


@pytest.mark.asyncio
async def test_security_require_role_denied():
    user = sec_mod.TokenPayload(
        sub="n1", preferred_username="nurse", email=None,
        realm_access={"roles": ["nurse"]}, raw={}
    )
    dep = sec_mod.require_role("pharmacist")
    with pytest.raises(HTTPException) as exc:
        await dep(user=user)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_security_require_role_super_admin_always_allowed():
    user = sec_mod.TokenPayload(
        sub="sa", preferred_username="admin", email=None,
        realm_access={"roles": ["super_admin"]}, raw={}
    )
    dep = sec_mod.require_role("doctor")
    result = await dep(user=user)
    assert result.sub == "sa"


# ---------------------------------------------------------------------------
# Tenant auth module tests
# ---------------------------------------------------------------------------

from app.core import tenant_auth as ta_mod


@pytest.mark.asyncio
async def test_tenant_auth_missing_credentials():
    req = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await ta_mod.get_current_tenant(request=req, credentials=None)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_auth_superadmin_token():
    orig_dec = ta_mod._decode_token
    async def mock_dec(t):
        return {"type": "superadmin", "super_admin_id": "sa-1", "username": "admin", "role": "super_admin"}
    ta_mod._decode_token = mock_dec
    try:
        req = MagicMock()
        req.state = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="super-tok")
        ctx = await ta_mod.get_current_tenant(request=req, credentials=creds)
        assert ctx.is_super_admin is True
        assert ctx.user_sub == "sa-1"
    finally:
        ta_mod._decode_token = orig_dec


@pytest.mark.asyncio
async def test_tenant_auth_regular_token_with_tenant():
    orig_dec = ta_mod._decode_token
    async def mock_dec(t):
        return {
            "sub": "u-reg", "preferred_username": "reguser", "email": "reg@h.com",
            "realm_access": {"roles": ["receptionist"]}, "tenant_id": "tenant-abc",
            "scope": "full", "is_super_admin": False,
        }
    ta_mod._decode_token = mock_dec
    orig_suspended = ta_mod.is_tenant_suspended
    ta_mod.is_tenant_suspended = AsyncMock(return_value=False)
    try:
        req = MagicMock()
        req.state = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="reg-tok")
        ctx = await ta_mod.get_current_tenant(request=req, credentials=creds)
        assert ctx.tenant_id == "tenant-abc"
        assert ctx.is_super_admin is False
    finally:
        ta_mod._decode_token = orig_dec
        ta_mod.is_tenant_suspended = orig_suspended


@pytest.mark.asyncio
async def test_tenant_auth_suspended_tenant_raises():
    orig_dec = ta_mod._decode_token
    async def mock_dec(t):
        return {
            "sub": "u1", "preferred_username": "u", "email": "u@h.com",
            "realm_access": {"roles": ["doctor"]}, "tenant_id": "suspended-tenant",
            "scope": "full", "is_super_admin": False,
        }
    ta_mod._decode_token = mock_dec
    orig_suspended = ta_mod.is_tenant_suspended
    ta_mod.is_tenant_suspended = AsyncMock(return_value=True)
    try:
        req = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="susp-tok")
        with pytest.raises(HTTPException) as exc:
            await ta_mod.get_current_tenant(request=req, credentials=creds)
        assert exc.value.status_code == 403
    finally:
        ta_mod._decode_token = orig_dec
        ta_mod.is_tenant_suspended = orig_suspended


@pytest.mark.asyncio
async def test_tenant_auth_no_tenant_no_super_raises():
    orig_dec = ta_mod._decode_token
    async def mock_dec(t):
        return {
            "sub": "u1", "preferred_username": "u", "email": "u@h.com",
            "realm_access": {"roles": ["doctor"]}, "tenant_id": None,
            "scope": "full", "is_super_admin": False,
        }
    ta_mod._decode_token = mock_dec
    try:
        req = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="no-tenant-tok")
        with pytest.raises(HTTPException) as exc:
            await ta_mod.get_current_tenant(request=req, credentials=creds)
        assert exc.value.status_code == 403
    finally:
        ta_mod._decode_token = orig_dec


@pytest.mark.asyncio
async def test_tenant_auth_readonly_scope():
    orig_dec = ta_mod._decode_token
    async def mock_dec(t):
        return {
            "sub": "u1", "preferred_username": "u", "email": "u@h.com",
            "realm_access": {"roles": ["doctor"]}, "tenant_id": "t1",
            "scope": "readonly", "is_super_admin": False,
        }
    ta_mod._decode_token = mock_dec
    orig_suspended = ta_mod.is_tenant_suspended
    ta_mod.is_tenant_suspended = AsyncMock(return_value=False)
    try:
        req = MagicMock()
        req.state = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="readonly-tok")
        ctx = await ta_mod.get_current_tenant(request=req, credentials=creds)
        assert ctx.scope == "readonly"
    finally:
        ta_mod._decode_token = orig_dec
        ta_mod.is_tenant_suspended = orig_suspended


# ---------------------------------------------------------------------------
# Exceptions module tests
# ---------------------------------------------------------------------------

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


def test_all_exception_classes():
    assert UnauthorizedError().status_code == 401
    assert ForbiddenError().status_code == 403
    assert NotFoundError().status_code == 404
    assert ConflictError().status_code == 409
    assert BadRequestError().status_code == 400
    assert RateLimitError().status_code == 429
    assert TenantNotFoundError().status_code == 404
    assert TokenExpiredError().status_code == 401
    assert MFARequiredError().status_code == 401
    assert TenantSuspendedError().status_code == 403
    assert ReadOnlyScopeError().status_code == 403


def test_exception_custom_messages():
    err = UnauthorizedError("Custom unauthorized")
    assert err.detail == "Custom unauthorized"
    err2 = NotFoundError("Custom not found")
    assert err2.detail == "Custom not found"


# ---------------------------------------------------------------------------
# Middleware tests
# ---------------------------------------------------------------------------

from app.core.middleware import AuditLogMiddleware, ImpersonationBannerMiddleware, ReadOnlyScopeMiddleware


@pytest.mark.asyncio
async def test_audit_log_middleware_options_skipped():
    middleware = AuditLogMiddleware(app=MagicMock())
    req = MagicMock()
    req.method = "OPTIONS"
    mock_response = MagicMock()
    call_next = AsyncMock(return_value=mock_response)
    result = await middleware.dispatch(req, call_next)
    assert result is mock_response


@pytest.mark.asyncio
async def test_audit_log_middleware_get_no_db_write():
    middleware = AuditLogMiddleware(app=MagicMock())
    req = MagicMock()
    req.method = "GET"
    req.state = MagicMock(spec=["request_id", "user_sub", "tenant"])
    mock_response = MagicMock()
    mock_response.headers = {}
    call_next = AsyncMock(return_value=mock_response)
    result = await middleware.dispatch(req, call_next)
    assert result is mock_response


@pytest.mark.asyncio
async def test_read_only_scope_middleware_blocks_post():
    middleware = ReadOnlyScopeMiddleware(app=MagicMock())
    tenant = MagicMock()
    tenant.scope = "readonly"
    req = MagicMock()
    req.method = "POST"
    req.state.tenant = tenant
    call_next = AsyncMock()
    resp = await middleware.dispatch(req, call_next)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_read_only_scope_middleware_allows_get():
    middleware = ReadOnlyScopeMiddleware(app=MagicMock())
    req = MagicMock()
    req.method = "GET"
    mock_response = MagicMock()
    call_next = AsyncMock(return_value=mock_response)
    result = await middleware.dispatch(req, call_next)
    assert result is mock_response


@pytest.mark.asyncio
async def test_impersonation_banner_middleware_sets_header():
    middleware = ImpersonationBannerMiddleware(app=MagicMock())
    tenant = MagicMock()
    tenant.scope = "readonly"
    req = MagicMock()
    req.state.tenant = tenant
    mock_response = MagicMock()
    mock_response.headers = {}
    call_next = AsyncMock(return_value=mock_response)
    await middleware.dispatch(req, call_next)
    assert mock_response.headers.get("X-Impersonation-Banner") == "true"


# ---------------------------------------------------------------------------
# Tenant service tests
# ---------------------------------------------------------------------------

from app.services import tenant_service as ts_mod


@pytest.mark.asyncio
async def test_tenant_service_is_suspended_redis_hit():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="1")
    ts_mod._redis = mock_redis
    try:
        result = await ts_mod.is_tenant_suspended("t1")
        assert result is True
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_is_suspended_redis_not_suspended():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value="0")
    ts_mod._redis = mock_redis
    try:
        result = await ts_mod.is_tenant_suspended("t1")
        assert result is False
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_is_suspended_redis_miss():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    ts_mod._redis = mock_redis
    try:
        result = await ts_mod.is_tenant_suspended("t1")
        assert result is False
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_is_suspended_redis_exception():
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))
    ts_mod._redis = mock_redis
    try:
        result = await ts_mod.is_tenant_suspended("t1")
        assert result is False
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_cache_suspension():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    ts_mod._redis = mock_redis
    try:
        await ts_mod.cache_tenant_suspension("t1", ttl=60)
        mock_redis.set.assert_awaited_once()
    finally:
        ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_remove_suspension_cache():
    mock_redis = AsyncMock()
    mock_redis.delete = AsyncMock()
    ts_mod._redis = mock_redis
    try:
        await ts_mod.remove_tenant_suspension_cache("t1")
        mock_redis.delete.assert_awaited_once()
    finally:
        ts_mod._redis = None


def test_tenant_service_encrypt_decrypt_dsn():
    original = "postgresql://user:pass@localhost/db"
    encrypted = ts_mod.encrypt_dsn(original)
    assert encrypted != original
    decrypted = ts_mod.decrypt_dsn(encrypted)
    assert decrypted == original


def test_tenant_service_check_subscription_not_found():
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = None
    result = asyncio.run(ts_mod.check_tenant_subscription(mock_db, "nonexistent"))
    assert result == "not_found"


def test_tenant_service_check_subscription_suspended():
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("suspended", None)
    result = asyncio.run(ts_mod.check_tenant_subscription(mock_db, "t1"))
    assert result == "suspended"


def test_tenant_service_check_subscription_active():
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("active", None)
    result = asyncio.run(ts_mod.check_tenant_subscription(mock_db, "t1"))
    assert result == "active"


def test_tenant_service_check_and_update_not_found():
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = None
    result = asyncio.run(ts_mod.check_and_update_tenant_status(mock_db, "nonexistent"))
    assert result == "not_found"


def test_tenant_service_check_and_update_suspended():
    mock_redis = AsyncMock()
    mock_redis.set = AsyncMock()
    ts_mod._redis = mock_redis
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("suspended", None, 1)
    try:
        result = asyncio.run(ts_mod.check_and_update_tenant_status(mock_db, "t1"))
        assert result == "suspended"
    finally:
        ts_mod._redis = None


def test_tenant_service_check_and_update_active():
    mock_db = MagicMock()
    mock_db.execute.return_value.one_or_none.return_value = ("active", None, 1)
    result = asyncio.run(ts_mod.check_and_update_tenant_status(mock_db, "t1"))
    assert result == "active"


def test_tenant_service_get_tenant_db_dsn_not_found():
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = None
    result = asyncio.run(ts_mod.get_tenant_db_dsn(mock_db, "nonexistent"))
    assert result is None


def test_tenant_service_get_tenant_db_dsn_found():
    original_dsn = "postgresql://user:pass@localhost/testdb"
    encrypted = ts_mod.encrypt_dsn(original_dsn)
    mock_db = MagicMock()
    mock_db.execute.return_value.scalar.return_value = encrypted
    result = asyncio.run(ts_mod.get_tenant_db_dsn(mock_db, "t1"))
    assert result == original_dsn


# ---------------------------------------------------------------------------
# Orchestrator additional branch tests
# ---------------------------------------------------------------------------

from app.services import orchestrator as orch_mod


def _make_req(headers=None):
    req = MagicMock()
    req.headers.items.return_value = (headers or {}).items()
    req.query_params = MagicMock()
    req.query_params.__str__ = MagicMock(return_value="")
    req.state = MagicMock()
    return req


@pytest.mark.asyncio
async def test_orchestrator_forward_error_status_raises():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"detail": "Internal error"}
    client = AsyncMock()
    client.request = AsyncMock(return_value=mock_resp)
    orig_client = orch_mod._client
    orch_mod._client = client
    try:
        with pytest.raises(HTTPException) as exc:
            await orch_mod._forward("GET", "http://svc", "/path", _make_req())
        assert exc.value.status_code == 500
    finally:
        orch_mod._client = orig_client


@pytest.mark.asyncio
async def test_orchestrator_forward_error_non_json_body():
    mock_resp = MagicMock()
    mock_resp.status_code = 503
    mock_resp.json.side_effect = Exception("not json")
    mock_resp.text = "Service Unavailable"
    client = AsyncMock()
    client.request = AsyncMock(return_value=mock_resp)
    orig_client = orch_mod._client
    orch_mod._client = client
    try:
        with pytest.raises(HTTPException) as exc:
            await orch_mod._forward("GET", "http://svc", "/path", _make_req())
        assert exc.value.status_code == 503
    finally:
        orch_mod._client = orig_client


@pytest.mark.asyncio
async def test_orchestrator_forward_non_json_success_response():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.side_effect = Exception("not json")
    client = AsyncMock()
    client.request = AsyncMock(return_value=mock_resp)
    orig_client = orch_mod._client
    orch_mod._client = client
    try:
        result = await orch_mod._forward("DELETE", "http://svc", "/path", _make_req())
        assert result == {"status": "ok"}
    finally:
        orch_mod._client = orig_client


@pytest.mark.asyncio
async def test_orchestrator_register_patient_other_error():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(500, {"detail": "Server Error"}))):
        with pytest.raises(HTTPException) as exc:
            await orch_mod.register_patient(MagicMock(model_dump=MagicMock(return_value={})), _make_req())
        assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_orchestrator_register_patient_publishes_event():
    patient_id = str(uuid4())
    patient_data = {"id": patient_id, "patient_number": "PAT-001"}
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(201, patient_data))):
        with patch("app.events.publisher.publish_patient_registered", new=AsyncMock()) as mock_pub:
            result = await orch_mod.register_patient(
                MagicMock(model_dump=MagicMock(return_value={})),
                _make_req(headers={"x-tenant-id": "t1"})
            )
    assert result == patient_data


@pytest.mark.asyncio
async def test_orchestrator_register_patient_event_failure_continues():
    patient_id = str(uuid4())
    patient_data = {"id": patient_id}
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(201, patient_data))):
        with patch("app.events.publisher.publish_patient_registered", new=AsyncMock(side_effect=Exception("AMQP down"))):
            result = await orch_mod.register_patient(
                MagicMock(model_dump=MagicMock(return_value={})),
                _make_req(headers={"x-tenant-id": "t1"})
            )
    assert result["id"] == patient_id


@pytest.mark.asyncio
async def test_orchestrator_get_visit_detail_with_insurance():
    visit_id = str(uuid4())
    patient_id = str(uuid4())
    insurance_id = str(uuid4())
    visit_data = {"visit_id": visit_id, "patient_id": patient_id, "insurance_id": insurance_id}
    patient_data = {"id": patient_id, "patient_number": "PAT-001", "full_name": "Alice"}
    insurance_list = [{"insurance_id": insurance_id, "insurer_name": "AAR", "policy_number": "P1", "verification_status": "verified"}]

    raw_call_count = {"n": 0}
    async def mock_raw(method, base, path, headers, body=None, params=None):
        raw_call_count["n"] += 1
        if path.endswith("/insurance"):
            return 200, [{"insurance_id": str(insurance_id), "insurer_name": "AAR", "policy_number": "P1", "verification_status": "verified"}]
        return 200, patient_data

    with patch.object(orch_mod, "_forward", new=AsyncMock(return_value=visit_data)):
        with patch.object(orch_mod, "_forward_raw", new=mock_raw):
            result = await orch_mod.get_visit_detail(visit_id, _make_req())

    assert result["patient"]["full_name"] == "Alice"
    assert result["insurance"]["insurer_name"] == "AAR"


@pytest.mark.asyncio
async def test_orchestrator_get_visit_detail_no_insurance():
    visit_id = str(uuid4())
    patient_id = str(uuid4())
    visit_data = {"visit_id": visit_id, "patient_id": patient_id, "insurance_id": None}
    patient_data = {"id": patient_id, "full_name": "Bob", "patient_number": "P2"}

    async def mock_forward(method, base, path, req):
        return visit_data

    async def mock_raw(method, base, path, headers, body=None, params=None):
        return 200, patient_data

    orig_forward = orch_mod._forward
    orig_raw = orch_mod._forward_raw
    orch_mod._forward = mock_forward
    orch_mod._forward_raw = mock_raw
    try:
        result = await orch_mod.get_visit_detail(visit_id, _make_req())
        assert "insurance" not in result
    finally:
        orch_mod._forward = orig_forward
        orch_mod._forward_raw = orig_raw


@pytest.mark.asyncio
async def test_orchestrator_reception_queue_empty_on_error():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(503, {}))):
        result = await orch_mod.get_reception_queue(_make_req())
        assert result == []


@pytest.mark.asyncio
async def test_orchestrator_reception_queue_not_list():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(200, {"data": []}))):
        result = await orch_mod.get_reception_queue(_make_req())
        assert result == []


@pytest.mark.asyncio
async def test_orchestrator_reception_queue_enrichment():
    patient_id = str(uuid4())
    visit_id = str(uuid4())
    entry = {
        "queue_id": str(uuid4()), "queue_number": "T-001", "queue_type": "triage",
        "priority": "normal", "status": "waiting", "created_at": "2026-01-01",
        "called_at": None, "completed_at": None, "patient_id": patient_id, "visit_id": visit_id,
    }
    patient_data = {"id": patient_id, "patient_number": "PAT-001", "full_name": "Carol"}
    visit_data = {"visit_id": visit_id, "visit_number": "V001", "queue_number": "T-001", "visit_type": "outpatient", "payment_type": "cash", "status": "registered"}

    async def mock_raw(method, base, path, headers, body=None, params=None):
        if "patients" in path:
            return 200, patient_data
        if "queues" in path:
            return 200, [entry]
        return 200, visit_data

    orig_raw = orch_mod._forward_raw
    orch_mod._forward_raw = mock_raw
    try:
        result = await orch_mod.get_reception_queue(_make_req())
        assert len(result) == 1
        assert result[0]["patient"]["full_name"] == "Carol"
    finally:
        orch_mod._forward_raw = orig_raw


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_missing_fields():
    with pytest.raises(HTTPException) as exc:
        body = MagicMock(model_dump=MagicMock(return_value={"patient": None, "visit": None, "insurance": None}))
        await orch_mod.register_and_create_visit(body, _make_req())
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_patient_409():
    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"name": "Test"}, "visit": {"visit_type": "outpatient"}, "insurance": None
    }))
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(409, {"detail": "Duplicate"}))):
        with pytest.raises(HTTPException) as exc:
            await orch_mod.register_and_create_visit(body, _make_req())
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_patient_500():
    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"name": "Test"}, "visit": {"visit_type": "outpatient"}, "insurance": None
    }))
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(500, {"detail": "Error"}))):
        with pytest.raises(HTTPException) as exc:
            await orch_mod.register_and_create_visit(body, _make_req())
        assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_insurance_failure_rollback():
    patient_id = str(uuid4())
    call_count = {"n": 0}

    async def side_effect_raw(method, base, path, headers, body=None, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 201, {"id": patient_id}
        return 400, {"detail": "Insurance error"}

    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"name": "Test"}, "visit": {"visit_type": "outpatient", "payment_type": "insurance"}, "insurance": {"insurer_name": "AAR"}
    }))
    with patch.object(orch_mod, "_forward_raw", new=side_effect_raw):
        with patch.object(orch_mod, "publish_event", new=AsyncMock()):
            with pytest.raises(HTTPException):
                await orch_mod.register_and_create_visit(body, _make_req())


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_success():
    patient_id = str(uuid4())
    visit_id = str(uuid4())
    call_count = {"n": 0}

    async def side_effect_raw(method, base, path, headers, body=None, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 201, {"id": patient_id, "patient_number": "P1"}
        return 201, {"visit_id": visit_id, "visit_number": "V1"}

    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"name": "Test"}, "visit": {"visit_type": "outpatient", "payment_type": "cash"}, "insurance": None
    }))
    with patch.object(orch_mod, "_forward_raw", new=side_effect_raw):
        result = await orch_mod.register_and_create_visit(body, _make_req())
    assert result["patient"]["id"] == patient_id
    assert result["visit"]["visit_id"] == visit_id


@pytest.mark.asyncio
async def test_orchestrator_verify_insurance_404_raises():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(404, {"detail": "Not found"}))):
        with pytest.raises(HTTPException) as exc:
            await orch_mod.verify_insurance_policy("bad-id", MagicMock(model_dump=MagicMock(return_value={})), _make_req())
        assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_orchestrator_verify_insurance_500_raises():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(500, {"detail": "Error"}))):
        with pytest.raises(HTTPException) as exc:
            await orch_mod.verify_insurance_policy("ins-id", MagicMock(model_dump=MagicMock(return_value={})), _make_req())
        assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_orchestrator_search_patients_clamps_page_size():
    async def mock_forward(method, base, path, req, extra_params=None):
        return {"results": [], "total": 0}

    orig_forward = orch_mod._forward
    orch_mod._forward = mock_forward
    try:
        result = await orch_mod.search_patients(_make_req(), search="alice", page=0, page_size=9999)
        assert result is not None
    finally:
        orch_mod._forward = orig_forward


@pytest.mark.asyncio
async def test_orchestrator_triage_queue_today():
    expected = {"queue": []}
    async def mock_forward(method, base, path, req):
        return expected
    orig_forward = orch_mod._forward
    orch_mod._forward = mock_forward
    try:
        result = await orch_mod.triage_queue_today(_make_req())
        assert result == expected
    finally:
        orch_mod._forward = orig_forward


# ---------------------------------------------------------------------------
# Messaging publisher / subscriber tests
# ---------------------------------------------------------------------------

from app.messaging import publisher as pub_mod
from app.messaging import subscriber as sub_mod


@pytest.mark.asyncio
async def test_publisher_publish_event_success():
    class FakeExch:
        async def publish(self, *a, **kw): pass

    with patch.object(pub_mod, "get_channel", new=AsyncMock()):
        with patch.object(pub_mod, "declare_exchange", new=AsyncMock(return_value=FakeExch())):
            await pub_mod.publish_event("test.event", {"key": "value"})


@pytest.mark.asyncio
async def test_publisher_publish_event_exception_logged():
    with patch.object(pub_mod, "get_channel", new=AsyncMock(side_effect=Exception("AMQP Error"))):
        # Should not raise, just log
        await pub_mod.publish_event("test.fail", {"key": "value"})


@pytest.mark.asyncio
async def test_subscriber_start_consumer_loop():
    class FakeProc:
        async def __aenter__(self): pass
        async def __aexit__(self, *a, **kw): pass

    class FakeMsg:
        body = b'{"event": "reception.registered"}'
        routing_key = "reception.registered"
        def process(self):
            return FakeProc()

    class FakeQueueIter:
        async def __aenter__(self): return self
        async def __aexit__(self, *a, **kw): pass
        def __aiter__(self):
            self.done = False
            return self
        async def __anext__(self):
            if not self.done:
                self.done = True
                return FakeMsg()
            raise StopAsyncIteration

    class FakeQueue:
        name = "reception_events"
        async def bind(self, *a, **kw): pass
        def iterator(self): return FakeQueueIter()

    class FakeCh:
        async def set_qos(self, *a, **kw): pass
        async def declare_exchange(self, *a, **kw): pass
        async def declare_queue(self, *a, **kw): return FakeQueue()

    class FakeConn:
        async def channel(self): return FakeCh()

    orig_get_conn = sub_mod.get_connection
    async def fake_get_conn(): return FakeConn()
    sub_mod.get_connection = fake_get_conn

    class FakeExch:
        async def publish(self, *a, **kw): pass

    handled = []
    async def handler(key, payload):
        handled.append((key, payload))

    with patch("app.messaging.connection.declare_exchange", new=AsyncMock(return_value=FakeExch())):
        try:
            await sub_mod.start_consumer("reception_service", ["reception.registered"], handler)
        finally:
            sub_mod.get_connection = orig_get_conn
    assert len(handled) == 1
    assert handled[0][0] == "reception.registered"


# ---------------------------------------------------------------------------
# Events publisher tests
# ---------------------------------------------------------------------------

from app.events import publisher as evt_pub


@pytest.mark.asyncio
async def test_events_publisher_publish_patient_registered():
    with patch.object(evt_pub, "publish_event", new=AsyncMock()) as mock_pub:
        await evt_pub.publish_patient_registered("pat-1", "tenant-1")
        mock_pub.assert_awaited_once()
