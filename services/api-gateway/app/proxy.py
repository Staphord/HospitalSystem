from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request, Response

from app.config import settings
from app.rate_limit import limiter
from app.tenant import get_tenant_db_url, is_tenant_suspended

logger = logging.getLogger("api-gateway")
proxy_router = APIRouter()

_http_client: httpx.AsyncClient | None = None

ROUTE_TABLE = {
    "/api/v1/auth": settings.auth_service_url,
    "/api/v1/superadmin": settings.master_service_url,
    "/api/v1/monitoring": settings.master_service_url,
    "/api/v1/admin": settings.admin_service_url,
    "/api/v1/tenant": settings.master_service_url,
    "/api/v1/reception": settings.reception_service_url,
    "/api/v1/triage": settings.triage_service_url,
    "/api/v1/consultation": settings.consultation_service_url,
    "/api/v1/laboratory": settings.laboratory_service_url,
    "/api/v1/radiology": settings.radiology_service_url,
    "/api/v1/pharmacy": settings.pharmacy_service_url,
    "/api/v1/billing": settings.billing_service_url,
    "/api/v1/ward": settings.ward_service_url,
    "/api/v1/notifications": settings.notification_service_url,
    "/api/v1/reports": settings.report_service_url,
    "/api/v1/me": settings.auth_service_url,
    "/api/v1/patients": settings.patient_service_url,
    "/api/v1/visits": settings.visit_service_url,
}


def resolve_service(path: str) -> str | None:
    for prefix, url in ROUTE_TABLE.items():
        if path.startswith(prefix):
            return url
    return None


def _get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=30.0)
    return _http_client


@proxy_router.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
@limiter.limit("100/minute")
async def proxy(request: Request, full_path: str):
    path = f"/{full_path}"
    if path.startswith("/health"):
        return Response(content='{"status":"ok"}', media_type="application/json")

    target_url = resolve_service(path)
    if target_url is None:
        return Response(
            content='{"detail":"Not found"}',
            status_code=404,
            media_type="application/json",
        )

    # Verify tenant suspension
    tenant_id = getattr(request.state, "tenant_id", None)
    payload = getattr(request.state, "token_payload", {})
    is_super = payload.get("type") == "superadmin" or "super_admin" in payload.get("realm_access", {}).get("roles", [])

    if not is_super and tenant_id:
        if await is_tenant_suspended(tenant_id):
            from fastapi import HTTPException, status
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "TENANT_SUSPENDED", "message": "Tenant subscription is suspended"},
            )

    # Build target URL
    target = f"{target_url}{path}"
    query = str(request.query_params)
    if query:
        target = f"{target}?{query}"

    # Forward headers
    headers = {}
    for k, v in request.headers.items():
        if k.lower() not in ("host", "content-length"):
            headers[k] = v

    # Attach X-Tenant-DB header
    if tenant_id:
        db_url = await get_tenant_db_url(tenant_id)
        if db_url:
            headers["X-Tenant-DB"] = db_url

    # Attach request ID
    request_id = getattr(request.state, "request_id", None)
    if request_id:
        headers["X-Request-ID"] = request_id

    body = await request.body()

    client = _get_client()
    response = await client.request(
        method=request.method,
        url=target,
        headers=headers,
        content=body,
    )

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.headers.get("content-type"),
    )
