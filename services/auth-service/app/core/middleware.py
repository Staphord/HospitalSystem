from __future__ import annotations

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_session_local


class AuditLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start_time = time.monotonic()

        if request.method in ("OPTIONS", "HEAD"):
            return await call_next(request)

        response = await call_next(request)

        process_time = time.monotonic() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time-Ms"] = str(round(process_time * 1000, 1))

        user_sub = getattr(request.state, "user_sub", "anonymous")
        tenant = getattr(request.state, "tenant", None)
        tenant_id = tenant.tenant_id if tenant else None

        if request.method not in ("GET", "OPTIONS", "HEAD"):
            try:
                db = get_session_local()()
                db.execute(
                    text(
                        "INSERT INTO audit_logs "
                        "(request_id, user_sub, tenant_id, method, path, status_code, process_time_ms) "
                        "VALUES (:rid, :sub, :tid, :method, :path, :status, :ms)"
                    ),
                    {
                        "rid": request_id,
                        "sub": user_sub,
                        "tid": tenant_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": response.status_code,
                        "ms": round(process_time * 1000, 1),
                    },
                )
                db.commit()
            except Exception:
                pass
            finally:
                db.close()

        return response


class ReadOnlyScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            tenant = getattr(request.state, "tenant", None)
            if tenant and hasattr(tenant, "scope") and tenant.scope == "readonly":
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=403,
                    content={"code": "READ_ONLY_SCOPE", "message": "Write operations are not allowed in readonly mode"},
                    headers={"X-Impersonation-Banner": "true"},
                )
        return await call_next(request)


class ImpersonationBannerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        tenant = getattr(request.state, "tenant", None)
        if tenant and getattr(tenant, "scope", None) == "readonly":
            response.headers["X-Impersonation-Banner"] = "true"
        return response
