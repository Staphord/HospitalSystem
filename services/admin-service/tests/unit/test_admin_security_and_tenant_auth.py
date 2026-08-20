from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from jose import jwt
from starlette.requests import Request

from app.config import settings
from app.core import security, tenant_auth
from app.core.security import TokenPayload
from app.exceptions import (
    BadRequestError, ConflictError, ForbiddenError, MFARequiredError, NotFoundError,
    RateLimitError, ReadOnlyScopeError, TenantNotFoundError, TenantSuspendedError,
    TokenExpiredError, UnauthorizedError,
)


def req():
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [],
                    "client": ("127.0.0.1", 1), "scheme": "http", "server": ("x", 80)})


def test_all_domain_exception_contracts():
    for cls, code in [(UnauthorizedError, 401), (ForbiddenError, 403), (NotFoundError, 404),
                      (ConflictError, 409), (BadRequestError, 400), (RateLimitError, 429),
                      (TenantNotFoundError, 404), (TokenExpiredError, 401), (MFARequiredError, 401),
                      (TenantSuspendedError, 403), (ReadOnlyScopeError, 403)]:
        assert cls().status_code == code


@pytest.mark.asyncio
async def test_tenant_auth_missing_invalid_suspended_and_normal_contexts():
    with pytest.raises(HTTPException) as exc:
        await tenant_auth.get_current_tenant(req(), None)
    assert exc.value.status_code == 401
    bad_scheme = SimpleNamespace(scheme="Basic", credentials="x")
    with pytest.raises(HTTPException):
        await tenant_auth.get_current_tenant(req(), bad_scheme)
    with patch.object(tenant_auth, "_decode_token", new_callable=AsyncMock, return_value={"sub": "u", "realm_access": {"roles": []}}):
        with pytest.raises(HTTPException) as exc:
            await tenant_auth.get_current_tenant(req(), SimpleNamespace(scheme="Bearer", credentials="x"))
        assert exc.value.status_code == 403
    with patch.object(tenant_auth, "_decode_token", new_callable=AsyncMock, return_value={"sub": "u", "tenant_id": "t", "scope": "readonly", "realm_access": {"roles": ["doctor"]}}), patch.object(tenant_auth, "is_tenant_suspended", new_callable=AsyncMock, return_value=True):
        with pytest.raises(HTTPException) as exc:
            await tenant_auth.get_current_tenant(req(), SimpleNamespace(scheme="Bearer", credentials="x"))
        assert exc.value.status_code == 403
    request = req()
    with patch.object(tenant_auth, "_decode_token", new_callable=AsyncMock, return_value={"sub": "u", "tenant_id": "t", "scope": "unexpected", "preferred_username": "doc", "email": "d@x.com", "realm_access": {"roles": ["doctor"]}}), patch.object(tenant_auth, "is_tenant_suspended", new_callable=AsyncMock, return_value=False):
        context = await tenant_auth.get_current_tenant(request, SimpleNamespace(scheme="Bearer", credentials="x"))
    assert context.scope == "full" and request.state.user_sub == "u"


