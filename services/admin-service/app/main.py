import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.api.v1.router import router as api_v1_router
from app.config import settings
from app.core.limiter import limiter
from app.core.middleware import (
    AuditLogMiddleware,
    ImpersonationBannerMiddleware,
    ReadOnlyScopeMiddleware,
)
from shared.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("admin_service.log", mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("admin_service")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.core.database import init_db
    init_db()

    from app.db.master import get_master_db
    from app.db.base import Base

    master_db = get_master_db()
    try:
        Base.metadata.create_all(bind=master_db.connection())
    finally:
        master_db.close()

    logger.info("Admin service started")

    consumer_task = None
    backup_stop = asyncio.Event()
    backup_task = None
    try:
        from app.events.subscriber import start_subscriber

        consumer_task = asyncio.create_task(start_subscriber())
    except Exception:
        logger.exception("Failed to start RabbitMQ subscriber")

    try:
        from app.services.backup import backup_scheduler_loop

        backup_task = asyncio.create_task(backup_scheduler_loop(backup_stop))
    except Exception:
        logger.exception("Failed to start backup scheduler")

    yield

    backup_stop.set()
    if backup_task:
        backup_task.cancel()
        try:
            await backup_task
        except asyncio.CancelledError:
            pass

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    logger.info("Admin service stopped")


docs_url = None if settings.environment == "prod" else "/docs"
openapi_url = None if settings.environment == "prod" else "/openapi.json"

app = FastAPI(
    title="Hospital Flow Admin Service",
    docs_url=docs_url,
    openapi_url=openapi_url,
    lifespan=lifespan,
    openapi_tags=[
        {
            "name": "Wards & Beds",
            "description": (
                "Create and manage beds by ward_name. There is no separate wards table — "
                "POST /admin/beds with a ward_name defines the ward. "
                "Clinical assign/release lives on ward-service."
            ),
        },
    ],
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

allowed_origins = [origin.strip()
                   for origin in settings.allowed_origins.split(",") if origin.strip()]
if allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Impersonation-Banner"],
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if settings.environment == "prod":
        response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


app.add_middleware(ReadOnlyScopeMiddleware)
app.add_middleware(ImpersonationBannerMiddleware)
app.add_middleware(AuditLogMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "admin-service"}
