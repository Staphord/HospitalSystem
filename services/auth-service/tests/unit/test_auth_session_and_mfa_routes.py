from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from jose import jwt
from starlette.requests import Request

from app.api.v1.auth import router
from app.api.v1.auth.schemas import (
    LogoutRequest, MFASetupResponse, PasswordResetConfirm, PasswordResetRequest,
    RefreshRequest,
)
from app.core.config import settings
from app.core.security import TokenPayload
from app.core.tenant_auth import TenantContext
from app.models.auth import RefreshToken
from app.models.admin import SuperAdmin
from app.models.user import User
from app.services import auth as auth_service


def req(method="POST", ip="127.0.0.1"):
    return Request({"type": "http", "method": method, "path": "/", "headers": [(b"x-request-id", b"r1")], "client": (ip, 1)})


def user(roles=None):
    return TokenPayload("sub", "user", "u@example.com", {"roles": roles or []}, {})


@pytest.mark.asyncio
async def test_refresh_reset_and_logout_routes(monkeypatch, db_session):
    monkeypatch.setattr(auth_service, "refresh_access_token", AsyncMock(return_value={"access_token": "a", "refresh_token": "r"}))
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=False))
    result = await router.refresh(req(), RefreshRequest(refresh_token="r"), db_session)
    assert result["scope"] == "full"

    monkeypatch.setattr(auth_service, "request_password_reset", AsyncMock())
    assert (await router.password_reset_request(req(), PasswordResetRequest(email="u@example.com"), db_session))["detail"]
    monkeypatch.setattr(auth_service, "confirm_password_reset", AsyncMock())
    assert (await router.password_reset_confirm(req(), PasswordResetConfirm(token="t", new_password="N3w!CedarRiver"), db_session))["detail"]

    monkeypatch.setattr(auth_service, "logout", AsyncMock())
    result = await router.logout(req(), LogoutRequest(refresh_token="r"), db_session, user("doctor"))
    assert result is None
    monkeypatch.setattr(auth_service, "revoke_all_sessions", AsyncMock(return_value=2))
    result = await router.logout_all(req(ip="127.0.0.201"), db_session, user("doctor"))
    assert result is None


@pytest.mark.asyncio
async def test_mfa_setup_and_impersonation_routes(monkeypatch, db_session):
    result = await router.mfa_setup(req(ip="127.0.0.202"), user())
    assert result["secret"] and result["qr_code_url"]

    admin = user(["super_admin"])
    monkeypatch.setattr(router, "create_impersonation_token", lambda **kwargs: {"access_token": "x", "tenant_id": "t"})
    monkeypatch.setattr(router, "log_impersonation_event", AsyncMock())
    result = await router.impersonate(req(ip="127.0.0.203"), type("B", (), {"target_tenant_id": "t"})(), admin)
    assert result["access_token"] == "x"
    monkeypatch.setattr(router, "log_impersonation_event", AsyncMock())
    result = await router.impersonate_exit(req(ip="127.0.0.204"), admin.__class__(admin.sub, admin.preferred_username, admin.email, admin.realm_access, {"tenant_id": "t"}))
    assert result["detail"] == "Impersonation session exited"


@pytest.mark.asyncio
async def test_mfa_email_verify_and_disable_for_superadmin(monkeypatch, db_session):
    import pyotp
    admin = SuperAdmin(username="mfa-admin", email="mfa@example.com", password_hash="x", full_name="MFA Admin", mfa_secret=pyotp.random_base32())
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    ctx = user(["super_admin"])
    ctx = TokenPayload(admin.super_admin_id, "mfa-admin", "mfa@example.com", {"roles": ["super_admin"]}, {})
    monkeypatch.setattr(auth_service, "send_mfa_email_code", AsyncMock())
    result = await router.mfa_email_send_setup_code(req(ip="127.0.0.91"), ctx, db_session)
    assert result["detail"]
    monkeypatch.setattr(auth_service, "get_pending_mfa_secret", lambda _: admin.mfa_secret)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: True)
    result = await router.mfa_verify(req(ip="127.0.0.92"), type("B", (), {"totp_code": "123456"})(), ctx, db_session)
    assert len(result["backup_codes"]) == 10
    result = await router.mfa_disable(req(ip="127.0.0.93"), ctx, db_session)
    assert result["detail"].startswith("Two-factor")