@pytest.mark.asyncio
async def test_security_active_user_introspection_and_hospital_id_paths():
    token = jwt.encode({"sub": "u", "realm_access": {"roles": ["doctor"]}}, settings.secret_key, algorithm="HS256")
    request = req()
    with patch.object(security.settings, "keycloak_introspect", False):
        payload = await security.get_current_active_user(request, SimpleNamespace(scheme="Bearer", credentials=token))
    assert payload.sub == "u" and request.state.user_sub == "u"
    with pytest.raises(HTTPException):
        await security.get_current_active_user(request, None)
    security._introspection_cache.clear()
    with patch.object(security, "_extract_realm_from_iss", return_value="realm"), patch("httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"active": True}))
        await security._introspect_token("token")
        await security._introspect_token("token")
    db = MagicMock()
    db.query.return_value.filter.return_value.one_or_none.return_value = SimpleNamespace(hospital_id="h1")
    user = TokenPayload("u", None, None, {"roles": []}, {})
    assert await security.get_current_hospital_id(user, db) == "h1"
    db.query.return_value.filter.return_value.one_or_none.return_value = None
    with pytest.raises(HTTPException):
        await security.get_current_hospital_id(user, db)
    superuser = TokenPayload("sa", None, None, {}, {"type": "superadmin", "role": "super_admin"})
    assert await security.get_current_hospital_id(superuser, db) is None


@pytest.mark.asyncio
async def test_security_and_tenant_token_helpers_and_jwks_paths():
    assert tenant_auth._issuer("r").endswith("/realms/r")
    assert tenant_auth._extract_realm_from_iss("bad") is None
    assert tenant_auth._token_iss("bad") is None
    assert security._extract_realm_from_iss("bad") is None
    assert security._token_iss("bad") is None
    token = jwt.encode({"iss": "http://keycloak/realms/r", "sub": "u"}, "secret", algorithm="HS256", headers={"kid": "k1"})
    assert tenant_auth._extract_realm_from_iss(token) == "r"
    assert security._extract_realm_from_iss(token) == "r"
    tenant_auth._jwks_cache.clear()
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def get(self, url, headers=None):
            self.headers = headers
            return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": [{"kid": "k1"}]})
    with patch.object(tenant_auth.settings, "keycloak_url", "http://host.docker.internal:8080"), patch("httpx.AsyncClient", return_value=Client()):
        assert (await tenant_auth._fetch_jwks("r"))["keys"][0]["kid"] == "k1"
        assert (await tenant_auth._fetch_jwks("r"))["keys"][0]["kid"] == "k1"
    with pytest.raises(HTTPException):
        tenant_auth._build_rsa_key({}, "missing")
    assert tenant_auth._build_rsa_key({"keys": [{"kid": "k"}]}, "k")["kid"] == "k"
    assert security._build_rsa_key({"keys": [{"kid": "other"}, {"kid": "k"}]}, "k")["kid"] == "k"
    assert tenant_auth._build_rsa_key({"keys": [{"kid": "other"}, {"kid": "k"}]}, "k")["kid"] == "k"
    with patch.object(tenant_auth, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "k1"}]}), patch.object(tenant_auth.jwt, "decode", return_value={"sub": "u"}):
        assert await tenant_auth._decode_token(token) == {"sub": "u"}
    with patch.object(tenant_auth, "_fetch_jwks", new_callable=AsyncMock, side_effect=RuntimeError("jwks")):
        with pytest.raises(HTTPException):
            await tenant_auth._decode_token(token)
    security._jwks_cache.clear()
    with patch.object(security, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "k1"}]}), patch.object(security.jwt, "decode", return_value={"sub": "u"}):
        assert await security._decode_token(token) == {"sub": "u"}


def test_update_schema_optional_validators_return_none():
    from app.api.v1.admin.schemas import HospitalUserUpdate

    value = HospitalUserUpdate(password=None, username=None)
    assert value.password is None and value.username is None
    assert HospitalUserUpdate(password="SecurePass1!", username="valid_user").username == "valid_user"


@pytest.mark.asyncio
async def test_tenant_auth_hs_and_rsa_decode_error_contracts():
    with patch.object(tenant_auth.jwt, "get_unverified_header", side_effect=ValueError("bad")):
        with pytest.raises(HTTPException):
            await tenant_auth._decode_token("bad")
    with patch.object(tenant_auth.jwt, "get_unverified_header", return_value={}), \
         patch.object(tenant_auth.jwt, "decode", side_effect=tenant_auth.jwt.ExpiredSignatureError("expired")):
        with pytest.raises(HTTPException) as exc:
            await tenant_auth._decode_token("token")
        assert "expired" in exc.value.detail.lower()
    with patch.object(tenant_auth.jwt, "get_unverified_header", return_value={}), \
         patch.object(tenant_auth.jwt, "decode", side_effect=ValueError("invalid")):
        with pytest.raises(HTTPException):
            await tenant_auth._decode_token("token")


def test_tenant_auth_helper_branch_inputs():
    assert tenant_auth._extract_realm_from_iss("not-a-realm") is None
    assert tenant_auth._extract_realm_from_iss("https://x/realms/") is None
    assert tenant_auth._token_iss("not-a-token") is None
    assert security._extract_realm_from_iss("not-a-realm") is None
    assert security._token_iss("not-a-token") is None


