from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass

import httpx
from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from app.config import settings
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
            return key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _decode_token(token: str) -> Dict[str, Any]:
    try:
        headers = jwt.get_unverified_header(token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    kid = headers.get("kid")

    # Local superadmin tokens are HS256 signed with SECRET_KEY and have no kid
    if not kid:
        try:
            payload = jwt.decode(
                token,
                settings.secret_key,
                algorithms=["HS256"],
                options={"verify_exp": True, "verify_aud": False},
            )
            return payload
        except jwt.ExpiredSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc

    # Multi-realm: derive realm from the token's issuer claim
    token_realm = _extract_realm_from_iss(token)
    jwks = await _fetch_jwks(token_realm)
    rsa_key = _build_rsa_key(jwks, kid)

    try:
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            issuer=_issuer(token_realm),
            options={"verify_exp": True, "verify_aud": False},
        )
        return payload
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def _introspect_token(token: str) -> None:
    if token in _introspection_cache:
        if not _introspection_cache[token]:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Inactive token",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return

    token_realm = _extract_realm_from_iss(token)
    url = f"{_issuer(token_realm)}/protocol/openid-connect/token/introspect"
    data = {
        "token": token,
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(url, data=data)
        response.raise_for_status()
        payload = response.json()
        active = bool(payload.get("active"))
        _introspection_cache[token] = active
        if not active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Inactive token",
                headers={"WWW-Authenticate": "Bearer"},
            )


async def get_current_active_user(
        request: Request,
        credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TokenPayload:
    if credentials is None or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = await _decode_token(token)

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
        allowed = role in roles or "super_admin" in roles
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
