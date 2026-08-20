from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi import Request
from jose import jwt

from app.api.v1.admin.schemas import HospitalUserCreate, HospitalUserUpdate
from app.config import settings
from app.core.limiter import _rate_limit_key
from app.core.security import (
    TokenPayload,
    _build_rsa_key,
    _decode_token,
    _extract_realm_from_iss,
    _extract_roles,
    _issuer,
    require_role,
)
from app.core import security as security_module
from app.core import tenant_auth as tenant_auth_module
from app.services.backup import _dsn_to_pg_env, _tenant_backup_dir, resolve_download_path
from app.services.mail import login_url_from_request
from app.services.reports import (
    _parse_range,
    bed_occupancy,
    dashboard,
    discharges,
    operational_activity,
    patient_census,
    revenue_summary,
    wait_times,
)
from app.services.roles import keycloak_roles_for, validate_assignable_role
from app.services.sessions import _device_from_ua
from app.services import tenant_service
from app.services import audit_service, settings_service


def test_user_schema_validates_and_sanitizes_values():
    user = HospitalUserCreate(
        username="  nurse_1 ",
        password="SecurePass1!",
        email="nurse@example.com",
        role="nurse",
    )
    assert user.username == "nurse_1"

    with pytest.raises(ValueError):
        HospitalUserCreate(
            username="ab",
            password="weak",
            email="not-email",
            role="nurse",
        )

    with pytest.raises(ValueError):
        HospitalUserUpdate(password="weak")
    assert HospitalUserUpdate(username="  nurse_2 ", password=None).username == "nurse_2"


def test_role_mapping_and_assignability_boundaries():
    assert keycloak_roles_for("hospital_user") == ["hospital_user"]
    assert keycloak_roles_for("patient") == ["patient", "hospital_user"]
    assert keycloak_roles_for("custom_role") == ["custom_role", "hospital_user"]
    validate_assignable_role("doctor")
    with pytest.raises(HTTPException):
        validate_assignable_role("super_admin")


def test_security_helpers_and_hs256_token_paths():
    assert _issuer("tenant") == f"{settings.keycloak_url}/realms/tenant"
    token = jwt.encode(
        {"sub": "user-1", "realm_access": {"roles": ["doctor"]}},
        settings.secret_key,
        algorithm="HS256",
    )
    payload = __import__("asyncio").run(_decode_token(token))
    assert payload["sub"] == "user-1"
    assert _extract_roles(TokenPayload("u", None, None, {"roles": ["doctor"]}, {})) == ["doctor"]
    assert _extract_roles(TokenPayload("u", None, None, {}, {"type": "superadmin", "role": "super_admin"})) == ["super_admin"]

    malformed = jwt.encode({"iss": "https://keycloak.example/realms/hospital/"}, "x", algorithm="HS256")
    assert _extract_realm_from_iss(malformed) == "hospital"
    assert _build_rsa_key({"keys": [{"kid": "k1"}]}, "k1")["kid"] == "k1"
    with pytest.raises(HTTPException):
        _build_rsa_key({"keys": []}, "missing")


@pytest.mark.asyncio
async def test_role_dependency_allows_expected_role_and_superadmin():
    dependency = require_role("doctor")
    doctor = TokenPayload("u", None, None, {"roles": ["doctor"]}, {})
    assert await dependency(doctor) is doctor

    superadmin = TokenPayload("u", None, None, {}, {"type": "superadmin", "role": "super_admin"})
    assert await dependency(superadmin) is superadmin

    with pytest.raises(HTTPException):
        await dependency(TokenPayload("u", None, None, {"roles": ["nurse"]}, {}))


@pytest.mark.asyncio
async def test_security_invalid_expired_and_jwks_paths():
    with pytest.raises(HTTPException):
        await security_module._decode_token("not-a-jwt")
    expired = jwt.encode({"sub": "u", "exp": 1}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        await security_module._decode_token(expired)
    assert exc.value.status_code == 401

    class JwksClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, *args, **kwargs): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": []})

    security_module._jwks_cache.clear()
    with patch("httpx.AsyncClient", return_value=JwksClient()):
        assert await security_module._fetch_jwks("realm-x") == {"keys": []}

    class IntrospectClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *args, **kwargs): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"active": False})

    security_module._introspection_cache.clear()
    with patch("httpx.AsyncClient", return_value=IntrospectClient()):
        with pytest.raises(HTTPException):
            await security_module._introspect_token("inactive")