@pytest.mark.asyncio
async def test_mfa_tenant_account_paths(monkeypatch, db_session):
    import pyotp
    record = User(keycloak_sub="tenant-sub", email="tenant@example.com", username="tenant", mfa_secret=pyotp.random_base32())
    db_session.add(record); db_session.commit()
    ctx = TokenPayload("tenant-sub", "tenant", None, {"roles": ["doctor"]}, {"tenant_id": "t1"})
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(auth_service, "send_mfa_email_code", AsyncMock())
    assert (await router.mfa_email_send_setup_code(req(ip="127.0.0.94"), ctx, db_session))["detail"]
    monkeypatch.setattr(auth_service, "get_pending_mfa_secret", lambda _: record.mfa_secret)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: True)
    result = await router.mfa_verify(req(ip="127.0.0.95"), type("B", (), {"totp_code": "123456"})(), ctx, db_session)
    assert result["backup_codes"]
    assert (await router.mfa_disable(req(ip="127.0.0.88"), ctx, db_session))["detail"]


@pytest.mark.asyncio
async def test_mfa_verify_login_superadmin_success(db_session, monkeypatch):
    import json
    import pyotp
    admin = SuperAdmin(username="login-mfa", email="login-mfa@example.com", password_hash="x", full_name="Login MFA", mfa_secret=pyotp.random_base32(), mfa_enabled=True, backup_codes="[\"hash\"]")
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    challenge = jwt.encode({
        "mfa_challenge": True,
        "superadmin": True,
        "super_admin_id": str(admin.super_admin_id),
        "tokens": json.dumps({"access_token": "a", "refresh_token": "r"}),
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
    }, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(auth_service, "is_valid_totp_secret", lambda _: True)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: True)
    result = await router.mfa_verify_login(req(ip="127.0.0.96"), type("B", (), {"challenge_token": challenge, "totp_code": "123456"})(), db_session)
    assert result["scope"] == "full"


