from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.core import security, tenant_auth
from app.services import tenant_service


def _token(payload):
    return jwt.encode(payload, security.settings.secret_key, algorithm="HS256")


@pytest.mark.asyncio
async def test_security_local_tokens_roles_and_hospital():
    from unittest.mock import patch
    req = MagicMock(); req.state = MagicMock()
    with patch("app.core.security.settings.keycloak_introspect", False):
        user = await security.get_current_active_user(req, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials=_token({"sub": "u", "type": "superadmin"})))
        assert security._extract_roles(user) == ["super_admin"]
    assert req.state.user_sub == "u"
    with pytest.raises(Exception): await security.get_current_active_user(req, None)
    with pytest.raises(Exception): await security.get_current_active_user(req, security.HTTPAuthorizationCredentials(scheme="Basic", credentials="x"))
    db = MagicMock(); db.query.return_value.filter.return_value.one_or_none.return_value = MagicMock(hospital_id="h")
    assert await security.get_current_hospital_id(user, db) == "h"
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    with pytest.raises(Exception): await security.get_current_hospital_id(security.TokenPayload("u",None,None,{},{}), db)


@pytest.mark.asyncio
async def test_security_jwt_and_introspection_errors(monkeypatch):
    with pytest.raises(Exception): await security._decode_token("bad")
    expired = _token({"sub": "u", "exp": 1})
    with pytest.raises(Exception): await security._decode_token(expired)
    security._introspection_cache.clear(); security._introspection_cache["inactive"] = False
    with pytest.raises(Exception): await security._introspect_token("inactive")
    monkeypatch.setattr(security, "_fetch_jwks", AsyncMock(return_value={"keys": []}))
    rsa = jwt.encode({"sub":"u"}, "secret", algorithm="HS256", headers={"kid":"k"})
    with pytest.raises(Exception): await security._decode_token(rsa)
    with pytest.raises(Exception): security._build_rsa_key({}, "x")


@pytest.mark.asyncio
async def test_security_keycloak_fetch_decode_and_active_introspection(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"keys": [{"kid": "k"}]}
    active = type("Active", (), {"raise_for_status": lambda self: None, "json": lambda self: {"active": True}})()
    client = MagicMock(); client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(); client.get = AsyncMock(return_value=Response()); client.post = AsyncMock(return_value=active)
    monkeypatch.setattr(security.httpx, "AsyncClient", lambda **kw: client)
    security._jwks_cache.clear(); assert await security._fetch_jwks("realm") == {"keys": [{"kid": "k"}]}; assert await security._fetch_jwks("realm") == {"keys": [{"kid": "k"}]}
    monkeypatch.setattr(security.jwt, "get_unverified_header", lambda _: {"kid": "k"})
    monkeypatch.setattr(security.jwt, "decode", lambda *a, **k: {"sub": "u"})
    assert (await security._decode_token("token"))["sub"] == "u"
    monkeypatch.setattr(security.jwt, "decode", MagicMock(side_effect=security.jwt.ExpiredSignatureError()))
    with pytest.raises(Exception): await security._decode_token("token")
    security._introspection_cache.clear(); await security._introspect_token("active")
    assert security._introspection_cache["active"] is True
    req = MagicMock(); req.state = MagicMock(); monkeypatch.setattr(security, "_decode_token", AsyncMock(return_value={"sub":"u", "realm_access":{"roles":["doctor"]}})); monkeypatch.setattr(security.settings, "keycloak_introspect", True); monkeypatch.setattr(security, "_introspect_token", AsyncMock())
    user = await security.get_current_active_user(req, security.HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")); assert user.sub == "u"
    assert await security.require_role("doctor")(user) is user
    super_user = security.TokenPayload("u", None, None, {}, {"type":"superadmin"}); db = MagicMock(); db.query.return_value.filter.return_value.one_or_none.return_value = None
    assert await security.get_current_hospital_id(super_user, db) is None


@pytest.mark.asyncio
async def test_tenant_auth_contexts_and_token_errors(monkeypatch):
    req = MagicMock(); req.state = MagicMock()
    local = jwt.encode({"type":"superadmin", "super_admin_id":"sa", "username":"root"}, tenant_auth.settings.secret_key, algorithm="HS256")
    ctx = await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=local))
    assert ctx.is_super_admin and req.state.user_sub == "sa"
    token = jwt.encode({"sub":"u", "tenant_id":"t", "scope":"readonly", "realm_access":{"roles":["doctor"]}}, tenant_auth.settings.secret_key, algorithm="HS256")
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=False))
    assert (await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))).scope == "readonly"
    no_tenant = jwt.encode({"sub":"u", "realm_access":{"roles":[]}}, tenant_auth.settings.secret_key, algorithm="HS256")
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=no_tenant))
    monkeypatch.setattr(tenant_auth, "is_tenant_suspended", AsyncMock(return_value=True))
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, tenant_auth.HTTPAuthorizationCredentials(scheme="Bearer", credentials=token))
    with pytest.raises(Exception): await tenant_auth.get_current_tenant(req, None)


