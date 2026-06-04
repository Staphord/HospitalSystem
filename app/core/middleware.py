from __future__ import annotations

import time
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

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

        if request.method in ("GET", "OPTIONS", "HEAD"):
            return response

        user_sub = getattr(request.state, "user_sub", "anonymous")

        try:
            db = get_session_local()()
            db.execute(
                text(
                    "INSERT INTO audit_logs "
                    "(request_id, user_sub, method, path, status_code, process_time_ms) "
                    "VALUES (:rid, :sub, :method, :path, :status, :ms)"
                ),
                {
                    "rid": request_id,
                    "sub": user_sub,
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


class TimingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        elapsed = time.monotonic() - start
        response.headers["X-Process-Time-Ms"] = str(round(elapsed * 1000, 1))
        return response
