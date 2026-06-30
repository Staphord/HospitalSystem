from cachetools import TTLCache
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt

from app.config import settings

_bearer_scheme = HTTPBearer(auto_error=False)
_jwks_cache: TTLCache = TTLCache(maxsize=10, ttl=300)


def _issuer(realm=None):
    return f"{settings.keycloak_url}/realms/{realm or settings.keycloak_realm}"


def _extract_realm_from_iss(token: str) -> str | None:
    try:
        claims = jwt.get_unverified_claims(token)
        iss = claims.get("iss", "")
        if iss.startswith(settings.keycloak_url + "/realms/"):
            return iss.split("/realms/", 1)[1]
    except Exception:
        pass
    return None


async def _fetch_jwks(realm=None):
    import httpx
    cache_key = f"jwks:{realm or settings.keycloak_realm}"
    if cache_key in _jwks_cache:
        return _jwks_cache[cache_key]
    url = f"{_issuer(realm)}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()
        _jwks_cache[cache_key] = data
        return data


def _build_rsa_key(jwks: dict, kid: str) -> dict:
    for key in jwks.get("keys", []):
        if key.get("kid") == kid:
            return key
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


async def _decode_token(token: str) -> dict:
    try:
        headers = jwt.get_unverified_header(token)
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    kid = headers.get("kid")
    if not kid:
        try:
            return jwt.decode(
                token, settings.secret_key, algorithms=["HS256"],
                options={"verify_exp": True, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
        except Exception as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    token_realm = _extract_realm_from_iss(token)
    jwks = await _fetch_jwks(token_realm)
    rsa_key = _build_rsa_key(jwks, kid)
    try:
        return jwt.decode(
            token, rsa_key, algorithms=["RS256"],
            options={"verify_exp": True, "verify_aud": False, "verify_iss": False},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


async def get_current_active_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> dict:
    if not credentials or not credentials.scheme.lower() == "bearer":
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = await _decode_token(credentials.credentials)

    if settings.keycloak_introspect:
        await _introspect_token(credentials.credentials)

    return payload


async def _introspect_token(token: str) -> None:
    import httpx
    token_realm = _extract_realm_from_iss(token)
    url = f"{_issuer(token_realm)}/protocol/openid-connect/token/introspect"
    data = {
        "token": token,
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("active"):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED,
                detail="Inactive token",
                headers={"WWW-Authenticate": "Bearer"},
            )


def _extract_roles(payload: dict) -> list[str]:
    if payload.get("type") == "superadmin":
        return [payload.get("role", "super_admin")]
    return payload.get("realm_access", {}).get("roles", [])


def require_role(role: str):
    async def _role_dependency(payload: dict = Depends(get_current_active_user)):
        roles = _extract_roles(payload)
        allowed = role in roles or "super_admin" in roles
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload
    return _role_dependency


def require_any_role(allowed_roles: list[str]):
    async def _role_dependency(payload: dict = Depends(get_current_active_user)):
        roles = _extract_roles(payload)
        allowed = any(r in roles for r in allowed_roles) or "super_admin" in roles
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return payload
    return _role_dependency

