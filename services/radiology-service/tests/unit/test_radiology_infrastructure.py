"""Infrastructure, security, middleware, and messaging unit tests for radiology-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from jose import jwt
import pytest
from fastapi import HTTPException

from app.core import config as cfg_mod
from app.core import middleware as mid_mod
from app.core import tenant_auth as ta_mod
from app.core import security as sec_mod
from app.core import database as db_mod
from app.messaging import connection as msg_conn_mod
from app.messaging import publisher as msg_pub_mod


# ---------------------------------------------------------------------------
# Config and Security Unit Tests
# ---------------------------------------------------------------------------

class TestRadiologyConfigAndSecurity:
    def test_config_loaded(self):
        assert cfg_mod.settings is not None
        assert cfg_mod.settings.secret_key is not None

    def test_extract_realm_from_iss(self):
        valid_iss = f"{cfg_mod.settings.keycloak_url}/realms/hospital-realm"
        tok = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert ta_mod._extract_realm_from_iss(tok) == "hospital-realm"

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
    async def test_get_current_active_user(self):
        sec_mod._jwks_cache["jwks:hospital"] = {"keys": []}
        payload = {"sub": "rad-user-1", "iss": "http://localhost:8080/realms/hospital", "preferred_username": "radiographer", "realm_access": {"roles": ["radiographer"]}}
        tok = jwt.encode(payload, cfg_mod.settings.secret_key, algorithm="HS256")
        creds = MagicMock(scheme="bearer", credentials=tok)
        req = MagicMock()

        user = await sec_mod.get_current_active_user(req, credentials=creds)
        assert user.sub == "rad-user-1"


# ---------------------------------------------------------------------------
# Middleware Dispatch Unit Tests
# ---------------------------------------------------------------------------

class TestRadiologyMiddlewareDispatch:
    @pytest.mark.asyncio
    async def test_audit_log_middleware(self):
        req = MagicMock(method="POST")
        req.url.path = "/api/v1/radiology/requests"
        req.state.user_sub = "u1"
        req.state.tenant = MagicMock(tenant_id="t1")

        mock_db = MagicMock()
        with patch("app.core.middleware.get_session_local", return_value=MagicMock(return_value=mock_db)):
            resp_mock = MagicMock(headers={}, status_code=200)
            call_next = AsyncMock(return_value=resp_mock)
            mw = mid_mod.AuditLogMiddleware(MagicMock())
            resp = await mw.dispatch(req, call_next)
            assert resp.headers["X-Request-ID"] is not None


# ---------------------------------------------------------------------------
# Messaging Publisher Unit Tests
# ---------------------------------------------------------------------------

class TestRadiologyMessagingPublisher:
    @pytest.mark.asyncio
    async def test_publish_event(self):
        mock_chan = AsyncMock()
        mock_exch = AsyncMock()
        with patch("app.messaging.publisher.get_channel", AsyncMock(return_value=mock_chan)):
            with patch("app.messaging.publisher.declare_exchange", AsyncMock(return_value=mock_exch)):
                await msg_pub_mod.publish_event("radiology.report_ready", {"id": "1"})
                mock_exch.publish.assert_awaited_once()
