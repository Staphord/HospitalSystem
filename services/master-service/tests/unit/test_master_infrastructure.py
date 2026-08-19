"""Infrastructure, security, middleware, database, and AMQP event unit tests for master-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from jose import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import create_engine

from app.config import settings
from app.core import security as sec_mod
from app.core import database as db_mod
from app.core import middleware as mid_mod
from app.events import publisher as evt_pub_mod
from app.messaging import connection as msg_conn_mod
from app.messaging import publisher as msg_pub_mod


# ---------------------------------------------------------------------------
# Security & Auth Unit Tests
# ---------------------------------------------------------------------------

class TestMasterSecurity:
    def test_extract_realm_from_iss(self):
        valid_iss = f"{settings.keycloak_url}/realms/master-realm"
        token = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert sec_mod._extract_realm_from_iss(token) == "master-realm"

        invalid_token = "not-a-valid-jwt"
        assert sec_mod._extract_realm_from_iss(invalid_token) is None

    def test_build_rsa_key(self):
        jwks = {"keys": [{"kid": "key-master-1", "n": "xyz"}]}
        assert sec_mod._build_rsa_key(jwks, "key-master-1") == {"kid": "key-master-1", "n": "xyz"}

        with pytest.raises(HTTPException) as exc:
            sec_mod._build_rsa_key(jwks, "missing-kid")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_decode_token_local_hs256(self):
        payload = {"sub": "admin-1", "preferred_username": "superadmin", "realm_access": {"roles": ["superadmin"]}}
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        decoded = await sec_mod._decode_token(token)
        assert decoded["sub"] == "admin-1"
        assert decoded["preferred_username"] == "superadmin"

    @pytest.mark.asyncio
    async def test_require_superadmin_role(self):
        checker = sec_mod.require_role("super_admin")
        mock_user = MagicMock()
        mock_user.realm_access = {"roles": ["super_admin"]}
        mock_user.raw = {"realm_access": {"roles": ["super_admin"]}}

        res = await checker(user=mock_user)
        assert res == mock_user

        mock_bad_user = MagicMock()
        mock_bad_user.realm_access = {"roles": ["user"]}
        mock_bad_user.raw = {"realm_access": {"roles": ["user"]}}
        with pytest.raises(HTTPException) as exc:
            await checker(user=mock_bad_user)
        assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Database Core Unit Tests
# ---------------------------------------------------------------------------

class TestMasterDatabaseCore:
    def test_get_db(self):
        db_gen = db_mod.get_db()
        db = next(db_gen)
        assert db is not None
        db_gen.close()


# ---------------------------------------------------------------------------
# AMQP Publisher Unit Tests
# ---------------------------------------------------------------------------

class TestMasterEventPublisher:
    @pytest.mark.asyncio
    async def test_publish_tenant_created(self):
        with patch("app.events.publisher.publish_event", new=AsyncMock()) as mock_pub:
            await evt_pub_mod.publish_tenant_created("t1", {"name": "Hosp 1"})
            mock_pub.assert_awaited_once_with("tenant.created", {"tenant_id": "t1", "name": "Hosp 1"})

    @pytest.mark.asyncio
    async def test_publish_tenant_suspended(self):
        with patch("app.events.publisher.publish_event", new=AsyncMock()) as mock_pub:
            await evt_pub_mod.publish_tenant_suspended("t1", {"reason": "Payment expired"})
            mock_pub.assert_awaited_once_with("tenant.suspended", {"tenant_id": "t1", "reason": "Payment expired"})

    @pytest.mark.asyncio
    async def test_messaging_connection_and_close(self):
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
