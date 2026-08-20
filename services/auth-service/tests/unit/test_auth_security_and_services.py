"""Behavioral coverage for authentication helpers and security dependencies."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

from app.core import security, tenant_auth
from app.core.config import settings
from app.models.user import User
from app.services import brute_force, impersonation, superadmin_auth, tenant_service
from app.services import auth as auth_service


def request() -> Request:
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/"})


def test_brute_force_helpers_with_mock_redis(monkeypatch):
    redis = MagicMock()
    redis.exists.return_value = 1
    redis.ttl.return_value = 42
    redis.get.side_effect = ["3"]
    pipe = redis.pipeline.return_value
    pipe.execute.return_value = [5, True]
    monkeypatch.setattr(brute_force, "_redis_client", redis)

    assert brute_force.is_blocked("u", None) is True
    brute_force.record_failed_attempt("u", "1.2.3.4")
    brute_force.record_successful_login("u", "1.2.3.4")
    assert brute_force.get_remaining_seconds("u", "1.2.3.4") == 42
    assert brute_force.get_failed_attempts("u", "1.2.3.4") == 3
    redis.setex.assert_called_once()


def test_brute_force_helpers_fail_closed_when_redis_errors(monkeypatch):
    redis = MagicMock()
    redis.exists.side_effect = RuntimeError("down")
    redis.ttl.side_effect = RuntimeError("down")
    redis.get.side_effect = RuntimeError("down")
    redis.pipeline.side_effect = RuntimeError("down")
    monkeypatch.setattr(brute_force, "_redis_client", redis)
    assert brute_force.is_blocked("u") is False
    brute_force.record_failed_attempt("u")
    brute_force.record_successful_login("u")
    assert brute_force.get_remaining_seconds("u") == 0
    assert brute_force.get_failed_attempts("u") == 0


def test_impersonation_token_and_superadmin_helpers():
    result = impersonation.create_impersonation_token("sa", "admin", "tenant-1")
    claims = jwt.get_unverified_claims(result["access_token"])
    assert result["scope"] == "readonly"
    assert claims["tenant_id"] == "tenant-1"
    assert claims["impersonator"] is True

    token = superadmin_auth.create_access_token("id", "admin", "super_admin")
    assert superadmin_auth.decode_superadmin_token(token)["username"] == "admin"
    with pytest.raises(Exception):
        superadmin_auth.decode_superadmin_token("bad-token")
    expired = jwt.encode({"type": "superadmin", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception):
        superadmin_auth.decode_superadmin_token(expired)


def test_superadmin_crud_and_authentication(db_session):
    admin = superadmin_auth.create_superadmin(db_session, "admin", "a@example.com", "Password1!", "Admin")
    assert superadmin_auth.authenticate_superadmin(db_session, "admin", "Password1!").username == "admin"
    with pytest.raises(Exception):
        superadmin_auth.create_superadmin(db_session, "admin", "other@example.com", "Password1!", "Admin")
    with pytest.raises(Exception):
        superadmin_auth.create_superadmin(db_session, "other", "a@example.com", "Password1!", "Admin")
    with pytest.raises(Exception):
        superadmin_auth.authenticate_superadmin(db_session, "admin", "wrong")
    admin.is_active = False
    db_session.commit()
    with pytest.raises(Exception):
        superadmin_auth.authenticate_superadmin(db_session, "admin", "Password1!")
    admin.is_active = True
    superadmin_auth.update_superadmin_password(db_session, admin, "NewPassword1!")
    superadmin_auth.update_superadmin_role(db_session, admin, "operator")
    assert admin.role == "operator"
    with pytest.raises(Exception):
        superadmin_auth.authenticate_superadmin(db_session, "missing", "Password1!")


@pytest.mark.asyncio
async def test_impersonation_audit_writes_record(db_session, monkeypatch):
    monkeypatch.setattr("app.db.master.get_master_db", lambda: db_session)
    await impersonation.log_impersonation_event("START", "sa", "tenant-1", "127.0.0.1")
    from app.models.master import GlobalAuditLog
    assert db_session.query(GlobalAuditLog).count() == 1


def test_security_role_and_hospital_dependencies(db_session):
    user = security.TokenPayload("sub", "name", "e@example.com", {"roles": ["doctor"]}, {})
    assert security._extract_roles(user) == ["doctor"]
    local = security.TokenPayload("sa", "admin", None, {}, {"type": "superadmin", "role": "super_admin"})
    assert security._extract_roles(local) == ["super_admin"]

    allowed = security.require_role("doctor")
    assert callable(allowed)

    db_session.add(User(keycloak_sub="sub", username="name", email="e@example.com", hospital_id="h1"))
    db_session.commit()


@pytest.mark.asyncio
async def test_security_token_decode_and_current_user(monkeypatch):
    token = jwt.encode({"sub": "s", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    payload = await security._decode_token(token)
    assert payload["sub"] == "s"
    with pytest.raises(HTTPException):
        await security._decode_token("not-a-token")
    monkeypatch.setattr(security.settings, "keycloak_introspect", False)
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    result = await security.get_current_active_user(request(), credentials)
    assert result.sub == "s"
    with pytest.raises(HTTPException):
        await security.get_current_active_user(request(), None)
    monkeypatch.setattr(security.settings, "keycloak_introspect", True)
    monkeypatch.setattr(security, "_introspect_token", AsyncMock())
    await security.get_current_active_user(request(), credentials)
    security._introspection_cache.clear()


@pytest.mark.asyncio
async def test_security_jwks_and_introspection_branches(monkeypatch):
    class Response:
        def __init__(self, body, status=200): self.body, self.status_code = body, status
        def json(self): return self.body
        def raise_for_status(self):
            if self.status_code >= 400: raise RuntimeError("http")
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response({"keys": [{"kid": "k"}]})
        async def post(self, *args, **kwargs): return Response({"active": True})
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kwargs: Client())
    security._jwks_cache.clear(); security._introspection_cache.clear()
    assert await security._fetch_jwks("realm") == {"keys": [{"kid": "k"}]}
    assert security._build_rsa_key({"keys": [{"kid": "k"}]}, "k")["kid"] == "k"
    with pytest.raises(HTTPException): security._build_rsa_key({}, "missing")
    await security._introspect_token("token")
    await security._introspect_token("token")
    security._introspection_cache["inactive"] = False
    with pytest.raises(HTTPException): await security._introspect_token("inactive")
    assert security._extract_realm_from_iss(jwt.encode({"iss": "https://other/realms/x"}, settings.secret_key, algorithm="HS256")) is None
    security._jwks_cache["jwks:realm"] = {"cached": True}
    assert await security._fetch_jwks("realm") == {"cached": True}


@pytest.mark.asyncio
async def test_security_multirealm_decode_and_issuer_edges(monkeypatch):
    assert security._issuer("realm").endswith("/realms/realm")
    token = jwt.encode({"iss": f"{settings.keycloak_url}/realms/tenant"}, settings.secret_key, algorithm="HS256", headers={"kid": "rsa-kid"})
    assert security._extract_realm_from_iss(token) == "tenant"
    assert security._extract_realm_from_iss("bad") is None
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "rsa-kid"}]}))
    monkeypatch.setattr(security.jwt, "decode", lambda *args, **kwargs: {"sub": "rsa-sub"})
    assert (await security._decode_token(token))["sub"] == "rsa-sub"
    monkeypatch.setattr(security.jwt, "decode", lambda *args, **kwargs: (_ for _ in ()).throw(security.jwt.ExpiredSignatureError("expired")))
    with pytest.raises(HTTPException): await security._decode_token(token)


@pytest.mark.asyncio
async def test_security_invalid_headers_introspection_and_hospital_fallbacks(monkeypatch, db_session):
    with pytest.raises(HTTPException):
        await security._decode_token("not-a-token")
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "k"}]}))
    token = jwt.encode({"iss": f"{settings.keycloak_url}/realms/t", "sub": "x"}, settings.secret_key, algorithm="HS256", headers={"kid": "k"})
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad")))
    with pytest.raises(HTTPException): await security._decode_token(token)
    with pytest.raises(HTTPException):
        await security.get_current_active_user(request(), HTTPAuthorizationCredentials(scheme="Basic", credentials="x"))
    security._introspection_cache.clear()
    class Resp:
        def raise_for_status(self): raise RuntimeError("down")
        def json(self): return {"active": False}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, *a, **k): return Resp()
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **k: Client())
    with pytest.raises(RuntimeError): await security._introspect_token("fresh")
    assert await security.get_current_hospital_id(security.TokenPayload("sa", "u", None, {"roles": ["super_admin"]}, {}), db_session) is None


@pytest.mark.asyncio
async def test_tenant_auth_invalid_and_superadmin_variants(monkeypatch):
    with pytest.raises(HTTPException):
        await tenant_auth._decode_token("bad")
    payload = {"type": "superadmin", "super_admin_id": "id", "username": "admin", "role": "operator"}
    token = jwt.encode(payload, settings.secret_key, algorithm="HS256", headers={"kid": "superadmin-key"})
    ctx = await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert ctx.roles == ["operator"]
    token = jwt.encode({"sub": "u", "is_super_admin": True, "scope": "weird"}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=False))
    ctx = await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert ctx.is_super_admin and ctx.scope == "full"


@pytest.mark.asyncio
async def test_security_roles_hospital_and_expired_tokens(db_session):
    dep = security.require_role("doctor")
    with pytest.raises(HTTPException): await dep(security.TokenPayload("s", "u", None, {"roles": []}, {}))
    assert await dep(security.TokenPayload("s", "u", None, {"roles": ["doctor"]}, {}))
    db_session.add(User(keycloak_sub="hospital-sub", hospital_id="h1")); db_session.commit()
    assert await security.get_current_hospital_id(security.TokenPayload("hospital-sub", "u", None, {}, {}), db_session) == "h1"
    assert await security.get_current_hospital_id(security.TokenPayload("sa", "u", None, {}, {"type": "superadmin"}), db_session) is None
    with pytest.raises(HTTPException):
        await security.get_current_hospital_id(security.TokenPayload("none", "u", None, {}, {}), db_session)
    expired = jwt.encode({"sub": "s", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException): await security._decode_token(expired)


@pytest.mark.asyncio
async def test_tenant_auth_local_superadmin_and_tenant_paths(monkeypatch):
    token = superadmin_auth.create_access_token("id", "admin", "super_admin")
    ctx = await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    assert ctx.is_super_admin is True

    tenant_token = jwt.encode({"sub": "u", "tenant_id": "t", "realm_access": {"roles": ["doctor"]}, "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=False))
    ctx = await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=tenant_token))
    assert ctx.tenant_id == "t"
    no_tenant = jwt.encode({"sub": "u", "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException):
        await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=no_tenant))


@pytest.mark.asyncio
async def test_tenant_auth_jwks_and_suspension_branches(monkeypatch):
    with pytest.raises(HTTPException):
        await tenant_auth.get_current_tenant(request(), None)
    tenant_auth._jwks_cache.clear()
    class Response:
        status_code = 200
        def json(self): return {"keys": [{"kid": "key"}]}
        def raise_for_status(self): pass
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(tenant_auth.httpx, "AsyncClient", lambda **kwargs: Client())
    assert await tenant_auth._fetch_jwks("realm") == {"keys": [{"kid": "key"}]}
    with pytest.raises(HTTPException): tenant_auth._build_rsa_key({}, "missing")
    token = jwt.encode({"sub": "u", "tenant_id": "t", "scope": "readonly", "realm_access": {"roles": []}, "exp": datetime.now(timezone.utc) + timedelta(minutes=1)}, settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(HTTPException):
        await tenant_auth.get_current_tenant(request(), HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    expired = jwt.encode({"sub": "u", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, settings.secret_key, algorithm="HS256")
    with pytest.raises(HTTPException): await tenant_auth._decode_token(expired)


@pytest.mark.asyncio
async def test_tenant_auth_multirealm_decode_and_extract_edges(monkeypatch):
    assert tenant_auth._issuer("realm").endswith("/realms/realm")
    token = jwt.encode({"iss": f"{settings.keycloak_url}/realms/tenant"}, settings.secret_key, algorithm="HS256", headers={"kid": "rsa-kid"})
    assert tenant_auth._extract_realm_from_iss(token) == "tenant"
    assert tenant_auth._extract_realm_from_iss("invalid") is None
    monkeypatch.setattr(tenant_auth, "_fetch_jwks", AsyncMock(return_value={"keys": [{"kid": "rsa-kid"}]}))
    monkeypatch.setattr(tenant_auth.jwt, "decode", lambda *args, **kwargs: {"sub": "u", "tenant_id": "t"})
    assert (await tenant_auth._decode_token(token))["sub"] == "u"
    monkeypatch.setattr(tenant_auth.jwt, "decode", lambda *args, **kwargs: (_ for _ in ()).throw(tenant_auth.jwt.ExpiredSignatureError("expired")))
    with pytest.raises(HTTPException): await tenant_auth._decode_token(token)


def test_tenant_crypto_and_subscription_queries(monkeypatch):
    encrypted = tenant_service.encrypt_dsn("postgresql://tenant")
    assert tenant_service.decrypt_dsn(encrypted) == "postgresql://tenant"
    assert tenant_service._get_cipher() is tenant_service._get_cipher()


@pytest.mark.asyncio
async def test_auth_mfa_and_backup_code(db_session):
    generated = auth_service.generate_mfa_secret("sub")
    assert auth_service.is_valid_totp_secret(generated["secret"])
    assert auth_service.get_pending_mfa_secret("sub") == generated["secret"]
    import pyotp
    assert auth_service.verify_mfa_totp("sub", pyotp.TOTP(generated["secret"]).now()) is True
    auth_service.clear_pending_mfa_secret("sub")
    assert auth_service.verify_mfa_totp("sub", "000000") is False

    import hashlib, json
    code = "backup-code"
    user = User(keycloak_sub="backup-sub", backup_codes=json.dumps([hashlib.sha256(code.encode()).hexdigest()]))
    assert auth_service.verify_backup_code(user, code, db_session) is True
    assert auth_service.verify_backup_code(user, code, db_session) is False
    user.backup_codes = "invalid"
    assert auth_service.verify_backup_code(user, code, db_session) is False


@pytest.mark.asyncio
async def test_auth_logout_revoke_and_token_helpers(db_session, monkeypatch):
    auth_service._store_refresh_token(db_session, "s", "sid", "token", 100, "realm")
    assert await auth_service.revoke_all_sessions("s", db_session) == 1
    monkeypatch.setattr(auth_service, "_keycloak_logout_endpoint", lambda realm=None: "http://logout")
    response = MagicMock()
    response.status_code = 204
    monkeypatch.setattr("httpx.AsyncClient.post", AsyncMock(return_value=response))
    await auth_service.logout("token", db_session)
    assert auth_service._extract_token_info("bad-token", "fallback")[1] == "fallback"
