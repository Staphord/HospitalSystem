"""Infrastructure, security, multi-tenancy, database sync, and AMQP event tests for visit-service.
"""
from unittest.mock import AsyncMock, MagicMock, patch
from jose import jwt
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import Column, Integer, MetaData, String, Table, create_engine

from app.config import settings
from app.core import security as sec_mod
from app.core import database as db_mod
from app import dependencies as dep_mod
from app.db import sync as sync_mod
from app.db.base import Base
from app.events import publisher as evt_pub_mod
from app.messaging import connection as msg_conn_mod
from app.messaging import publisher as msg_pub_mod
from app.messaging import subscriber as msg_sub_mod


# ---------------------------------------------------------------------------
# Security & Token Validation Tests
# ---------------------------------------------------------------------------

class TestVisitSecurity:
    def test_extract_realm_from_iss(self):
        valid_iss = f"{settings.keycloak_url}/realms/hospital-realm"
        token = jwt.encode({"iss": valid_iss}, "secret", algorithm="HS256")
        assert sec_mod._extract_realm_from_iss(token) == "hospital-realm"

        invalid_token = "invalid-jwt"
        assert sec_mod._extract_realm_from_iss(invalid_token) is None

    def test_build_rsa_key(self):
        jwks = {"keys": [{"kid": "key-1", "n": "abc"}]}
        assert sec_mod._build_rsa_key(jwks, "key-1") == {"kid": "key-1", "n": "abc"}

        with pytest.raises(HTTPException) as exc:
            sec_mod._build_rsa_key(jwks, "missing-key")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_fetch_jwks_success_and_cache(self):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"keys": [{"kid": "k1", "n": "abc"}]}
        mock_resp.raise_for_status.return_value = None

        with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
            sec_mod._jwks_cache.clear()
            jwks = await sec_mod._fetch_jwks()
            assert jwks == {"keys": [{"kid": "k1", "n": "abc"}]}

            # Second call uses cache
            jwks_cached = await sec_mod._fetch_jwks()
            assert jwks_cached == jwks

    @pytest.mark.asyncio
    async def test_introspect_token_active_and_inactive(self):
        mock_resp_active = MagicMock()
        mock_resp_active.json.return_value = {"active": True}
        mock_resp_active.raise_for_status.return_value = None

        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp_active)):
            await sec_mod._introspect_token("valid_token")

        mock_resp_inactive = MagicMock()
        mock_resp_inactive.json.return_value = {"active": False}
        with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_resp_inactive)):
            with pytest.raises(HTTPException) as exc:
                await sec_mod._introspect_token("expired_token")
            assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_decode_token_local_hs256(self):
        payload = {"sub": "user-123", "hospital_id": "tenant-1", "realm_access": {"roles": ["receptionist"]}}
        token = jwt.encode(payload, settings.secret_key, algorithm="HS256")

        decoded = await sec_mod._decode_token(token)
        assert decoded["sub"] == "user-123"
        assert decoded["hospital_id"] == "tenant-1"

    @pytest.mark.asyncio
    async def test_decode_token_invalid_token_raises_401(self):
        with pytest.raises(HTTPException) as exc:
            await sec_mod._decode_token("invalid.jwt.token")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_require_any_role_authorized_and_unauthorized(self):
        checker = sec_mod.require_any_role(["doctor", "receptionist"])

        # Authorized via realm_access
        payload_ok = {"realm_access": {"roles": ["receptionist"]}}
        res = await checker(payload=payload_ok)
        assert res == payload_ok

        # Unauthorized
        payload_bad = {"realm_access": {"roles": ["patient"]}}
        with pytest.raises(HTTPException) as exc:
            await checker(payload=payload_bad)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_current_active_user(self):
        req = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy")

        with patch("app.core.security._decode_token", new=AsyncMock(return_value={"sub": "u1", "hospital_id": "h1"})):
            user_payload = await sec_mod.get_current_active_user(req, creds)
            assert user_payload["sub"] == "u1"

        # Missing creds
        with pytest.raises(HTTPException) as exc:
            await sec_mod.get_current_active_user(req, None)
        assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# Dependencies & Tenant Resolution Tests
