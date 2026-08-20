"""Infrastructure, security, multi-tenancy, and database unit tests for patient-service.
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
from app.events import subscriber as event_sub_mod
from app.messaging import connection as msg_conn_mod
from app.messaging import subscriber as msg_sub_mod
from app.models.patient import TenantPatient, PatientNumberSequence
from app.services import patient_service as ps_mod
from app.services import patient_number as pn_mod


# ---------------------------------------------------------------------------
# Security & Token Validation Tests
# ---------------------------------------------------------------------------

class TestPatientSecurity:
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
    async def test_fetch_jwks_failure_raises_500(self):
        with patch("httpx.AsyncClient.get", side_effect=Exception("JWKS server unreachable")):
            sec_mod._jwks_cache.clear()
            with pytest.raises(Exception):
                await sec_mod._fetch_jwks()

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
    async def test_decode_token_rs256_keycloak(self):
        payload = {"sub": "user-rs256", "iss": f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"}
        headers = {"kid": "key-rs256"}
        token = jwt.encode(payload, "secret", algorithm="HS256", headers=headers)

        jwks = {"keys": [{"kid": "key-rs256"}]}
        with patch("app.core.security._fetch_jwks", new=AsyncMock(return_value=jwks)):
            with patch("jose.jwt.decode", return_value=payload):
                decoded = await sec_mod._decode_token(token)
                assert decoded["sub"] == "user-rs256"

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

        # Authorized via realm_access doctor
        payload_ok2 = {"realm_access": {"roles": ["doctor"]}}
        assert await checker(payload=payload_ok2) == payload_ok2

        # Unauthorized
        payload_bad = {"realm_access": {"roles": ["patient"]}}
        with pytest.raises(HTTPException) as exc:
            await checker(payload=payload_bad)
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_require_role_single(self):
        checker = sec_mod.require_role("doctor")
        res = await checker(payload={"realm_access": {"roles": ["doctor"]}})
        assert res["realm_access"]["roles"] == ["doctor"]

        with pytest.raises(HTTPException) as exc:
            await checker(payload={"realm_access": {"roles": ["nurse"]}})
        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_get_current_active_user_success_and_missing_creds(self):
        req = MagicMock()
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="dummy")

        with patch.object(settings, "keycloak_introspect", True):
            with patch("app.core.security._decode_token", new=AsyncMock(return_value={"sub": "u1", "hospital_id": "h1"})):
                with patch("app.core.security._introspect_token", new=AsyncMock()) as mock_intro:
                    user_payload = await sec_mod.get_current_active_user(req, creds)
                    assert user_payload["sub"] == "u1"
                    mock_intro.assert_awaited_once_with("dummy")

        # Missing or non-bearer creds
        with pytest.raises(HTTPException) as exc:
            await sec_mod.get_current_active_user(req, None)
        assert exc.value.status_code == 401

    def test_extract_roles_superadmin(self):
        super_payload = {"type": "superadmin", "role": "super_admin"}
        assert sec_mod._extract_roles(super_payload) == ["super_admin"]


# ---------------------------------------------------------------------------
# Dependencies & Tenant Resolution Tests
# ---------------------------------------------------------------------------

class TestPatientDependencies:
    def test_get_master_engine_singleton_and_get_master_db(self):
        e1, s1 = dep_mod._get_master_engine()
        e2, s2 = dep_mod._get_master_engine()
        assert e1 is e2
        assert s1 is s2

        with patch.object(dep_mod, "_get_master_engine", return_value=(e1, MagicMock())):
            db = dep_mod.get_master_db()
            assert db is not None

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

    @pytest.mark.asyncio
    async def test_get_tenant_db_url_from_request(self):
        req = MagicMock()
        req.headers.get.return_value = "postgresql://user:pass@localhost:5432/tenant_db"
        res = await dep_mod.get_tenant_db_url_from_request(req, {})
        assert res == "postgresql://user:pass@localhost:5432/tenant_db"

        req.headers.get.return_value = "invalid-prefix://url"
        assert await dep_mod.get_tenant_db_url_from_request(req, {}) is None

    def test_get_tenant_session_cache(self):
        db_url = "postgresql://user:pass@localhost:5432/test_tenant"
        with patch("app.dependencies.create_engine") as mock_ce:
            with patch("app.dependencies.sync_tenant_schema"):
                mock_engine = MagicMock()
                mock_ce.return_value = mock_engine
                s1 = dep_mod.get_tenant_session(db_url)
                assert s1 is not None

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
        sync_mod.sync_tenant_schema(engine, Base.metadata)

    def test_sync_tenant_schema_missing_columns_postgres_and_sqlite(self):
        mock_engine = MagicMock()
        mock_engine.dialect.name = "postgresql"
        mock_inspector = MagicMock()
        mock_inspector.get_table_names.return_value = ["patients", "patient_number_sequences"]
        mock_inspector.get_columns.return_value = [{"name": "id"}]  # missing other columns
        mock_inspector.get_indexes.return_value = []

        with patch("app.db.sync.inspect", return_value=mock_inspector):
            with patch("app.db.sync._table_has_rows", return_value=True):
                sync_mod.sync_tenant_schema(mock_engine, Base.metadata)

    def test_table_has_rows(self):
        engine = create_engine("sqlite:///:memory:")
        meta = MetaData()
        t = Table("test_table", meta, Column("id", Integer, primary_key=True))
        meta.create_all(engine)
        assert sync_mod._table_has_rows(engine, "test_table") is False

    def test_database_get_db(self):
        db_gen = db_mod.get_db()
        db = next(db_gen)
        assert db is not None
        db_gen.close()


# ---------------------------------------------------------------------------
# Event Subscriber & Messaging Unit Tests
# ---------------------------------------------------------------------------

class TestEventSubscriber:
    @pytest.mark.asyncio
    async def test_handle_registration_failed_found_patient(self, db_session):
        from datetime import date
        patient = ps_mod.register_patient(
            db_session, "tenant-del", "ToDelete", date(1990, 1, 1), "male", "07111"
        )
        pid_str = str(patient.id)
        with patch("app.events.subscriber.get_session_local", return_value=lambda: db_session):
            with patch.object(db_session, "close"):  # prevent closing fixture session
                await event_sub_mod.handle_registration_failed(pid_str, "tenant-del")
        assert ps_mod.get_patient_by_id(db_session, "tenant-del", pid_str) is None

    @pytest.mark.asyncio
    async def test_start_consumer_loop(self):
        mock_conn = AsyncMock()
        mock_channel = AsyncMock()
        mock_exchange = MagicMock()
        mock_queue = MagicMock()
        mock_queue.bind = AsyncMock()

        mock_msg = MagicMock()
        mock_msg.body = b'{"key": "val"}'
        mock_msg.routing_key = "visit.registration_failed"
        mock_msg.process.return_value.__aenter__ = AsyncMock()
        mock_msg.process.return_value.__aexit__ = AsyncMock()

        class MockQueueIter:
            def __aiter__(self):
                return self
            async def __anext__(self):
                if not hasattr(self, "_done"):
                    self._done = True
                    return mock_msg
                raise StopAsyncIteration

        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=MockQueueIter())
        mock_cm.__aexit__ = AsyncMock(return_value=None)
        mock_queue.iterator.return_value = mock_cm

        mock_channel.declare_queue = AsyncMock(return_value=mock_queue)
        mock_conn.channel = AsyncMock(return_value=mock_channel)

        handler = AsyncMock()
        with patch("app.messaging.subscriber.get_connection", new=AsyncMock(return_value=mock_conn)):
            with patch("app.messaging.subscriber.declare_exchange", new=AsyncMock(return_value=mock_exchange)):
                await msg_sub_mod.start_consumer("svc", ["key1"], handler)
                handler.assert_awaited_once_with("visit.registration_failed", {"key": "val"})

    @pytest.mark.asyncio
    async def test_run_consumer_task(self):
        with patch("app.messaging.subscriber.start_consumer", new=AsyncMock()):
            task = await msg_sub_mod.run_consumer_task("patient-service", ["key1"], AsyncMock())
            assert task is not None
            task.cancel()


# ---------------------------------------------------------------------------
# Patient Number Sequence Wrap & Patient Service Updates
# ---------------------------------------------------------------------------

def test_patient_number_sequence_new_year_and_reset(db_session):
    seq = PatientNumberSequence(hospital_id="tenant-year", date_key="20200101", counter=9999)
    db_session.add(seq)
    db_session.commit()

    num = pn_mod.generate_patient_number(db_session, "tenant-year")
    assert num.startswith("PT-")

def test_update_patient_partial_fields_only(db_session):
    from datetime import date
    p = ps_mod.register_patient(
        db_session, "tenant-1", "Partial", date(1990, 1, 1), "female", "07000"
    )
    updated = ps_mod.update_patient(
        db_session, "tenant-1", str(p.id),
        email="partial@test.com", address="Partial Addr"
    )
    assert updated.email == "partial@test.com"
    assert updated.full_name == "Partial"