@pytest.mark.asyncio
async def test_mfa_email_login_code_superadmin_and_tenant(db_session, monkeypatch):
    import pyotp
    admin = SuperAdmin(username="email-mfa", email="email-mfa@example.com", password_hash="x", full_name="Email MFA", mfa_secret=pyotp.random_base32())
    db_session.add(admin)
    tenant_user = User(keycloak_sub="email-tenant", email="tenant-mfa@example.com", mfa_secret=pyotp.random_base32())
    db_session.add(tenant_user); db_session.commit(); db_session.refresh(admin)
    monkeypatch.setattr(auth_service, "send_mfa_email_code", AsyncMock())
    admin_challenge = jwt.encode({"mfa_challenge": True, "super_admin_id": str(admin.super_admin_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    result = await router.mfa_email_send_login_code(req(ip="127.0.0.97"), type("B", (), {"challenge_token": admin_challenge})(), db_session)
    assert result["detail"]


@pytest.mark.asyncio
async def test_mfa_and_impersonation_rejection_paths(db_session, monkeypatch):
    with pytest.raises(Exception):
        await router.mfa_email_send_login_code(req(ip="127.0.0.99"), type("B", (), {"challenge_token": "bad"})(), db_session)
    with pytest.raises(Exception):
        await router.mfa_verify_login(req(ip="127.0.0.100"), type("B", (), {"challenge_token": "bad", "totp_code": "000000"})(), db_session)
    with pytest.raises(Exception):
        await router.impersonate(req(ip="127.0.0.101"), type("B", (), {"target_tenant_id": "t"})(), user(["doctor"]))

    tenant_token = jwt.encode({"mfa_challenge": False, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception):
        await router.mfa_email_send_login_code(req(ip="127.0.0.102"), type("B", (), {"challenge_token": tenant_token})(), db_session)
    tenant_user = User(keycloak_sub="email-tenant", email="tenant@example.com", mfa_secret="JBSWY3DPEHPK3PXP")
    db_session.add(tenant_user)
    db_session.commit()
    tenant_challenge = jwt.encode({"mfa_challenge": True, "tenant_id": "t1", "sub": "email-tenant", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(auth_service, "send_mfa_email_code", AsyncMock())
    result = await router.mfa_email_send_login_code(req(ip="127.0.0.98"), type("B", (), {"challenge_token": tenant_challenge})(), db_session)
    assert result["detail"]


@pytest.mark.asyncio
async def test_mfa_verify_login_tenant_success_and_rejections(db_session, monkeypatch):
    import hashlib
    import json
    import pyotp
    from fastapi import HTTPException

    tenant_secret = pyotp.random_base32()
    code = "tenant-backup"
    user_record = User(keycloak_sub="tenant-login", email="tenant@example.com", mfa_secret=tenant_secret, mfa_enabled=True, backup_codes=json.dumps([hashlib.sha256(code.encode()).hexdigest()]))
    db_session.add(user_record); db_session.commit()
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: False)
    token = jwt.encode({"mfa_challenge": True, "tenant_id": "t1", "sub": "tenant-login", "exp": datetime.now(timezone.utc) + timedelta(minutes=5), "tokens": json.dumps({"access_token": "a"})}, settings.secret_key, algorithm="HS256")
    result = await router.mfa_verify_login(req(ip="127.0.0.110"), type("B", (), {"challenge_token": token, "totp_code": code})(), db_session)
    assert result["scope"] == "full"

    bad_context = jwt.encode({"mfa_challenge": True, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException):
        await router.mfa_verify_login(req(ip="127.0.0.111"), type("B", (), {"challenge_token": bad_context, "totp_code": "0"})(), db_session)
    bad_user = jwt.encode({"mfa_challenge": True, "tenant_id": "t1", "sub": "missing", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException):
        await router.mfa_verify_login(req(ip="127.0.0.112"), type("B", (), {"challenge_token": bad_user, "totp_code": "0"})(), db_session)


@pytest.mark.asyncio
async def test_mfa_verify_login_superadmin_backup_and_payload_errors(db_session, monkeypatch):
    import hashlib
    import json
    from fastapi import HTTPException
    admin = SuperAdmin(username="backup-admin", email="backup@example.com", password_hash="x", full_name="Backup", mfa_secret="JBSWY3DPEHPK3PXP", mfa_enabled=True, backup_codes=json.dumps([hashlib.sha256(b"backup").hexdigest()]))
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    token_base = {"mfa_challenge": True, "superadmin": True, "super_admin_id": str(admin.super_admin_id), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    monkeypatch.setattr(auth_service, "is_valid_totp_secret", lambda _: True)
    monkeypatch.setattr(auth_service, "verify_mfa_totp", lambda **kwargs: False)
    token_base["tokens"] = json.dumps({"access_token": "a"})
    token = jwt.encode(token_base, settings.secret_key, algorithm="HS256")
    result = await router.mfa_verify_login(req(ip="127.0.0.113"), type("B", (), {"challenge_token": token, "totp_code": "backup"})(), db_session)
    assert result["scope"] == "full"

    for payload in ({**token_base, "tokens": {"access_token": "a"}}, {**token_base, "tokens": "not-json"}):
        encoded = jwt.encode(payload, settings.secret_key, algorithm="HS256")
        with pytest.raises(HTTPException):
            await router.mfa_verify_login(req(ip="127.0.0.114" if payload["tokens"] is not None else "127.0.0.115"), type("B", (), {"challenge_token": encoded, "totp_code": "backup"})(), db_session)


@pytest.mark.asyncio
async def test_refresh_and_mfa_email_error_paths(db_session, monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(auth_service, "refresh_access_token", AsyncMock(return_value={"access_token": jwt.encode({"sub": "u", "tenant_id": "t1"}, settings.secret_key, algorithm="HS256"), "refresh_token": "r"}))
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(HTTPException):
        await router.refresh(req(ip="127.0.0.120"), type("B", (), {"refresh_token": "r"})(), db_session)
    monkeypatch.setattr(router, "is_tenant_suspended", AsyncMock(return_value=False))
    monkeypatch.setattr(auth_service, "refresh_access_token", AsyncMock(return_value={"access_token": "bad", "refresh_token": "r"}))
    assert (await router.refresh(req(ip="127.0.0.121"), type("B", (), {"refresh_token": "r"})(), db_session))["tenant_id"] is None

    bad = jwt.encode({"mfa_challenge": True, "tenant_id": "t1", "sub": "missing", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    with pytest.raises(HTTPException):
        await router.mfa_email_send_login_code(req(ip="127.0.0.122"), type("B", (), {"challenge_token": bad})(), db_session)
    no_tenant = jwt.encode({"mfa_challenge": True, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException):
        await router.mfa_email_send_login_code(req(ip="127.0.0.123"), type("B", (), {"challenge_token": no_tenant})(), db_session)


@pytest.mark.asyncio
async def test_session_check_and_keep_only_this_session(db_session):
    auth_service._store_refresh_token(db_session, "sub", "sid", "r", 3600, "realm")
    context = TenantContext("t", "sub", "user", "u@example.com", [], False, raw_token={"sid": "sid"})
    result = await router.check_session(req("GET"), context, db_session)
    assert result["session_revoked"] is False
    result = await router.keep_only_this_session(req(), context, db_session)
    assert result["detail"]
    bad = TenantContext("t", "", None, None, [], False, raw_token={})
    result = await router.check_session(req("GET"), bad, db_session)
    assert result["session_revoked"] is False


@pytest.mark.asyncio
async def test_session_check_distinguishes_missing_from_revoked(db_session):
    """Regression test for the false-logout bug: a RefreshToken row that
    simply doesn't exist must NOT produce the same response shape as a row
    that genuinely exists and is revoked — the frontend force-logs-out only
    on a real revoke, so conflating the two caused valid sessions to be
    kicked."""
    # Valid, active row: session_revoked must be false. This path falls
    # through to the existing has_other_active check, which keeps its
    # original 2-key response shape (has_other_active, session_revoked) —
    # session_missing/status are only added on the not_found/revoked/expired
    # short-circuit branches below, which is what this test is really about.
    auth_service._store_refresh_token(db_session, "sub-a", "sid-active", "r", 3600, "realm")
    ctx_active = TenantContext("t", "sub-a", "user", "u@example.com", [], False, raw_token={"sid": "sid-active"})
    result = await router.check_session(req("GET"), ctx_active, db_session)
    assert result["session_revoked"] is False

    # No row at all for this session_id/keycloak_sub — this must be reported
    # as "not found", not as a revoke.
    ctx_missing = TenantContext("t", "sub-a", "user", "u@example.com", [], False, raw_token={"sid": "sid-does-not-exist"})
    result = await router.check_session(req("GET"), ctx_missing, db_session)
    assert result["session_revoked"] is False
    assert result.get("session_missing") is True
    assert result.get("status") == "not_found"
    # Must not be the same shape as a genuine revoke.
    assert result != {"has_other_active": False, "session_revoked": True}

    # Now genuinely revoke the active row and confirm the endpoint correctly
    # reports it as revoked, distinct from the "not found" case above.
    row = db_session.query(RefreshToken).filter(RefreshToken.session_id == "sid-active").first()
    row.is_revoked = True
    db_session.commit()
    result = await router.check_session(req("GET"), ctx_active, db_session)
    assert result["session_revoked"] is True
    assert result.get("session_missing") is False
    assert result.get("status") == "revoked"