# ---------------------------------------------------------------------------

class TestVisitDependencies:
    def test_get_master_engine_singleton(self):
        e1, s1 = dep_mod._get_master_engine()
        e2, s2 = dep_mod._get_master_engine()
        assert e1 is e2
        assert s1 is s2

    def test_resolve_tenant_db_url_success_and_missing(self):
        cipher = Fernet(settings.tenant_db_encryption_key.encode())
        encrypted_url = cipher.encrypt(b"sqlite:///:memory:").decode()

        mock_db = MagicMock()
        mock_row = MagicMock()
        mock_row.scalar.return_value = encrypted_url
        mock_db.execute.return_value = mock_row

        with patch.object(dep_mod, "get_master_db", return_value=mock_db):
            resolved = dep_mod.resolve_tenant_db_url("tenant-1")
            assert resolved == "sqlite:///:memory:"

        # Missing tenant
        mock_row.scalar.return_value = None
        with patch.object(dep_mod, "get_master_db", return_value=mock_db):
            assert dep_mod.resolve_tenant_db_url("tenant-missing") is None

    @pytest.mark.asyncio
    async def test_get_tenant_id_from_token_success_and_missing(self):
        req = MagicMock()
        payload_ok = {"hospital_id": "tenant-1"}
        tid = await dep_mod.get_tenant_id_from_token(req, payload_ok)
        assert tid == "tenant-1"

        payload_missing = {}
        with pytest.raises(HTTPException) as exc:
            await dep_mod.get_tenant_id_from_token(req, payload_missing)
        assert exc.value.status_code == 403

    def test_get_tenant_db_yields_and_closes(self):
        mock_session = MagicMock()
        with patch("app.dependencies.get_tenant_session", return_value=mock_session):
            db_gen = dep_mod.get_tenant_db(x_tenant_db="postgresql://localhost/t1", tenant_id="t1")
            session = next(db_gen)
            assert session is mock_session
            with pytest.raises(StopIteration):
                next(db_gen)
            mock_session.close.assert_called_once()

    def test_get_tenant_db_resolution_failure_raises_400(self):
        with patch.object(dep_mod, "resolve_tenant_db_url", return_value=None):
            with pytest.raises(HTTPException) as exc:
                list(dep_mod.get_tenant_db(x_tenant_db=None, tenant_id="bad-tenant"))
            assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Database & Sync Schema Tests
# ---------------------------------------------------------------------------

class TestDatabaseAndSync:
    def test_sync_tenant_schema(self):
        engine = create_engine("sqlite:///:memory:")
        with patch("app.db.sync._ensure_enum_types"):
            sync_mod.sync_tenant_schema(engine, Base.metadata)

    def test_database_get_db(self):
        db_gen = db_mod.get_db()
        db = next(db_gen)
        assert db is not None
        db_gen.close()


# ---------------------------------------------------------------------------
# Event Publisher & AMQP Messaging Tests
# ---------------------------------------------------------------------------

class TestEventPublisherAndMessaging:
    @pytest.mark.asyncio
    async def test_publish_visit_created(self):
        with patch("app.events.publisher.publish_event", new=AsyncMock()) as mock_pub:
            await evt_pub_mod.publish_visit_created("v1", "p1", "t1")
            mock_pub.assert_awaited_once_with("visit.created", {
                "visit_id": "v1",
                "patient_id": "p1",
                "tenant_id": "t1",
            })

    @pytest.mark.asyncio
    async def test_messaging_publisher_publish_event(self):
        mock_channel = AsyncMock()
        mock_exchange = AsyncMock()

        with patch("app.messaging.publisher.get_channel", new=AsyncMock(return_value=mock_channel)):
            with patch("app.messaging.publisher.declare_exchange", new=AsyncMock(return_value=mock_exchange)):
                await msg_pub_mod.publish_event("visit.created", {"data": "test"})
                mock_exchange.publish.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_connection_and_close(self):
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
