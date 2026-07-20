from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("admin_service.middleware")


class AuditLogMiddleware(BaseHTTPMiddleware):
    """Write HTTP-level audit rows into the tenant DB audit_logs table."""

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

        if request.method not in ("GET", "OPTIONS", "HEAD"):
            await self._write_tenant_audit(request, response, request_id, process_time)

        return response

    async def _write_tenant_audit(
        self,
        request: Request,
        response: Response,
        request_id: str,
        process_time: float,
    ) -> None:
        tenant = getattr(request.state, "tenant", None)
        tenant_id = getattr(tenant, "tenant_id", None) if tenant else None
        if not tenant_id:
            return

        user_sub = getattr(request.state, "user_sub", None) or "anonymous"
        ip = request.client.host if request.client else None

        try:
            from app.db.tenant_sync import _get_tenant_engine
            from app.services import audit_service

            _, SessionLocal = _get_tenant_engine(tenant_id)
            db = SessionLocal()
            try:
                audit_service.log_change(
                    db,
                    user_id=str(user_sub),
                    action=request.method,
                    table_name="http_request",
                    record_id=request_id,
                    new_values={
                        "path": request.url.path,
                        "status_code": response.status_code,
                        "process_time_ms": round(process_time * 1000, 1),
                    },
                    ip_address=ip,
                    session_id=request_id,
                )
            finally:
                db.close()
        except Exception:
            logger.exception(
                "Failed tenant audit_logs write for %s %s",
                request.method,
                request.url.path,
            )


class ReadOnlyScopeMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            tenant = getattr(request.state, "tenant", None)
            if tenant and hasattr(tenant, "scope") and tenant.scope == "readonly":
                from fastapi.responses import JSONResponse

                return JSONResponse(
                    status_code=403,
                    content={
                        "code": "READ_ONLY_SCOPE",
                        "message": "Write operations are not allowed in readonly mode",
                    },
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
