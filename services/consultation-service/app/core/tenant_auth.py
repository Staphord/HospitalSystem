from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from app.core.config import settings
from app.db.master import get_master_session
from app.models.master import Tenant
from app.services.tenant_service import is_tenant_suspended


@dataclass
class TenantContext:
    tenant_id: str | None
    user_sub: str
    preferred_username: str | None
    email: str | None
    roles: list[str]
    is_super_admin: bool
    scope: str = "full"
    raw_token: dict[str, Any] = field(default_factory=dict)


_bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: TTLCache[str, dict[str, Any]] = TTLCache(maxsize=10, ttl=300)


def _issuer(realm: str | None = None) -> str:
    return f"{settings.keycloak_url}/realms/{realm or settings.keycloak_realm}"


def _extract_realm_from_iss(token: str) -> str | None:
    """Extract realm name from the unverified token's iss claim."""
    try:
        claims = jwt.get_unverified_claims(token)
        iss = claims.get("iss", "")
        if iss.startswith(settings.keycloak_url + "/realms/"):
            return iss.split("/realms/", 1)[1]
    except Exception:
        pass
    return None


async def _fetch_jwks(realm: str | None = None) -> dict[str, Any]:
    key = f"jwks:{realm or settings.keycloak_realm}"
    if key in _jwks_cache:
        return _jwks_cache[key]
    url = f"{_issuer(realm)}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        data = resp.json()
        _jwks_cache[key] = data
        return data


def _build_rsa_key(jwks: dict[str, Any], kid: str) -> dict[str, Any]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def _decode_token(token: str) -> dict[str, Any]:
    try:
        headers = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    kid = headers.get("kid", "")

    if kid == "impersonation-key" or not kid:
        try:
            payload = jwt.decode(
                token, settings.secret_key, algorithms=["HS256"],
                options={"verify_exp": True, "verify_aud": False},
            )
            return payload
        except jwt.ExpiredSignatureError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
        except Exception as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    # Multi-realm: derive realm from the token's issuer claim
    token_realm = _extract_realm_from_iss(token)
    jwks = await _fetch_jwks(token_realm)
    rsa_key = _build_rsa_key(jwks, kid)
    try:
        return jwt.decode(
            token, rsa_key, algorithms=["RS256"],
            issuer=_issuer(token_realm), options={"verify_exp": True, "verify_aud": False},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


async def get_current_tenant(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TenantContext:
    if not credentials or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = await _decode_token(credentials.credentials)

    # Local superadmin token handling
    if payload.get("type") == "superadmin":
        ctx = TenantContext(
            tenant_id=None,
            user_sub=payload.get("super_admin_id"),
            preferred_username=payload.get("username"),
            email=None,
            roles=[payload.get("role", "super_admin")],
            is_super_admin=True,
            scope="full",
            raw_token=payload,
        )
        request.state.tenant = ctx
        request.state.user_sub = ctx.user_sub
        request.state.user = {
            "super_admin_id": payload.get("super_admin_id"),
            "username": payload.get("username"),
            "role": payload.get("role", "super_admin"),
        }
        return ctx

    raw_roles = payload.get("realm_access", {}).get("roles", [])
    is_super = payload.get("is_super_admin", False) or "super_admin" in raw_roles
    tenant_id: str | None = payload.get("tenant_id", None)
    raw_scope: str = payload.get("scope", "full")
    scope = "readonly" if raw_scope == "readonly" else "full"

    ctx = TenantContext(
        tenant_id=tenant_id,
        user_sub=payload.get("sub"),
        preferred_username=payload.get("preferred_username"),
        email=payload.get("email"),
        roles=raw_roles,
        is_super_admin=is_super,
        scope=scope,
        raw_token=payload,
    )

    if not is_super and not tenant_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="User has no tenant association",
        )

    if tenant_id and await is_tenant_suspended(tenant_id):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "TENANT_SUSPENDED", "message": "Tenant subscription is suspended"},
        )

    request.state.tenant = ctx
    request.state.user_sub = ctx.user_sub
    return ctx
