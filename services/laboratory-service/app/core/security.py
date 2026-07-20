from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from app.core.config import settings
from app.core.database import get_db
from app.models.user import User

_bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: TTLCache[str, Dict[str, Any]] = TTLCache(maxsize=10, ttl=300)
_introspection_cache: TTLCache[str, bool] = TTLCache(maxsize=1024, ttl=60)


@dataclass
class TokenPayload:
    sub: str
    preferred_username: Optional[str]
    email: Optional[str]
    realm_access: Dict[str, Any]
    raw: Dict[str, Any]


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


async def _fetch_jwks(realm: str | None = None) -> Dict[str, Any]:
    cache_key = f"jwks:{realm or settings.keycloak_realm}"
    if cache_key in _jwks_cache:
        return _jwks_cache[cache_key]

    url = f"{_issuer(realm)}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(url)
        response.raise_for_status()
        jwks = response.json()
        _jwks_cache[cache_key] = jwks
        return jwks


def _build_rsa_key(jwks: Dict[str, Any], kid: str) -> Dict[str, Any]:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key.get("use", "sig"),
                "n": key["n"],
                "e": key["e"],
            }
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Unable to find matching RSA key for token signature verification.",
    )


def _decode_unverified_header(token: str) -> Dict[str, Any]:
    try:
        return jwt.get_unverified_header(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token header format: {str(exc)}",
        ) from exc


async def _introspect_token(token: str) -> bool:
    if token in _introspection_cache:
        if _introspection_cache[token]:
            return True
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token introspection marked token as inactive.",
        )

    introspect_url = f"{_issuer()}/protocol/openid-connect/token/introspect"
    data = {
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
        "token": token,
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(introspect_url, data=data)
            resp.raise_for_status()
            res_data = resp.json()
            is_active = res_data.get("active", False)
            _introspection_cache[token] = is_active
            if not is_active:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token has been revoked or expired.",
                )
            return True
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Keycloak introspection failed: {str(exc)}",
        ) from exc


async def get_current_active_user(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> TokenPayload:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
        )

    token = credentials.credentials
    realm = _extract_realm_from_iss(token)

    try:
        header = _decode_unverified_header(token)
        kid = header.get("kid")
        if not kid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token missing kid in header",
            )

        jwks = await _fetch_jwks(realm)
        rsa_key = _build_rsa_key(jwks, kid)

        issuer_url = _issuer(realm)
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=settings.keycloak_client_id,
            issuer=issuer_url,
            options={"verify_aud": False},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(exc)}",
        ) from exc

    if settings.keycloak_introspect:
        await _introspect_token(token)

    token_payload = TokenPayload(
        sub=payload.get("sub"),
        preferred_username=payload.get("preferred_username"),
        email=payload.get("email"),
        realm_access=payload.get("realm_access", {}),
        raw=payload,
    )
    request.state.user_sub = token_payload.sub
    return token_payload


def _extract_roles(user: TokenPayload) -> list[str]:
    """Return effective roles from either Keycloak realm_access or local superadmin token."""
    raw = user.raw or {}
    if raw.get("type") == "superadmin":
        return [raw.get("role", "super_admin")]
    return user.realm_access.get("roles", []) if user.realm_access else []


def require_role(role: str):
    async def _role_dependency(user: TokenPayload = Depends(get_current_active_user)) -> TokenPayload:
        roles = _extract_roles(user)

        # Allow role aliases so "tech", "lab_technician", "admin", "super_admin", and "doctor" (for views) pass cleanly
        allowed_roles = {role}
        if role == "lab_technician":
            allowed_roles.update(["tech", "lab_technician", "admin", "super_admin"])
        elif role == "doctor":
            allowed_roles.update(["doctor", "lab_technician", "tech", "admin", "super_admin"])

        allowed = any(r in roles for r in allowed_roles) or "super_admin" in roles or "admin" in roles
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return user

    return _role_dependency


async def get_current_hospital_id(
        user: TokenPayload = Depends(get_current_active_user),
        db=Depends(get_db),
) -> str | None:
    record = db.query(User).filter(
        User.keycloak_sub == user.sub).one_or_none()
    if record and record.hospital_id:
        return record.hospital_id
    roles = _extract_roles(user)
    if "super_admin" in roles:
        return None
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="User is not associated with any hospital",
    )
