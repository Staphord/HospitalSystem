import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.config import settings
from app.middleware import JWTVerificationMiddleware, AccessLogMiddleware
from app.rate_limit import limiter
from app.proxy import proxy_router
from shared.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("gateway.log", mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("api-gateway")


docs_url = None if settings.environment == "prod" else "/docs"
openapi_url = None if settings.environment == "prod" else "/openapi.json"

app = FastAPI(docs_url=docs_url, openapi_url=openapi_url, title="API Gateway")

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if settings.environment == "prod":
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


app.add_middleware(JWTVerificationMiddleware)
app.add_middleware(AccessLogMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

allowed_origins = [origin.strip()
                   for origin in settings.allowed_origins.split(",") if origin.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Impersonation-Banner", "X-Tenant-DB"],
    )


@app.get("/health")
async def health_check():
    import os, platform
    from datetime import datetime, timezone
    telemetry = {
        "status": "ok",
        "service": "api-gateway",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "system": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
    }
    try:
        import psutil
        telemetry["cpu"] = {
            "percent": psutil.cpu_percent(interval=0.1),
            "count": psutil.cpu_count(),
        }
        mem = psutil.virtual_memory()
        telemetry["memory"] = {
            "total": mem.total,
            "available": mem.available,
            "percent": mem.percent,
        }
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        telemetry["disk"] = {
            "total": disk.total,
            "free": disk.free,
            "percent": disk.percent,
        }
    except ImportError:
        pass
    return telemetry


app.include_router(proxy_router)