@pytest.mark.asyncio
async def test_tenant_auth_context_paths():
    request = SimpleNamespace(state=SimpleNamespace())
    creds = SimpleNamespace(scheme="Bearer", credentials="token")
    with patch.object(tenant_auth_module, "_decode_token", new_callable=AsyncMock, return_value={
        "type": "superadmin", "super_admin_id": "sa", "username": "root", "role": "super_admin"
    }):
        ctx = await tenant_auth_module.get_current_tenant(request, creds)
    assert ctx.is_super_admin is True and ctx.tenant_id is None

    with patch.object(tenant_auth_module, "_decode_token", new_callable=AsyncMock, return_value={"sub": "u", "realm_access": {"roles": []}}):
        with pytest.raises(HTTPException):
            await tenant_auth_module.get_current_tenant(request, creds)


def test_report_range_and_database_fallbacks():
    start, end = _parse_range(date(2026, 1, 1), date(2026, 1, 2))
    assert (start, end) == (date(2026, 1, 1), date(2026, 1, 2))
    with pytest.raises(HTTPException):
        _parse_range(date(2026, 2, 1), date(2026, 1, 1))
    with pytest.raises(HTTPException):
        _parse_range(date(2020, 1, 1), date(2026, 1, 1))

    db = MagicMock()
    db.execute.side_effect = RuntimeError("database unavailable")
    census = patient_census(db, date(2026, 1, 1), date(2026, 1, 2))
    assert census["active_patients"] == 0
    assert census["visits_by_day"] == []
    assert wait_times(db, date(2026, 1, 1), date(2026, 1, 2))["by_queue_type"] == []
    fallback = discharges(db, date(2026, 1, 1), date(2026, 1, 2))
    assert fallback["source"] == "visits"


def test_report_rows_and_dashboard_aggregation():
    db = MagicMock()
    first = MagicMock()
    first.scalar.side_effect = [12, 7]
    first.fetchall.side_effect = [
        [(date(2026, 1, 1), 3)],
        [("doctor", 10.5, 2)],
        [("completed", 4), ("cancelled", 1)],
    ]
    db.execute.side_effect = [first, first, first, first]
    assert patient_census(db, date(2026, 1, 1), date(2026, 1, 2))["total_visits"] == 3

    db.execute.side_effect = [SimpleNamespace(fetchall=lambda: [("doctor", 10.5, 2)])]
    assert wait_times(db, date(2026, 1, 1), date(2026, 1, 2))["by_queue_type"][0]["samples"] == 2

    db.execute.side_effect = [SimpleNamespace(scalar=lambda: 5)]
    assert discharges(db, date(2026, 1, 1), date(2026, 1, 2))["source"] == "admissions"

    db.execute.side_effect = [SimpleNamespace(fetchall=lambda: [("Cash", 100), ("Insurance", 50)])]
    revenue = revenue_summary(db, date(2026, 1, 1), date(2026, 1, 2))
    assert revenue["total_revenue"] == 150
    assert len(revenue["breakdown"]) == 5

    db.execute.side_effect = [SimpleNamespace(scalar=lambda: 4), SimpleNamespace(fetchall=lambda: [("u1", "Jane Doe", "doctor", 5, 3)])]
    activity = operational_activity(db, date(2026, 1, 1), date(2026, 1, 2))
    assert activity["staff_activities"][0]["initials"] == "JD"

    with patch("app.services.reports.beds_summary", return_value={"total": 4, "available": 2, "occupied": 2}):
        db.execute.side_effect = None
        db.execute.return_value.fetchall.return_value = [("Ward A", 4, 2)]
        assert bed_occupancy(db)["by_ward"][0]["occupied"] == 2

    db.query.return_value.filter.return_value.count.return_value = 3
    db.execute.side_effect = [SimpleNamespace(scalar=lambda: 2), SimpleNamespace(scalar=lambda: 1)]
    with patch("app.services.reports.beds_summary", return_value={"total": 4, "available": 2, "occupied": 2}):
        assert dashboard(db)["active_users"] == 3


