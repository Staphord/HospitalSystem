import pytest
from fastapi.responses import Response
from starlette.requests import Request

from app.core.middleware import AuditLogMiddleware, ImpersonationBannerMiddleware, ReadOnlyScopeMiddleware
from app.core.tenant_auth import TenantContext
from app import exceptions


def request(method="GET"):
    return Request({"type": "http", "method": method, "path": "/x", "headers": [], "client": ("127.0.0.1", 1)})


@pytest.mark.asyncio
async def test_scope_and_banner_middleware():
    async def next_call(req):
        return Response("ok")

    req = request("POST")
    req.state.tenant = TenantContext("t", "u", None, None, [], False, "readonly")
    blocked = await ReadOnlyScopeMiddleware(None).dispatch(req, next_call)
    assert blocked.status_code == 403
    req2 = request()
    req2.state.tenant = req.state.tenant
    response = await ImpersonationBannerMiddleware(None).dispatch(req2, next_call)
    assert response.headers["X-Impersonation-Banner"] == "true"


@pytest.mark.asyncio
async def test_audit_middleware_safe_paths_and_headers(monkeypatch):
    async def next_call(req):
        return Response("ok")

    middleware = AuditLogMiddleware(None)
    response = await middleware.dispatch(request("GET"), next_call)
    assert "X-Request-ID" in response.headers
    response = await middleware.dispatch(request("OPTIONS"), next_call)
    assert "X-Request-ID" not in response.headers


def test_http_exception_types_have_expected_statuses():
    assert exceptions.UnauthorizedError().status_code == 401
    assert exceptions.ForbiddenError().status_code == 403
    assert exceptions.NotFoundError().status_code == 404
    assert exceptions.ConflictError().status_code == 409
    assert exceptions.BadRequestError().status_code == 400
    assert exceptions.RateLimitError().status_code == 429
    assert exceptions.TenantNotFoundError().status_code == 404
    assert exceptions.TokenExpiredError().status_code == 401
    assert exceptions.MFARequiredError().status_code == 401
    assert exceptions.TenantSuspendedError().status_code == 403
    assert exceptions.ReadOnlyScopeError().status_code == 403