@pytest.mark.asyncio
async def test_tenant_auth_keycloak_fetch_and_decode_branches(monkeypatch):
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"keys": [{"kid": "k"}]}
    client = MagicMock(); client.__aenter__=AsyncMock(return_value=client); client.__aexit__=AsyncMock(); client.get=AsyncMock(return_value=Response())
    monkeypatch.setattr(tenant_auth.httpx, "AsyncClient", lambda **kw: client)
    tenant_auth._jwks_cache.clear(); assert await tenant_auth._fetch_jwks("realm") == {"keys":[{"kid":"k"}]}; assert await tenant_auth._fetch_jwks("realm") == {"keys":[{"kid":"k"}]}
    monkeypatch.setattr(tenant_auth.jwt, "get_unverified_header", lambda _: {"kid":"k"}); monkeypatch.setattr(tenant_auth.jwt, "decode", lambda *a, **k: {"tenant_id":"t"})
    assert (await tenant_auth._decode_token("token"))["tenant_id"] == "t"
    monkeypatch.setattr(tenant_auth.jwt, "decode", MagicMock(side_effect=tenant_auth.jwt.ExpiredSignatureError()))
    with pytest.raises(Exception): await tenant_auth._decode_token("token")


def _row(*values):
    db = MagicMock(); result = MagicMock(); result.one_or_none.return_value = values; result.scalar.return_value = values[0] if values else None; db.execute.return_value = result; return db


@pytest.mark.asyncio
async def test_tenant_service_status_cache_and_encryption(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setattr(tenant_service.settings, "tenant_db_encryption_key", Fernet.generate_key().decode())
    assert tenant_service.decrypt_dsn(tenant_service.encrypt_dsn("postgresql://tenant")) == "postgresql://tenant"
    redis = AsyncMock(); redis.get.return_value = "1"; monkeypatch.setattr(tenant_service, "_redis", redis)
    assert await tenant_service.is_tenant_suspended("t") is True
    await tenant_service.cache_tenant_suspension("t", 10); await tenant_service.remove_tenant_suspension_cache("t")
    assert await tenant_service.check_tenant_subscription(_row("suspended", None), "t") == "suspended"
    assert await tenant_service.check_tenant_subscription(_row("active", datetime.now(timezone.utc)-timedelta(days=1)), "t") == "expired"
    assert await tenant_service.check_tenant_subscription(_row("active", datetime.now(timezone.utc)+timedelta(days=1)), "t") == "active"
    assert await tenant_service.check_tenant_subscription(_row(), "t") == "not_found"


@pytest.mark.asyncio
async def test_tenant_service_update_expiry_and_redis_failure(monkeypatch):
    failing = AsyncMock(); failing.get.side_effect = RuntimeError(); failing.set.side_effect = RuntimeError(); failing.delete.side_effect = RuntimeError()
    monkeypatch.setattr(tenant_service, "_redis", failing)
    assert await tenant_service.is_tenant_suspended("t") is False
    await tenant_service.cache_tenant_suspension("t"); await tenant_service.remove_tenant_suspension_cache("t")
    monkeypatch.setattr(tenant_service, "cache_tenant_suspension", AsyncMock())
    monkeypatch.setattr(tenant_service, "_revoke_keycloak_sessions", AsyncMock())
    old = datetime.now(timezone.utc) - timedelta(days=40)
    db = _row("active", old, 7)
    assert await tenant_service.check_and_update_tenant_status(db, "t") == "suspended"
    assert db.commit.called
    assert await tenant_service.check_and_update_tenant_status(_row("active", datetime.now(timezone.utc)-timedelta(days=2), 7), "t") == "expired"
    assert await tenant_service.check_and_update_tenant_status(_row("suspended", None, 7), "t") == "suspended"


@pytest.mark.asyncio
async def test_tenant_service_keycloak_revoke_and_dsn_lookup(monkeypatch):
    class Response:
        is_success = True
        def raise_for_status(self): pass
        def json(self): return {"access_token": "admin"}
    class Users:
        is_success = True
        def raise_for_status(self): pass
        def json(self): return [{"id": "user-1"}, {}]
    client = MagicMock(); client.__aenter__=AsyncMock(return_value=client); client.__aexit__=AsyncMock(); client.post=AsyncMock(return_value=Response()); client.get=AsyncMock(return_value=Users()); client.put=AsyncMock()
    monkeypatch.setattr(tenant_service.httpx, "AsyncClient", lambda **kw: client)
    await tenant_service._revoke_keycloak_sessions("tenant")
    assert client.put.await_count == 1
    from cryptography.fernet import Fernet
    monkeypatch.setattr(tenant_service.settings, "tenant_db_encryption_key", Fernet.generate_key().decode())
    db = _row(tenant_service.encrypt_dsn("postgresql://tenant"))
    assert await tenant_service.get_tenant_db_dsn(db, "tenant") == "postgresql://tenant"