def test_report_optional_query_failures_and_empty_values():
    db = MagicMock()
    db.execute.side_effect = [RuntimeError("los"), RuntimeError("audit")]
    activity = operational_activity(db, date(2026, 1, 1), date(2026, 1, 2))
    assert activity["avg_length_of_stay_days"] == 0.0 and activity["staff_activities"] == []
    db.execute.side_effect = [SimpleNamespace(scalar=lambda: None), RuntimeError("audit")]
    assert operational_activity(db, date(2026, 1, 1), date(2026, 1, 2))["avg_length_of_stay_days"] == 0.0
    db.execute.side_effect = [RuntimeError("payments")]
    assert revenue_summary(db, date(2026, 1, 1), date(2026, 1, 2))["total_revenue"] == 0.0
    db.execute.side_effect = [RuntimeError("beds")]
    with patch("app.services.reports.beds_summary", return_value={"total": 0}):
        assert bed_occupancy(db)["by_ward"] == []
    db.execute.side_effect = [RuntimeError("visits"), RuntimeError("queues")]
    assert dashboard(db)["visits_today"] == 0
    db.execute.side_effect = [RuntimeError("admissions"), SimpleNamespace(fetchall=lambda: [("completed", 2), ("discharged", 1)])]
    assert discharges(db, date(2026, 1, 1), date(2026, 1, 2))["completed"] == 2
    db = MagicMock(); db.execute.return_value.one_or_none.return_value = ("active", None)
    assert __import__("asyncio").run(tenant_service.check_tenant_subscription(db, "tenant")) == "active"


def test_backup_path_and_dsn_helpers(tmp_path: Path):
    with patch.object(settings, "backup_root", str(tmp_path)):
        assert _tenant_backup_dir("tenant-1") == tmp_path / "tenant-1"
        with pytest.raises(HTTPException):
            _tenant_backup_dir("../escape")

        job = SimpleNamespace(status="pending", file_path=None, tenant_id="tenant-1")
        with pytest.raises(HTTPException):
            resolve_download_path(job)

    env = _dsn_to_pg_env("postgresql://dbuser:secret@db.example:5433/hospital")
    assert env["PGHOST"] == "db.example"
    assert env["PGPORT"] == "5433"
    assert env["PGUSER"] == "dbuser"
    assert env["PGPASSWORD"] == "secret"
    assert env["PGDATABASE"] == "hospital"


def test_mail_url_and_device_detection():
    request = Request({"type": "http", "headers": [(b"referer", b"https://hospital.example/admin")]})
    assert login_url_from_request(request) == "https://hospital.example/login"
    request = Request({"type": "http", "headers": [(b"origin", b"https://hospital.example/")]})
    assert login_url_from_request(request) == "https://hospital.example/login"
    request = Request({"type": "http", "headers": []})
    assert login_url_from_request(request).endswith("/login")

    assert _device_from_ua(None) == "Web Browser"
    assert _device_from_ua("Mozilla iPhone") == "iPhone"
    assert _device_from_ua("Mozilla Android") == "Android Device"
    assert _device_from_ua("Mozilla Windows") == "Windows PC"
    assert _device_from_ua("Mozilla Macintosh") == "Mac"
    assert _device_from_ua("Mozilla Linux") == "Linux PC"


def test_tenant_dsn_encryption_and_subscription_states():
    encrypted = tenant_service.encrypt_dsn("postgresql://tenant-db/hospital")
    assert encrypted != "postgresql://tenant-db/hospital"
    assert tenant_service.decrypt_dsn(encrypted) == "postgresql://tenant-db/hospital"

    db = MagicMock()
    db.execute.return_value.one_or_none.return_value = None
    assert __import__("asyncio").run(tenant_service.check_tenant_subscription(db, "missing")) == "not_found"

    db.execute.return_value.one_or_none.return_value = ("suspended", None)
    assert __import__("asyncio").run(tenant_service.check_tenant_subscription(db, "tenant")) == "suspended"

    db.execute.return_value.one_or_none.return_value = (
        "active", __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - timedelta(days=1)
    )
    assert __import__("asyncio").run(tenant_service.check_tenant_subscription(db, "tenant")) == "expired"


