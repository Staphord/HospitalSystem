from __future__ import annotations

import time
import uuid
from typing import Any, Callable

import httpx
from fastapi import HTTPException, Request, status
from jose import jwt
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import settings

_bearer_scheme = None
_jwks_cache: dict[str, Any] = {}


def _issuer() -> str:
    return f"{settings.keycloak_url}/realms/{settings.keycloak_realm}"


async def _fetch_jwks() -> dict[str, Any]:
    from cachetools import TTLCache
    cache = getattr(_fetch_jwks, "_cache", None)
    if cache is None:
        cache = TTLCache(maxsize=1, ttl=300)
        setattr(_fetch_jwks, "_cache", cache)
    key = "jwks"
    if key in cache:
        return cache[key]
    url = f"{_issuer()}/protocol/openid-connect/certs"
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.get(url)
        resp.raise_for_status()
        data = resp.json()
        cache[key] = data
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
            return jwt.decode(
                token, settings.secret_key, algorithms=["HS256"],
                options={"verify_exp": True, "verify_aud": False},
            )
        except jwt.ExpiredSignatureError as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
        except Exception as e:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e

    jwks = await _fetch_jwks()
    rsa_key = _build_rsa_key(jwks, kid)
    try:
        return jwt.decode(
            token, rsa_key, algorithms=["RS256"],
            issuer=_issuer(), options={"verify_exp": True, "verify_aud": False},
        )
    except jwt.ExpiredSignatureError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Token has expired") from e
    except Exception as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from e


async def _introspect_token(token: str) -> None:
    from cachetools import TTLCache
    cache = getattr(_introspect_token, "_cache", None)
    if cache is None:
        cache = TTLCache(maxsize=1024, ttl=60)
        setattr(_introspect_token, "_cache", cache)
    if token in cache:
        if not cache[token]:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Inactive token")
        return

    url = f"{_issuer()}/protocol/openid-connect/token/introspect"
    data = {
        "token": token,
        "client_id": settings.keycloak_client_id,
        "client_secret": settings.keycloak_client_secret,
    }
    async with httpx.AsyncClient(timeout=10.0) as c:
        resp = await c.post(url, data=data)
        resp.raise_for_status()
        payload = resp.json()
        active = bool(payload.get("active"))
        cache[token] = active
        if not active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Inactive token")


class JWTVerificationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # Skip health and docs
        if path in ("/health", "/docs", "/openapi.json", "/redoc"):
            return await call_next(request)

        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing bearer token"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth[7:]
        try:
            payload = await _decode_token(token)
        except HTTPException as e:
            from fastapi.responses import JSONResponse
            return JSONResponse(
                status_code=e.status_code,
                content={"detail": e.detail},
                headers=e.headers or {},
            )

        if settings.keycloak_introspect:
            try:
                await _introspect_token(token)
            except HTTPException as e:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=e.status_code,
                    content={"detail": e.detail},
                    headers=e.headers or {},
                )

        tenant_id = payload.get("tenant_id")
        is_super = payload.get("type") == "superadmin" or "super_admin" in payload.get("realm_access", {}).get("roles", [])

        if not is_super and tenant_id:
            if await is_tenant_suspended(tenant_id):
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=status.HTTP_403_FORBIDDEN,
                    content={"code": "TENANT_SUSPENDED", "message": "Tenant subscription is suspended"},
                )

        request.state.user_sub = payload.get("sub")
        request.state.tenant_id = payload.get("tenant_id")
        request.state.token_payload = payload

        return await call_next(request)


class AccessLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start_time = time.monotonic()

        if request.method in ("OPTIONS", "HEAD"):
            return await call_next(request)

        response = await call_next(request)

        process_time = time.monotonic() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = str(round(process_time * 1000, 1))

        return response