@pytest.mark.asyncio
async def test_security_and_tenant_jwks_cache_and_header_branches():
    with patch.object(security.jwt, "get_unverified_claims", return_value={"iss": "https://kc/no-realm"}):
        assert security._extract_realm_from_iss("token") is None
    security._jwks_cache.clear()
    with patch.object(security.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient") as cls:
        client = cls.return_value; client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": []}))
        await security._fetch_jwks("no-host")
        assert await security._fetch_jwks("no-host") == {"keys": []}
    with patch.object(tenant_auth.jwt, "get_unverified_claims", return_value={"iss": "https://kc/no-realm"}):
        assert tenant_auth._extract_realm_from_iss("token") is None
    tenant_auth._jwks_cache.clear()
    with patch.object(tenant_auth.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient") as cls:
        client = cls.return_value; client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": []}))
        await tenant_auth._fetch_jwks("no-host")
        assert await tenant_auth._fetch_jwks("no-host") == {"keys": []}
    with patch.object(tenant_auth.jwt, "get_unverified_header", return_value={}), patch.object(tenant_auth.jwt, "decode", return_value={"sub": "u"}):
        assert await tenant_auth._decode_token("token") == {"sub": "u"}
    security._jwks_cache.clear()
    with patch.object(security.settings, "keycloak_url", "http://host.docker.internal:8080"), patch("httpx.AsyncClient") as cls:
        client = cls.return_value; client.__aenter__ = AsyncMock(return_value=client); client.__aexit__ = AsyncMock(return_value=False)
        client.get = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"keys": []}))
        await security._fetch_jwks("docker")


@pytest.mark.asyncio
async def test_security_introspection_inactive_and_configured_paths():
    security._introspection_cache.clear()
    with patch.object(security, "_extract_realm_from_iss", return_value=None), patch("httpx.AsyncClient") as client_cls:
        client = client_cls.return_value
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(return_value=SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"active": False}))
        with pytest.raises(HTTPException):
            await security._introspect_token("inactive")
    with pytest.raises(HTTPException):
        await security._introspect_token("inactive")
    token_payload = {"sub": "u", "realm_access": {"roles": []}}
    request = req()
    with patch.object(security, "_decode_token", new_callable=AsyncMock, return_value=token_payload), \
         patch.object(security.settings, "keycloak_introspect", True), \
         patch.object(security, "_introspect_token", new_callable=AsyncMock) as introspect:
        result = await security.get_current_active_user(request, SimpleNamespace(scheme="Bearer", credentials="x"))
        assert result.sub == "u"
        introspect.assert_awaited_once_with("x")


@pytest.mark.asyncio
async def test_security_decode_error_and_jwks_failure_branches():
    with patch.object(security.jwt, "get_unverified_header", return_value={}), \
         patch.object(security.jwt, "decode", side_effect=ValueError("bad")):
        with pytest.raises(HTTPException):
            await security._decode_token("token")
    with patch.object(security.jwt, "get_unverified_header", return_value={"kid": "rsa"}), \
         patch.object(security, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "rsa"}]}), \
         patch.object(security.jwt, "decode", side_effect=security.jwt.ExpiredSignatureError("expired")):
        with pytest.raises(HTTPException):
            await security._decode_token("token")
    rsa_headers = {"kid": "rsa"}
    with patch.object(security.jwt, "get_unverified_header", return_value=rsa_headers), \
         patch.object(security, "_fetch_jwks", new_callable=AsyncMock, side_effect=RuntimeError("jwks")):
        with pytest.raises(HTTPException):
            await security._decode_token("token")
    with patch.object(security.jwt, "get_unverified_header", return_value=rsa_headers), \
         patch.object(security, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "rsa"}]}), \
         patch.object(security.jwt, "decode", side_effect=ValueError("bad")):
        with pytest.raises(HTTPException):
            await security._decode_token("token")
    rsa_headers = {"kid": "k1"}
    with patch.object(tenant_auth.jwt, "get_unverified_header", return_value=rsa_headers), \
         patch.object(tenant_auth, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "k1"}]}), \
         patch.object(tenant_auth.jwt, "decode", side_effect=tenant_auth.jwt.ExpiredSignatureError("expired")):
        with pytest.raises(HTTPException):
            await tenant_auth._decode_token("token")
    with patch.object(tenant_auth.jwt, "get_unverified_header", return_value=rsa_headers), \
         patch.object(tenant_auth, "_fetch_jwks", new_callable=AsyncMock, return_value={"keys": [{"kid": "k1"}]}), \
         patch.object(tenant_auth.jwt, "decode", side_effect=ValueError("invalid")):
        with pytest.raises(HTTPException):
            await tenant_auth._decode_token("token")
