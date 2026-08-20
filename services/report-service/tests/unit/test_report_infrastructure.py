"""Infrastructure, security, database, messaging, and authentication unit tests for report-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from jose import jwt, JWTError, ExpiredSignatureError
import pytest
from fastapi import HTTPException

from app.core import config as cfg_mod
from app.core import middleware as mid_mod
from app.core import tenant_auth as ta_mod
from app.core import security as sec_mod
from app.core import database as db_mod
from app.db import tenant as db_tenant_mod
from app.db import session as db_sess_mod
from app import dependencies as deps_mod
from app.messaging import connection as msg_conn_mod
from app.messaging import publisher as msg_pub_mod
from app.messaging import subscriber as msg_sub_mod


# ---------------------------------------------------------------------------
# Config and Middleware Unit Tests
# ---------------------------------------------------------------------------

class TestReportConfigAndMiddleware:
    def test_config_loaded(self):
        assert cfg_mod.settings is not None
        assert cfg_mod.settings.secret_key is not None

    def test_middleware_instance(self):
        app_mock = MagicMock()
        middleware = mid_mod.AuditLogMiddleware(app_mock)
        assert middleware is not None


class TestReportMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_audit_log_middleware_options(self):
        req = MagicMock(method="OPTIONS")
        call_next = AsyncMock(return_value=MagicMock(headers={}))
        mw = mid_mod.AuditLogMiddleware(MagicMock())
        resp = await mw.dispatch(req, call_next)
        assert resp is not None

    @pytest.mark.asyncio
    async def test_audit_log_middleware_post_insert(self):
        req = MagicMock(method="POST")
        req.url.path = "/api/v1/reports"
        req.state.user_sub = "u1"
        req.state.tenant = MagicMock(tenant_id="t1")

        mock_db = MagicMock()
        with patch("app.core.middleware.get_session_local", return_value=MagicMock(return_value=mock_db)):
            resp_mock = MagicMock(headers={}, status_code=200)
            call_next = AsyncMock(return_value=resp_mock)
            mw = mid_mod.AuditLogMiddleware(MagicMock())
            resp = await mw.dispatch(req, call_next)
            assert resp.headers["X-Request-ID"] is not None

    @pytest.mark.asyncio
    async def test_readonly_scope_middleware(self):
        req_post_readonly = MagicMock(method="POST")
        req_post_readonly.state.tenant = MagicMock(scope="readonly")
        call_next = AsyncMock()

        mw = mid_mod.ReadOnlyScopeMiddleware(MagicMock())
        resp = await mw.dispatch(req_post_readonly, call_next)
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_impersonation_banner_middleware(self):
        req = MagicMock()
        req.state.tenant = MagicMock(scope="readonly")
        mock_resp = MagicMock(headers={})
        call_next = AsyncMock(return_value=mock_resp)

        mw = mid_mod.ImpersonationBannerMiddleware(MagicMock())
        resp = await mw.dispatch(req, call_next)
        assert resp.headers.get("X-Impersonation-Banner") == "true"


# ---------------------------------------------------------------------------
# Tenant Auth and JWT Decoding Unit Tests
# ---------------------------------------------------------------------------

class TestTenantAuthAndJWT:
    def test_extract_realm_from_iss_valid_and_invalid(self):
        valid_iss = f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"
        tok = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert ta_mod._extract_realm_from_iss(tok) == "hospital-realm"

        assert ta_mod._extract_realm_from_iss("not-a-jwt") is None

    def test_build_rsa_key_success_and_failure(self):
        jwks = {"keys": [{"kid": "k1", "n": "abc"}]}
        assert ta_mod._build_rsa_key(jwks, "k1") == {"kid": "k1", "n": "abc"}

        with pytest.raises(HTTPException) as exc:
            ta_mod._build_rsa_key(jwks, "k2")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_fetch_jwks_tenant_auth(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"keys": [{"kid": "k1"}]}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            ta_mod._jwks_cache.clear()
            jwks = await ta_mod._fetch_jwks("hospital-realm")
            assert jwks == {"keys": [{"kid": "k1"}]}

    @pytest.mark.asyncio
    async def test_decode_token_hs256_success(self):
        payload = {"sub": "u1", "preferred_username": "user1", "roles": ["doctor"]}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")

        decoded = await ta_mod._decode_token(tok)
        assert decoded["sub"] == "u1"
        assert decoded["preferred_username"] == "user1"

    @pytest.mark.asyncio
    async def test_decode_token_rs256_mock(self):
        jwks = {"keys": [{"kid": "k-rs256", "n": "abc"}]}
        tok = jwt.encode({"sub": "rs256-user", "iss": f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"}, "secret", algorithm="HS256")

        with patch("app.core.tenant_auth._fetch_jwks", AsyncMock(return_value=jwks)):
            with patch("app.core.tenant_auth._build_rsa_key", return_value={"kid": "k-rs256"}):
                with patch("jose.jwt.get_unverified_header", return_value={"alg": "RS256", "kid": "k-rs256"}):
                    with patch("jose.jwt.decode", return_value={"sub": "rs256-user"}):
                        decoded = await ta_mod._decode_token(tok)
                        assert decoded["sub"] == "rs256-user"

    @pytest.mark.asyncio
    async def test_decode_token_expired_signature_error(self):
        tok = jwt.encode({"sub": "u1"}, "secret", algorithm="HS256")
        with patch("jose.jwt.decode", side_effect=jwt.ExpiredSignatureError("Expired")):
            with pytest.raises(HTTPException) as exc:
                await ta_mod._decode_token(tok)
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_get_current_tenant_superadmin(self):
        payload = {"type": "superadmin", "super_admin_id": "sa-1", "username": "admin", "role": "super_admin"}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()

        ctx = await ta_mod.get_current_tenant(req, credentials=creds)
        assert ctx.is_super_admin is True
        assert ctx.user_sub == "sa-1"

    @pytest.mark.asyncio
    async def test_get_current_tenant_standard_user_and_errors(self):
        payload = {"sub": "u1", "preferred_username": "john", "tenant_id": "tenant-1", "realm_access": {"roles": ["doctor"]}}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()

        with patch("app.core.tenant_auth.is_tenant_suspended", new=AsyncMock(return_value=False)):
            ctx = await ta_mod.get_current_tenant(req, credentials=creds)
            assert ctx.tenant_id == "tenant-1"

        payload_no_tenant = {"sub": "u2", "preferred_username": "mary", "realm_access": {"roles": ["doctor"]}}
        tok_no_tenant = jwt.encode(payload_no_tenant, cfg_mod.settings.secret_key, algorithm="HS256")
        creds_no_tenant = MagicMock(scheme="bearer", credentials=tok_no_tenant)

        with pytest.raises(HTTPException) as exc1:
            await ta_mod.get_current_tenant(req, credentials=creds_no_tenant)
        assert exc1.value.status_code == 403

        with patch("app.core.tenant_auth.is_tenant_suspended", new=AsyncMock(return_value=True)):
            with pytest.raises(HTTPException) as exc2:
                await ta_mod.get_current_tenant(req, credentials=creds)
            assert exc2.value.status_code == 403


# ---------------------------------------------------------------------------
# Core Security Module Comprehensive Tests
# ---------------------------------------------------------------------------

class TestCoreSecurityModule:
    def test_issuer(self):
        iss = sec_mod._issuer("my-realm")
        assert "my-realm" in iss

    @pytest.mark.asyncio
    async def test_fetch_jwks_sec_mod(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"keys": [{"kid": "k-sec-1"}]}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            sec_mod._jwks_cache.clear()
            jwks = await sec_mod._fetch_jwks("my-realm")
            assert jwks == {"keys": [{"kid": "k-sec-1"}]}

    @pytest.mark.asyncio
    async def test_decode_token_hs256_sec_mod(self):
        payload = {"sub": "admin-1", "preferred_username": "admin"}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")

        decoded = await sec_mod._decode_token(tok)
        assert decoded["sub"] == "admin-1"

    def test_build_rsa_key_sec_mod(self):
        jwks = {"keys": [{"kid": "k-sec-1"}]}
        assert sec_mod._build_rsa_key(jwks, "k-sec-1") == {"kid": "k-sec-1"}

    @pytest.mark.asyncio
    async def test_introspect_token_http_call(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = {"active": True}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.post.return_value = mock_resp

        with patch("httpx.AsyncClient", return_value=mock_client):
            await sec_mod._introspect_token("token-to-introspect")

    @pytest.mark.asyncio
    async def test_get_current_active_user_success(self):
        payload = {"sub": "user-999", "preferred_username": "user999", "realm_access": {"roles": ["doctor"]}}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()

        user = await sec_mod.get_current_active_user(req, credentials=creds)
        assert user.sub == "user-999"

    @pytest.mark.asyncio
    async def test_get_current_active_user_no_creds(self):
        req = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await sec_mod.get_current_active_user(req, credentials=None)
        assert exc.value.status_code == 401

    def test_extract_roles_superadmin_vs_realm(self):
        user1 = sec_mod.TokenPayload(
            sub="u1", preferred_username="u1", email=None, realm_access={}, raw={"type": "superadmin", "role": "super_admin"}
        )
        assert sec_mod._extract_roles(user1) == ["super_admin"]

        user2 = sec_mod.TokenPayload(
            sub="u2", preferred_username="u2", email=None, realm_access={"roles": ["nurse"]}, raw={}
        )
        assert sec_mod._extract_roles(user2) == ["nurse"]

    @pytest.mark.asyncio
    async def test_require_role_checker(self):
        checker = sec_mod.require_role("doctor")
        user = sec_mod.TokenPayload(
            sub="u1", preferred_username="u1", email=None, realm_access={"roles": ["doctor"]}, raw={}
        )
        assert await checker(user=user) == user

        user_bad = sec_mod.TokenPayload(
            sub="u1", preferred_username="u1", email=None, realm_access={"roles": ["nurse"]}, raw={}
        )
        with pytest.raises(HTTPException) as exc:
            await checker(user=user_bad)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_current_hospital_id_found_and_superadmin(self):
        user_hosp = sec_mod.TokenPayload(
            sub="u-hosp", preferred_username="u", email=None, realm_access={"roles": ["doctor"]}, raw={}
        )
        mock_db = MagicMock()
        mock_user_rec = MagicMock(hospital_id="hosp-100")
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = mock_user_rec

        hosp_id = await sec_mod.get_current_hospital_id(user=user_hosp, db=mock_db)
        assert hosp_id == "hosp-100"

        user_super = sec_mod.TokenPayload(
            sub="u-super", preferred_username="super", email=None, realm_access={"roles": ["super_admin"]}, raw={}
        )
        mock_db.query.return_value.filter.return_value.one_or_none.return_value = None
        assert await sec_mod.get_current_hospital_id(user=user_super, db=mock_db) is None


# ---------------------------------------------------------------------------
# Tenant Session & Dependencies Unit Tests
# ---------------------------------------------------------------------------

class TestTenantSessionAndDependencies:
    @pytest.mark.asyncio
    async def test_get_async_session_factory_cached_and_not_found(self):
        db_tenant_mod._async_engine_cache["t-cached"] = MagicMock()
        factory = await db_tenant_mod._get_async_session_factory("t-cached")
        assert factory is not None

        with patch("app.db.tenant.get_tenant_db_dsn", new=AsyncMock(return_value=None)):
            with patch("app.db.master.get_master_db"):
                with pytest.raises(Exception):
                    await db_tenant_mod._get_async_session_factory("t-missing")

    @pytest.mark.asyncio
    async def test_get_current_user_dependency(self):
        mock_user = MagicMock()
        res = await deps_mod.get_current_user(user=mock_user)
        assert res is mock_user

    @pytest.mark.asyncio
    async def test_get_db_session_factories(self):
        session_factory = db_mod.get_session_local()
        assert session_factory is not None

        db_gen = db_mod.get_db()
        db_inst = next(db_gen)
        assert db_inst is not None

        db_mod.init_db()

        router = db_mod.DefaultDatabaseRouter()
        assert router is not None

        ctx = db_mod.get_hospital_context("hosp-1")
        assert ctx.hospital_id == "hosp-1"
        db_mod.close_hospital_context(ctx)

        with patch("app.db.session.get_tenant_session") as mock_sess:
            async def _fake_session(tid):
                yield "session_val"
            mock_sess.side_effect = _fake_session
            async for sess in db_sess_mod.get_tenant_db("t1"):
                assert sess == "session_val"

    @pytest.mark.asyncio
    async def test_app_lifespan(self):
        from app.main import lifespan, app
        async with lifespan(app):
            pass

    def test_master_db_session_and_contextmanager(self):
        from app.db import master as db_master_mod
        with patch("app.db.master.MasterSessionLocal") as mock_local:
            mock_sess = MagicMock()
            mock_local.return_value = mock_sess
            db_inst = db_master_mod.get_master_db()
            assert db_inst is mock_sess

            with db_master_mod.get_master_session() as sess:
                assert sess is mock_sess
            mock_sess.close.assert_called_once()


# ---------------------------------------------------------------------------
# Messaging Connection, Publisher & Subscriber Unit Tests
# ---------------------------------------------------------------------------

class TestReportMessagingPublisherAndSubscriber:
    @pytest.mark.asyncio
    async def test_publish_event(self):
        mock_chan = AsyncMock()
        mock_exch = AsyncMock()
        with patch("app.messaging.publisher.get_channel", AsyncMock(return_value=mock_chan)):
            with patch("app.messaging.publisher.declare_exchange", AsyncMock(return_value=mock_exch)):
                await msg_pub_mod.publish_event("report.generated", {"id": "1"})
                mock_exch.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_consumer(self):
        mock_conn = AsyncMock()
        mock_chan = AsyncMock()
        mock_exch = AsyncMock()
        mock_queue = AsyncMock()

        async def _empty_aiter():
            if False:
                yield None

        mock_iter_cm = MagicMock()
        mock_iter_cm.__aenter__ = AsyncMock(return_value=_empty_aiter())
        mock_iter_cm.__aexit__ = AsyncMock(return_value=None)
        mock_queue.iterator = MagicMock(return_value=mock_iter_cm)

        mock_conn.channel.return_value = mock_chan
        mock_chan.declare_queue.return_value = mock_queue

        with patch("app.messaging.subscriber.get_connection", AsyncMock(return_value=mock_conn)):
            with patch("app.messaging.subscriber.declare_exchange", AsyncMock(return_value=mock_exch)):
                async def _dummy_handler(rk, body):
                    pass
                await msg_sub_mod.start_consumer("report_service", ["report.*"], _dummy_handler)
                mock_queue.bind.assert_awaited_once()


class TestReportMessagingConnection:
    @pytest.mark.asyncio
    async def test_messaging_connection_get_and_close(self):
        mock_conn = AsyncMock()
        mock_conn.is_closed = False

        with patch("aio_pika.connect_robust", new=AsyncMock(return_value=mock_conn)):
            msg_conn_mod._connection = None
            conn = await msg_conn_mod.get_connection()
            assert conn is mock_conn

            chan = await msg_conn_mod.get_channel()
            assert chan is not None

            await msg_conn_mod.close_connection()
            mock_conn.close.assert_awaited_once()
