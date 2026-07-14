"""Shared security middleware for Hospital Flow microservices.

Provides body size limiting, referrer policy, and other common
security headers that should be applied to all service entry points.
"""

import logging
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

DEFAULT_MAX_BODY_SIZE = 10 * 1024 * 1024  # 10 MB


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with a body larger than the configured limit."""

    def __init__(self, app, max_bytes: int = DEFAULT_MAX_BODY_SIZE):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Callable):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.max_bytes:
                    from fastapi.responses import JSONResponse
                    return JSONResponse(
                        status_code=413,
                        content={
                            "code": "PAYLOAD_TOO_LARGE",
                            "message": f"Request body exceeds {self.max_bytes} bytes",
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Append additional security headers to every response.

    Complements the per-service security_headers middleware by adding
    headers that are common across all services.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response