@pytest.mark.asyncio
async def test_tenant_redis_cache_failures_are_safe():
    tenant_service._redis = None
    redis_client = AsyncMock()
    with patch.object(tenant_service.aioredis, "from_url", return_value=redis_client):
        assert await tenant_service._get_redis() is redis_client
        assert await tenant_service._get_redis() is redis_client
    with patch.object(tenant_service, "_get_redis", side_effect=RuntimeError("redis down")):
        assert await tenant_service.is_tenant_suspended("tenant") is False
        await tenant_service.cache_tenant_suspension("tenant")
        await tenant_service.remove_tenant_suspension_cache("tenant")

    redis = AsyncMock()
    redis.get.return_value = "1"
    with patch.object(tenant_service, "_get_redis", return_value=redis):
        assert await tenant_service.is_tenant_suspended("tenant") is True
        await tenant_service.cache_tenant_suspension("tenant", ttl=10)
        await tenant_service.remove_tenant_suspension_cache("tenant")
    redis.set.assert_awaited_once()
    redis.delete.assert_awaited_once()
    redis.get.return_value = None
    with patch.object(tenant_service, "_get_redis", return_value=redis):
        assert await tenant_service.is_tenant_suspended("tenant") is False
    class NoIdClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *a, **k): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"access_token": "a"})
        async def get(self, *a, **k): return SimpleNamespace(is_success=True, json=lambda: [{"name": "no-id"}])
    with patch.object(tenant_service.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient", return_value=NoIdClient()):
        await tenant_service._revoke_keycloak_sessions("tenant")
    class InactiveClient(NoIdClient):
        async def get(self, *a, **k): return SimpleNamespace(is_success=False, json=lambda: [])
    with patch.object(tenant_service.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient", return_value=InactiveClient()):
        await tenant_service._revoke_keycloak_sessions("tenant")


@pytest.mark.asyncio
async def test_tenant_status_updates_and_dsn_resolution():
    db = MagicMock()
    db.execute.return_value.one_or_none.return_value = ("active", None, 1)
    assert await tenant_service.check_and_update_tenant_status(db, "tenant") == "active"

    db.execute.return_value.one_or_none.return_value = ("suspended", None, 1)
    with patch.object(tenant_service, "cache_tenant_suspension", new_callable=AsyncMock):
        assert await tenant_service.check_and_update_tenant_status(db, "tenant") == "suspended"

    db.execute.return_value.one_or_none.return_value = (
        "active", __import__("datetime").datetime.now(__import__("datetime").timezone.utc) - timedelta(days=1), 1
    )
    assert await tenant_service.check_and_update_tenant_status(db, "tenant") == "expired"

    encrypted = tenant_service.encrypt_dsn("postgresql://db/tenant")
    db.execute.return_value.scalar.return_value = encrypted
    assert await tenant_service.get_tenant_db_dsn(db, "tenant") == "postgresql://db/tenant"
    db.execute.return_value.scalar.side_effect = [None]
    with patch.object(tenant_service.settings, "database_url", "postgresql://master"):
        assert await tenant_service.get_tenant_db_dsn(db, "default") == "postgresql://master"
    db.execute.return_value.scalar.side_effect = [None, encrypted]
    assert await tenant_service.get_tenant_db_dsn(db, "other") == "postgresql://db/tenant"
    class FailingClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *a, **k): raise RuntimeError("keycloak")
    with patch.object(tenant_service.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient", return_value=FailingClient()):
        await tenant_service._revoke_keycloak_sessions("tenant")
    db.execute.return_value.scalar.side_effect = None
    db.execute.return_value.scalar.return_value = None
    with patch.object(tenant_service.settings, "database_url", "postgresql://fallback/db"):
        assert await tenant_service.get_tenant_db_dsn(db, "tenant") == "postgresql://fallback/db"


def test_audit_and_settings_services():
    db = MagicMock()
    row = audit_service.log_change(
        db,
        user_id="actor",
        action="UPDATE",
        table_name="users",
        record_id="u1",
        new_values={"role": "doctor"},
    )
    assert row is not None
    audit_query = db.query.return_value
    audit_query.filter.return_value = audit_query
    audit_query.order_by.return_value = audit_query
    audit_query.offset.return_value = audit_query
    audit_query.limit.return_value = audit_query
    audit_query.all.return_value = [row]
    audit_query.count.return_value = 1
    rows, total = audit_service.list_audit_logs(db, user_id="actor", action="UPDATE", table_name="users", limit=300)
    assert rows == [row] and total == 1
    assert audit_service.get_audit_log(db, __import__("uuid").uuid4()) is not None

    settings_db = MagicMock()
    inspector = MagicMock()
    inspector.get_table_names.return_value = ["hospital_settings"]
    settings_db.get_bind.return_value = object()
    with patch("app.services.settings_service.inspect", return_value=inspector):
        settings_db.query.return_value.order_by.return_value.all.return_value = []
        assert settings_service.list_settings(settings_db) == []

    with patch.object(settings_service.audit_service, "log_change"):
        settings_db.query.return_value.filter.return_value.first.return_value = None
        settings_db.query.return_value.order_by.return_value.all.return_value = []
        assert settings_service.upsert_settings(settings_db, {"timezone": "Africa/Dar", "": "ignored"}, actor_sub="actor") == []
