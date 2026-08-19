"""Focused edge-path tests for the auth HTTP orchestration layer."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
import httpx
from fastapi import HTTPException
from jose import jwt

from app.api.v1.auth import router
from app.core.config import settings
from app.core.security import TokenPayload
from app.core.tenant_auth import TenantContext
from app.models.auth import RefreshToken
from app.models.user import User
from app.services import auth as auth_service


def req(ip="10.50.0.1"):
    from starlette.requests import Request
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": (ip, 1)})


def token_claims(**claims):
    base = {"sub": "sub", "tenant_id": "t1", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    base.update(claims)
    return jwt.encode(base, settings.secret_key, algorithm="HS256")


@pytest.mark.asyncio
async def test_first_login_failure_paths(monkeypatch, db_session):
    body = type("Body", (), {"username": "u", "temp_password": "old", "new_password": "N3w!CedarRiver"})()
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value=None))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(req(), body, db_session)
    assert exc.value.status_code == 404

    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="realm"))
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=HTTPException(401, detail="bad password")))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(req("10.50.0.2"), body, db_session)
    assert exc.value.status_code == 401

    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(req("10.50.0.3"), body, db_session)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_first_login_identity_and_final_login_errors(monkeypatch, db_session):
    body = type("Body", (), {"username": "u", "temp_password": "old", "new_password": "N3w!CedarRiver"})()
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(return_value="realm"))
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=[{}, {"access_token": token_claims(), "session_id": "s"}]))
    monkeypatch.setattr("app.services.keycloak_admin._headers", AsyncMock(return_value={}))

    class Response:
        def __init__(self, data): self.data = data
        def json(self): return self.data
        def raise_for_status(self): return None

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return None
        async def get(self, url, **kwargs): return Response([])
        async def put(self, *args, **kwargs): return Response({})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(req("10.50.0.4"), body, db_session)
    assert exc.value.status_code == 500

    class ClientOK(Client):
        async def get(self, url, **kwargs):
            if "users?" in url: return Response([{"id": "id"}])
            return Response({"requiredActions": ["UPDATE_PASSWORD"]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: ClientOK())
    monkeypatch.setattr("app.services.keycloak_admin.set_user_password", AsyncMock())
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=[{}, RuntimeError("login down")]))
    with pytest.raises(HTTPException) as exc:
        await router.first_login_change_password(req("10.50.0.5"), body, db_session)
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_mfa_setup_and_disable_missing_contexts(monkeypatch, db_session):
    plain = TokenPayload("sub", "u", None, {"roles": []}, {})
    with pytest.raises(Exception):
        await router.mfa_email_send_setup_code(req(), plain, db_session)
    with pytest.raises(Exception):
        await router.mfa_verify(req("10.50.0.6"), type("B", (), {"totp_code": "0"})(), plain, db_session)
    with pytest.raises(Exception):
        await router.mfa_disable(req("10.50.0.7"), plain, db_session)

    tenant = TokenPayload("missing", "u", None, {"roles": []}, {"tenant_id": "t1"})
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    with pytest.raises(Exception):
        await router.mfa_email_send_setup_code(req("10.50.0.8"), tenant, db_session)


@pytest.mark.asyncio
async def test_mfa_verify_invalid_code_and_disable_tenant(monkeypatch, db_session):
    record = User(keycloak_sub="mfa-sub", email="mfa@example.com", mfa_secret="JBSWY3DPEHPK3PXP")
    db_session.add(record); db_session.commit()
    tenant = TokenPayload("mfa-sub", "u", "mfa@example.com", {"roles": []}, {"tenant_id": "t1"})
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(auth_service, "get_pending_mfa_secret", lambda _: record.mfa_secret)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: False)
    with pytest.raises(Exception):
        await router.mfa_verify(req("10.50.0.9"), type("B", (), {"totp_code": "0"})(), tenant, db_session)
    record.mfa_enabled = True; db_session.commit()
    assert (await router.mfa_disable(req("10.50.0.10"), tenant, db_session))["detail"]


@pytest.mark.asyncio
async def test_session_revoked_and_other_active_paths(db_session):
    ctx = TenantContext("t", "sub", "u", "u@example.com", [], False, raw_token={"sid": "current"})
    assert (await router.check_session(req(), ctx, db_session))["session_revoked"] is True
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    db_session.add_all([
        RefreshToken(keycloak_sub="sub", session_id="current", refresh_token_hash="a", expires_at=now, is_revoked=False),
        RefreshToken(keycloak_sub="sub", session_id="other", refresh_token_hash="b", expires_at=now, is_revoked=False),
    ])
    db_session.commit()
    result = await router.check_session(req("10.50.0.11"), ctx, db_session)
    assert result == {"has_other_active": True, "session_revoked": False}
    await router.keep_only_this_session(req("10.50.0.12"), ctx, db_session)
    assert db_session.query(RefreshToken).filter(RefreshToken.session_id == "other").one().is_revoked
    no_impersonation = TokenPayload("sub", "u", None, {}, {})
    assert (await router.impersonate_exit(req("10.50.0.13"), no_impersonation))["detail"].startswith("No active")


def test_department_status_rejects_inactive_user(monkeypatch, db_session):
    from types import SimpleNamespace
    record = SimpleNamespace(is_active=False)
    query = type("Query", (), {"filter": lambda self, *args: self, "first": lambda self: record})()
    fake_db = type("DB", (), {"query": lambda self, *args: query, "close": lambda self: None})()
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: fake_db)
    with pytest.raises(HTTPException) as exc:
        router.check_user_department_status("t1", "inactive")
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_login_and_regular_login_error_branches(monkeypatch, db_session):
    body = type("Body", (), {"username": "missing", "password": "bad"})()
    monkeypatch.setattr(router, "is_blocked", lambda *args: True)
    monkeypatch.setattr(router, "get_remaining_seconds", lambda *args: 7)
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(req("10.50.0.14"), body, db_session)
    assert exc.value.status_code == 429

    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=RuntimeError("down")))
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(req("10.50.0.15"), body, db_session)
    assert exc.value.status_code == 500

    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    monkeypatch.setattr("app.services.keycloak_admin.find_user_realm_by_username", AsyncMock(side_effect=RuntimeError("lookup")))
    with pytest.raises(HTTPException) as exc:
        await router.login(req("10.50.0.16"), type("B", (), {"username": "u", "password": "p", "realm": None})(), db_session)
    assert exc.value.status_code == 500

    super_token = token_claims(realm_access={"roles": ["super_admin"]})
    monkeypatch.setattr(auth_service, "login", AsyncMock(return_value={"access_token": super_token}))
    with pytest.raises(HTTPException) as exc:
        await router.login(req("10.50.0.17"), type("B", (), {"username": "u", "password": "p", "realm": "master"})(), db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_sync_and_audit_exception_paths(monkeypatch, db_session):
    from app.models.admin import SuperAdmin
    admin = SuperAdmin(username="sync-fail", email="sync-fail@example.com", password_hash="x", full_name="Sync Fail", mfa_secret="JBSWY3DPEHPK3PXP")
    db_session.add(admin); db_session.commit()
    body = type("Body", (), {"username": "sync-fail", "password": "bad"})()
    monkeypatch.setattr(router, "is_blocked", lambda *args: False)
    monkeypatch.setattr(router, "record_failed_attempt", lambda *args: None)
    monkeypatch.setattr(router, "ensure_roles", AsyncMock(side_effect=RuntimeError("roles")))
    monkeypatch.setattr(router, "create_keycloak_user", AsyncMock(return_value="sub"))
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=HTTPException(401, detail="bad")))
    monkeypatch.setattr(router, "get_failed_attempts", lambda *args: 99)
    monkeypatch.setattr(router, "get_remaining_seconds", lambda *args: 30)
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(req("10.50.0.22"), body, db_session)
    assert exc.value.status_code == 429

    monkeypatch.setattr(auth_service, "login", AsyncMock(return_value={"access_token": "not-jwt", "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 1}))
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(req("10.50.0.23"), type("Body", (), {"username": "new", "password": "p"})(), db_session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_superadmin_non401_and_regular_login_audit_edges(monkeypatch, db_session):
    body = type("Body", (), {"username": "n", "password": "p"})()
    monkeypatch.setattr(router, "is_blocked", lambda *a: False)
    monkeypatch.setattr(auth_service, "login", AsyncMock(side_effect=HTTPException(403, detail="forbidden")))
    with pytest.raises(HTTPException) as exc:
        await router.superadmin_login(req("10.50.0.24"), body, db_session)
    assert exc.value.status_code == 403

    token = token_claims(realm_access={"roles": ["doctor", "hospital_admin"]})
    monkeypatch.setattr(auth_service, "login", AsyncMock(return_value={"access_token": token, "refresh_token": "r", "expires_in": 1, "refresh_expires_in": 1}))
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(router, "get_session_local", lambda: (_ for _ in ()).throw(RuntimeError("audit")))
    result = await router.login(req("10.50.0.25"), type("B", (), {"username": "u", "password": "p", "realm": "test"})(), db_session)
    assert result["scope"] == "full"


@pytest.mark.asyncio
async def test_mfa_verify_login_configuration_and_payload_branches(monkeypatch, db_session):
    import json
    from app.models.admin import SuperAdmin
    admin = SuperAdmin(username="mfa-branches", email="mfa-b@example.com", password_hash="x", full_name="MFA", mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=False, backup_codes=None)
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    base = {"mfa_challenge": True, "superadmin": True, "super_admin_id": str(admin.super_admin_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    for extra in ({}, {"tokens": {"access_token": "a"}}, {"tokens": "not-json"}):
        payload = dict(base, **extra)
        challenge = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        with pytest.raises(HTTPException):
            await router.mfa_verify_login(req("10.50.0.26"), type("B", (), {"challenge_token": challenge, "totp_code": "000000"})(), db_session)

    admin.mfa_enabled = True; admin.backup_codes = json.dumps(["hash"]); db_session.commit()
    monkeypatch.setattr(auth_service, "is_valid_totp_secret", lambda _: True)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: False)
    challenge = jwt.encode(dict(base, tokens=json.dumps({"access_token": "a"})), settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(auth_service, "verify_backup_code", lambda **kwargs: False)
    with pytest.raises(HTTPException):
        await router.mfa_verify_login(req("10.50.0.27"), type("B", (), {"challenge_token": challenge, "totp_code": "000000"})(), db_session)


@pytest.mark.asyncio
async def test_mfa_email_and_verify_context_errors(monkeypatch, db_session):
    invalid_context = token_claims(mfa_challenge=False)
    with pytest.raises(HTTPException):
        await router.mfa_email_send_login_code(req("10.50.0.18"), type("B", (), {"challenge_token": invalid_context})(), db_session)

    no_email = User(keycloak_sub="no-email", mfa_secret="JBSWY3DPEHPK3PXP")
    db_session.add(no_email); db_session.commit()
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    challenge = token_claims(mfa_challenge=True, tenant_id="t1", sub="no-email")
    with pytest.raises(HTTPException):
        await router.mfa_email_send_login_code(req("10.50.0.19"), type("B", (), {"challenge_token": challenge})(), db_session)

    missing_super_id = token_claims(mfa_challenge=True, superadmin=True)
    with pytest.raises(HTTPException):
        await router.mfa_verify_login(req("10.50.0.20"), type("B", (), {"challenge_token": missing_super_id, "totp_code": "000000"})(), db_session)

    bad_record = token_claims(mfa_challenge=True, tenant_id="t1", sub="no-email", tokens="{}")
    with pytest.raises(HTTPException):
        await router.mfa_verify_login(req("10.50.0.21"), type("B", (), {"challenge_token": bad_record, "totp_code": "000000"})(), db_session)


@pytest.mark.asyncio
async def test_auth_email_keycloak_and_mfa_cache_edges(monkeypatch):
    monkeypatch.setattr(auth_service.settings, "smtp_user", None)
    monkeypatch.setattr(auth_service.settings, "smtp_password", None)
    await auth_service._send_email("u@example.com", "https://reset")
    auth_service._totp_secrets.clear()
    first = auth_service.generate_mfa_secret("cache-sub")
    assert auth_service.generate_mfa_secret("cache-sub") == first
    auth_service.clear_pending_mfa_secret("cache-sub")
    assert auth_service.get_pending_mfa_secret("cache-sub") is None
    assert auth_service.verify_mfa_totp("cache-sub", "000000") is False
    assert auth_service.is_valid_totp_secret("not-a-valid-secret") is False

    class Response:
        is_success = True
        def json(self): return [{"id": "u"}]
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def get(self, *a, **k): return Response()
    monkeypatch.setattr(auth_service, "_get_admin_token", AsyncMock(return_value="token"))
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", lambda **k: Client())
    assert await auth_service._user_exists_in_keycloak("u@example.com") is True


def test_superadmin_auth_remaining_errors():
    from app.services import superadmin_auth
    with pytest.raises(Exception):
        superadmin_auth.decode_superadmin_token(superadmin_auth.create_access_token("id", "u", "operator").replace("ey", "ex", 1))
    wrong = jwt.encode({"type": "other"}, settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception):
        superadmin_auth.decode_superadmin_token(wrong)


def test_refresh_record_update_optional_fields(db_session):
    auth_service._store_refresh_token(db_session, "s", "session", "r", 60, "realm")
    auth_service._store_refresh_token(db_session, "s2", "session", "r2", 120, None, "ip", "agent")
    record = db_session.query(__import__("app.models.auth", fromlist=["RefreshToken"]).RefreshToken).one()
    assert record.keycloak_sub == "s2" and record.ip_address == "ip"
