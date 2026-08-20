"""Targeted unit tests for dependencies, security introspection/JWKS, Keycloak session revocation,
and remaining branch paths in reception-service.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

from app.core.config import settings
from app.core import security as sec_mod
from app.core import tenant_auth as ta_mod
from app import dependencies as deps_mod
from app.services import tenant_service as ts_mod
from app.db import tenant as db_tenant_mod
from app.messaging import connection as conn_mod


# ---------------------------------------------------------------------------
# Dependencies module tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dependencies_get_current_user():
    payload = sec_mod.TokenPayload(
        sub="user-123", preferred_username="john", email="john@example.com",
        realm_access={"roles": ["doctor"]}, raw={},
    )
    user = await deps_mod.get_current_user(user=payload)
    assert user.sub == "user-123"


@pytest.mark.asyncio
async def test_dependencies_get_tenant_db():
    ctx = ta_mod.TenantContext(
        tenant_id="tenant-xyz", user_sub="sub1", preferred_username="user1",
        email="user1@example.com", roles=["receptionist"], is_super_admin=False,
    )
    mock_session = AsyncMock()

    async def fake_session_gen(tid):
        yield mock_session

    with patch("app.dependencies.get_tenant_session", side_effect=fake_session_gen):
        sessions = []
        async for session in deps_mod.get_tenant_db(ctx=ctx):
            sessions.append(session)
        assert len(sessions) == 1
        assert sessions[0] is mock_session


# ---------------------------------------------------------------------------
# Security — get_current_hospital_id tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_current_hospital_id_user_associated():
    user_payload = sec_mod.TokenPayload(
        sub="sub-hosp-1", preferred_username="user", email=None,
        realm_access={"roles": ["doctor"]}, raw={},
    )
    mock_user_record = MagicMock()
    mock_user_record.hospital_id = "hosp-123"

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.one_or_none.return_value = mock_user_record

    hosp_id = await sec_mod.get_current_hospital_id(user=user_payload, db=mock_db)
    assert hosp_id == "hosp-123"


@pytest.mark.asyncio
async def test_get_current_hospital_id_super_admin_returns_none():
    user_payload = sec_mod.TokenPayload(
        sub="sub-admin", preferred_username="admin", email=None,
        realm_access={"roles": ["super_admin"]}, raw={},
    )
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.one_or_none.return_value = None

    hosp_id = await sec_mod.get_current_hospital_id(user=user_payload, db=mock_db)
    assert hosp_id is None


@pytest.mark.asyncio
async def test_get_current_hospital_id_unassociated_raises_forbidden():
    user_payload = sec_mod.TokenPayload(
        sub="sub-unassociated", preferred_username="user", email=None,
        realm_access={"roles": ["doctor"]}, raw={},
    )
    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.one_or_none.return_value = None

    with pytest.raises(HTTPException) as exc:
        await sec_mod.get_current_hospital_id(user=user_payload, db=mock_db)
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Security — _fetch_jwks & _introspect_token & realm extraction
# ---------------------------------------------------------------------------

def test_security_extract_realm_from_iss_matching():
    token = jwt.encode({"iss": f"{settings.keycloak_url}/realms/hospital_realm"}, "secret", algorithm="HS256")
    realm = sec_mod._extract_realm_from_iss(token)
    assert realm == "hospital_realm"


@pytest.mark.asyncio
async def test_security_fetch_jwks_cache_and_network():
    sec_mod._jwks_cache.clear()
    fake_jwks = {"keys": [{"kid": "k1", "kty": "RSA"}]}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_jwks)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        jwks1 = await sec_mod._fetch_jwks("test_realm")
        assert jwks1 == fake_jwks

        # Second call should return from cache without network request
        jwks2 = await sec_mod._fetch_jwks("test_realm")
        assert jwks2 == fake_jwks


@pytest.mark.asyncio
async def test_security_introspect_token_active():
    sec_mod._introspection_cache.clear()
    token = "test_token_active"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"active": True})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        await sec_mod._introspect_token(token)
        assert sec_mod._introspection_cache.get(token) is True

        # Second call should use cache
        await sec_mod._introspect_token(token)


@pytest.mark.asyncio
async def test_security_introspect_token_inactive_raises():
    sec_mod._introspection_cache.clear()
    token = "test_token_inactive"

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value={"active": False})

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(HTTPException) as exc:
            await sec_mod._introspect_token(token)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_security_introspect_cached_inactive_raises():
    token = "test_token_cached_inactive"
    sec_mod._introspection_cache[token] = False

    with pytest.raises(HTTPException) as exc:
        await sec_mod._introspect_token(token)
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Tenant auth — JWKS & RS256 token decode tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_auth_fetch_jwks_cache_and_network():
    ta_mod._jwks_cache.clear()
    fake_jwks = {"keys": [{"kid": "k2", "kty": "RSA"}]}

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=fake_jwks)

    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        jwks1 = await ta_mod._fetch_jwks("realm_a")
        assert jwks1 == fake_jwks

        # Cached call
        jwks2 = await ta_mod._fetch_jwks("realm_a")
        assert jwks2 == fake_jwks


def test_tenant_auth_build_rsa_key_found():
    jwks = {"keys": [{"kid": "target_kid", "kty": "RSA"}]}
    key = ta_mod._build_rsa_key(jwks, "target_kid")
    assert key["kid"] == "target_kid"


def test_tenant_auth_build_rsa_key_not_found_raises():
    jwks = {"keys": [{"kid": "other_kid"}]}
    with pytest.raises(HTTPException) as exc:
        ta_mod._build_rsa_key(jwks, "missing_kid")
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Security & Tenant Auth RS256 token decoding mocks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_decode_rs256_token_flow():
    token = "header.payload.signature"

    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.security._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.security._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", return_value={"sub": "user-rs256"}):
                    res = await sec_mod._decode_token(token)
                    assert res["sub"] == "user-rs256"


@pytest.mark.asyncio
async def test_tenant_auth_decode_rs256_token_flow():
    token = "header.payload.signature"

    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.tenant_auth._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.tenant_auth._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", return_value={"sub": "user-rs256", "tenant_id": "t-rs256"}):
                    res = await ta_mod._decode_token(token)
                    assert res["sub"] == "user-rs256"


# ---------------------------------------------------------------------------
# Tenant service — _get_redis & _revoke_keycloak_sessions
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_service_get_redis():
    ts_mod._redis = None
    mock_redis_instance = AsyncMock()
    with patch("redis.asyncio.from_url", return_value=mock_redis_instance):
        r1 = await ts_mod._get_redis()
        r2 = await ts_mod._get_redis()
        assert r1 is mock_redis_instance
        assert r2 is mock_redis_instance
    ts_mod._redis = None


@pytest.mark.asyncio
async def test_tenant_service_revoke_keycloak_sessions_success():
    token_resp = MagicMock()
    token_resp.raise_for_status = MagicMock()
    token_resp.json = MagicMock(return_value={"access_token": "admin-token-123"})

    users_resp = MagicMock()
    users_resp.is_success = True
    users_resp.json = MagicMock(return_value=[{"id": "user-uuid-1"}])

    logout_resp = MagicMock()
    logout_resp.is_success = True

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=token_resp)
    mock_client.get = AsyncMock(return_value=users_resp)
    mock_client.put = AsyncMock(return_value=logout_resp)

    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        await ts_mod._revoke_keycloak_sessions("tenant-revoked")
        mock_client.post.assert_called_once()
        mock_client.get.assert_called_once()
        mock_client.put.assert_called_once()


@pytest.mark.asyncio
async def test_tenant_service_revoke_keycloak_sessions_exception_silenced():
    mock_client = MagicMock()
    mock_client.post = AsyncMock(side_effect=Exception("Keycloak down"))

    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("httpx.AsyncClient", return_value=mock_client_ctx):
        # Should not raise exception
        await ts_mod._revoke_keycloak_sessions("tenant-revoked")


# ---------------------------------------------------------------------------
# DB Tenant — engine factory creation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tenant_db_async_session_factory_creation():
    db_tenant_mod._async_engine_cache.clear()
    raw_dsn = "postgresql://user:pass@localhost:5432/tenant_db"

    with patch("app.db.tenant.get_tenant_db_dsn", new=AsyncMock(return_value=raw_dsn)):
        with patch("app.db.master.get_master_db", return_value=MagicMock()):
            with patch("app.db.tenant.create_async_engine") as mock_create_engine:
                mock_engine = MagicMock()
                mock_create_engine.return_value = mock_engine

                factory = await db_tenant_mod._get_async_session_factory("tenant-new")
                assert factory is not None
                mock_create_engine.assert_called_once()
                args, kwargs = mock_create_engine.call_args
                assert "postgresql+asyncpg://" in args[0]


# ---------------------------------------------------------------------------
# Messaging connection module tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messaging_connection_get_connection_singleton():
    conn_mod._connection = None
    mock_conn = AsyncMock()
    with patch("aio_pika.connect_robust", new=AsyncMock(return_value=mock_conn)):
        c1 = await conn_mod.get_connection()
        c2 = await conn_mod.get_connection()
        assert c1 is mock_conn
        assert c2 is mock_conn
    conn_mod._connection = None


@pytest.mark.asyncio
async def test_messaging_connection_get_channel_singleton():
    conn_mod._channel = None
    mock_conn = AsyncMock()
    mock_chan = AsyncMock()
    mock_conn.channel = AsyncMock(return_value=mock_chan)

    with patch("app.messaging.connection.get_connection", new=AsyncMock(return_value=mock_conn)):
        ch1 = await conn_mod.get_channel()
        assert ch1 is mock_chan
    conn_mod._channel = None


@pytest.mark.asyncio
async def test_messaging_connection_close_connection():
    mock_conn = AsyncMock()
    mock_conn.is_closed = False
    conn_mod._connection = mock_conn
    await conn_mod.close_connection()
    mock_conn.close.assert_called_once()
    assert conn_mod._connection is None


@pytest.mark.asyncio
async def test_security_decode_rs256_expired_token_raises():
    token = "header.payload.signature"
    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.security._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.security._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", side_effect=jwt.ExpiredSignatureError("Expired")):
                    with pytest.raises(HTTPException) as exc:
                        await sec_mod._decode_token(token)
                    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_security_decode_rs256_invalid_token_raises():
    token = "header.payload.signature"
    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.security._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.security._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", side_effect=Exception("Invalid RS256")):
                    with pytest.raises(HTTPException) as exc:
                        await sec_mod._decode_token(token)
                    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_auth_decode_rs256_expired_token_raises():
    token = "header.payload.signature"
    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.tenant_auth._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.tenant_auth._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", side_effect=jwt.ExpiredSignatureError("Expired")):
                    with pytest.raises(HTTPException) as exc:
                        await ta_mod._decode_token(token)
                    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_auth_decode_rs256_invalid_token_raises():
    token = "header.payload.signature"
    with patch("jose.jwt.get_unverified_header", return_value={"kid": "rs256-kid"}):
        with patch("app.core.tenant_auth._extract_realm_from_iss", return_value="realm-1"):
            with patch("app.core.tenant_auth._fetch_jwks", new=AsyncMock(return_value={"keys": [{"kid": "rs256-kid"}]})):
                with patch("jose.jwt.decode", side_effect=Exception("Invalid RS256")):
                    with pytest.raises(HTTPException) as exc:
                        await ta_mod._decode_token(token)
                    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Subscriber — error handling and task runner tests
# ---------------------------------------------------------------------------

from app.messaging import subscriber as sub_mod


@pytest.mark.asyncio
async def test_subscriber_run_consumer_task():
    async def dummy_handler(k, p): pass
    with patch("app.messaging.subscriber.start_consumer", new=AsyncMock()):
        task = await sub_mod.run_consumer_task("reception", ["key"], dummy_handler)
        assert task is not None
        task.cancel()


@pytest.mark.asyncio
async def test_subscriber_consumer_exception_in_handler_logging():
    class FakeProc:
        async def __aenter__(self): pass
        async def __aexit__(self, *a, **kw): pass

    class FakeMsg:
        body = b'{"bad": "json"}'
        routing_key = "key1"
        def process(self): return FakeProc()

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
        name = "q1"
        async def bind(self, *a, **kw): pass
        def iterator(self): return FakeQueueIter()

    class FakeCh:
        async def set_qos(self, *a, **kw): pass
        async def declare_exchange(self, *a, **kw): pass
        async def declare_queue(self, *a, **kw): return FakeQueue()

    class FakeConn:
        async def channel(self): return FakeCh()

    async def failing_handler(k, p):
        raise Exception("Handler error")

    with patch("app.messaging.subscriber.get_connection", new=AsyncMock(return_value=FakeConn())):
        with patch("app.messaging.connection.declare_exchange", new=AsyncMock()):
            # Should run loop, catch exception, and exit cleanly
            await sub_mod.start_consumer("test_service", ["key1"], failing_handler)


# ---------------------------------------------------------------------------
# Security & Tenant Auth — HS256 Expired Signature tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_decode_hs256_expired_raises():
    import os
    key = os.environ.get("SECRET_KEY", "ci-test-secret-key-for-testing-purposes-only")
    expired_token = jwt.encode({"sub": "u1", "exp": int(time.time()) - 100}, key, algorithm="HS256")

    with patch("jose.jwt.get_unverified_header", return_value={"alg": "HS256"}):
        with pytest.raises(HTTPException) as exc:
            await sec_mod._decode_token(expired_token)
        assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_tenant_auth_decode_hs256_expired_raises():
    import os
    key = os.environ.get("SECRET_KEY", "ci-test-secret-key-for-testing-purposes-only")
    expired_token = jwt.encode({"sub": "u1", "exp": int(time.time()) - 100}, key, algorithm="HS256")

    with patch("jose.jwt.get_unverified_header", return_value={"alg": "HS256"}):
        with pytest.raises(HTTPException) as exc:
            await ta_mod._decode_token(expired_token)
        assert exc.value.status_code == 401


def test_tenant_auth_extract_realm_exception_handling():
    with patch("jose.jwt.get_unverified_claims", side_effect=Exception("Decode failed")):
        assert ta_mod._extract_realm_from_iss("invalid_token") is None


# ---------------------------------------------------------------------------
# Main app lifespan, CORS, and CSP tests
# ---------------------------------------------------------------------------

from app.main import lifespan, app as fastapi_app


@pytest.mark.asyncio
async def test_main_lifespan_with_subscriber_task():
    class DummyTask:
        def cancel(self): pass
        def __await__(self):
            async def _dummy(): pass
            return _dummy().__await__()

    mock_task = DummyTask()

    with patch("app.core.database.init_db"):
        with patch("app.events.subscriber.start_subscriber", new=AsyncMock(), create=True):
            with patch("asyncio.create_task", return_value=mock_task):
                async with lifespan(fastapi_app):
                    pass


@pytest.mark.asyncio
async def test_main_lifespan_subscriber_cancelled_error():
    async def cancelling_task():
        raise asyncio.CancelledError()

    mock_task = asyncio.create_task(cancelling_task())

    with patch("app.core.database.init_db"):
        with patch("app.events.subscriber.start_subscriber", new=AsyncMock(), create=True):
            with patch("asyncio.create_task", return_value=mock_task):
                async with lifespan(fastapi_app):
                    pass


def test_main_security_headers_prod_mode():
    from fastapi.testclient import TestClient
    with patch.object(settings, "environment", "prod"):
        with TestClient(fastapi_app) as client:
            resp = client.get("/health")
            assert resp.headers.get("Content-Security-Policy") == "default-src 'none'"


# ---------------------------------------------------------------------------
# Orchestrator missing branch tests
# ---------------------------------------------------------------------------

from app.services import orchestrator as orch_mod


@pytest.mark.asyncio
async def test_orchestrator_forward_httpx_exception_raises_500():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"detail": "Connection refused"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        req = MagicMock()
        req.headers.items.return_value = []
        with pytest.raises(HTTPException) as exc:
            await orch_mod._forward("GET", "http://svc", "/path", req)
        assert exc.value.status_code == 500
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_forward_raw_httpx_exception_returns_500():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.json.return_value = {"detail": "Network down"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        sc, data = await orch_mod._forward_raw("GET", "http://svc", "/path", {})
        assert sc == 500
        assert data == {"detail": "Network down"}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_search_patients_with_national_id():
    req = MagicMock()
    req.headers.items.return_value = []
    req.query_params.__str__ = MagicMock(return_value="search=12345678")

    async def mock_forward(method, base, path, request, extra_params=None):
        return {"results": [{"id": "p1", "national_id": "12345678"}], "total": 1}

    with patch.object(orch_mod, "_forward", new=mock_forward):
        result = await orch_mod.search_patients(req, search="12345678", page=1, page_size=20)
        assert result["total"] == 1


@pytest.mark.asyncio
async def test_orchestrator_delete_patient_exception_raises_500():
    with patch.object(orch_mod, "_forward", side_effect=HTTPException(status_code=500, detail="Delete failed")):
        req = MagicMock()
        req.headers.items.return_value = []
        with pytest.raises(HTTPException) as exc:
            await orch_mod.delete_patient("p1", req)
        assert exc.value.status_code == 500


import httpx
from fastapi.security import HTTPAuthorizationCredentials


from app.core import database as db_mod


# ---------------------------------------------------------------------------
# DatabaseRouter abstract method test
# ---------------------------------------------------------------------------

def test_database_router_abstract_method_raises():
    class TestRouter(db_mod.DatabaseRouter):
        def get_session(self, hospital_id: str):
            return super().get_session(hospital_id)

    router = TestRouter()
    with pytest.raises(NotImplementedError):
        router.get_session("h1")


# ---------------------------------------------------------------------------
# Additional Orchestrator branch tests (extra_params, body types, rollback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_orchestrator_forward_extra_params_and_dict_body():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        req = MagicMock()
        req.headers.items.return_value = []
        res = await orch_mod._forward(
            "POST", "http://svc", "/path", req,
            body={"key": "val"}, extra_params={"param": "1", "empty": None}
        )
        assert res == {"status": "ok"}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_forward_non_dict_content_body():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        req = MagicMock()
        req.headers.items.return_value = []
        res = await orch_mod._forward(
            "POST", "http://svc", "/path", req,
            body="raw text body", extra_params=None
        )
        assert res == {"status": "ok"}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_forward_raw_with_body_and_params():
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": "ok"}
    mock_client = AsyncMock()
    mock_client.request = AsyncMock(return_value=mock_resp)
    orig = orch_mod._client
    orch_mod._client = mock_client
    try:
        sc, data = await orch_mod._forward_raw(
            "POST", "http://svc", "/path", {},
            body={"data": "test"}, params={"filter": "abc", "none_val": None}
        )
        assert sc == 200
        assert data == {"data": "ok"}
    finally:
        orch_mod._client = orig


@pytest.mark.asyncio
async def test_orchestrator_create_visit_unverified_insurance_policy():
    pid = str(uuid4())
    iid = str(uuid4())
    body = MagicMock(model_dump=MagicMock(return_value={
        "patient_id": str(pid),
        "visit_type": "outpatient",
        "payment_type": "insurance",
        "insurance_id": iid,
    }))
    policy = {"insurance_id": iid, "verification_status": "pending"}
    req = MagicMock()
    req.headers.items.return_value = []

    async def mock_raw(method, base, path, headers, body=None, params=None):
        if "insurance" in path:
            return 200, [policy]
        return 201, {"visit_id": str(uuid4())}

    with patch.object(orch_mod, "_forward_raw", new=mock_raw):
        with patch.object(orch_mod, "_forward", new=AsyncMock(return_value={"visit_id": str(uuid4())})):
            result = await orch_mod.create_visit(body, req)
    assert result is not None


@pytest.mark.asyncio
async def test_orchestrator_register_and_create_visit_insurance_failure_triggers_delete():
    pid = str(uuid4())
    call_count = {"n": 0}

    async def mock_raw(method, base, path, headers, body=None, params=None):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return 201, {"id": pid}
        return 400, {"detail": "Insurance validation failed"}

    body = MagicMock(model_dump=MagicMock(return_value={
        "patient": {"full_name": "Rollback Patient"},
        "visit": {"visit_type": "outpatient", "payment_type": "insurance"},
        "insurance": {"insurer_name": "Invalid Insurer"},
    }))
    req = MagicMock()
    req.headers.items.return_value = []
    req.state = MagicMock()

    with patch.object(orch_mod, "_forward_raw", new=mock_raw):
        with patch.object(orch_mod, "publish_event", new=AsyncMock()) as mock_pub:
            with pytest.raises(HTTPException) as exc:
                await orch_mod.register_and_create_visit(body, req)
            assert exc.value.status_code == 400
            mock_pub.assert_awaited_once()


@pytest.mark.asyncio
async def test_orchestrator_verify_insurance_policy_exception_raises_500():
    with patch.object(orch_mod, "_forward_raw", new=AsyncMock(return_value=(500, {"detail": "Service down"}))):
        req = MagicMock()
        req.headers.items.return_value = []
        with pytest.raises(HTTPException) as exc:
            await orch_mod.verify_insurance_policy(
                "i1", MagicMock(model_dump=MagicMock(return_value={})), req
            )
        assert exc.value.status_code == 500


# ---------------------------------------------------------------------------
# Security — get_current_active_user with introspect enabled
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_security_get_current_active_user_introspect_enabled():
    with patch.object(settings, "keycloak_introspect", True):
        with patch("app.core.security._decode_token", new=AsyncMock(return_value={"sub": "u1", "preferred_username": "user1", "email": "u1@h.com"})):
            with patch("app.core.security._introspect_token", new=AsyncMock()) as mock_intro:
                req = MagicMock()
                req.state = MagicMock()
                creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="tok")
                user = await sec_mod.get_current_active_user(request=req, credentials=creds)
                assert user.sub == "u1"
                mock_intro.assert_awaited_once_with("tok")



