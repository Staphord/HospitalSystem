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
        logging.FileHandler("master_service.log", mode="a"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("master_service")


def _find_migrations_dir() -> str | None:
    """Return the absolute path to the master migrations directory, if found."""
    import os

    candidates = [
        # Monorepo layout from services/master-service/app/main.py
        os.path.join(os.path.dirname(__file__), "..", "..", "..", "migrations", "master"),
        # Docker layout from /app/app/main.py with migrations mounted at /app/migrations
        os.path.join(os.path.dirname(__file__), "..", "migrations", "master"),
        # Fallback via MIGRATIONS_DIR env var
        os.environ.get("MASTER_MIGRATIONS_DIR", ""),
    ]
    for candidate in candidates:
        if candidate and os.path.isdir(candidate):
            return os.path.abspath(candidate)
    return None


def _stamp_existing_schema_if_needed(migrations_dir: str) -> None:
    """Bootstrap alembic for databases created outside alembic.

    If the database already has the tenants table but no alembic_version record,
    detect whether the schema looks like 0001 or head and stamp the appropriate
    revision so that subsequent upgrades only apply real deltas.
    """
    import os
    import subprocess
    import sys

    from sqlalchemy import create_engine, text
    from sqlalchemy.exc import SQLAlchemyError

    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            alembic_exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
                )
            ).scalar()
            if alembic_exists:
                return

            tenants_exists = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_name = 'tenants'"
                )
            ).scalar()
            if not tenants_exists:
                # Fresh database; alembic upgrade will create everything.
                return

            # Detect which migration revision best matches the existing schema
            # to prevent skipping migrations (e.g. 0003 adds country/city, 0002 adds subscription_status)
            has_country = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'tenants' "
                    "AND column_name = 'country'"
                )
            ).scalar()
            has_sub_status = conn.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = 'tenants' "
                    "AND column_name = 'subscription_status'"
                )
            ).scalar()

            if has_country:
                target_revision = "0003_add_saas_schema"
            elif has_sub_status:
                target_revision = "0002_add_subscription_lifecycle"
            else:
                target_revision = "0001_initial_master_schema"

            logger.info("Bootstrapping alembic revision for existing schema: %s", target_revision)
            result = subprocess.run(
                [sys.executable, "-m", "alembic", "stamp", target_revision],
                cwd=migrations_dir,
                capture_output=True,
                text=True,
                check=True,
                env={**dict(os.environ), "DATABASE_URL": settings.database_url},
            )
            if result.stdout:
                logger.debug(result.stdout)
    except subprocess.CalledProcessError as exc:
        logger.error("Failed to stamp existing schema: %s\n%s", exc.stderr, exc.stdout)
        if settings.environment == "prod":
            raise
    except SQLAlchemyError as exc:
        logger.error("Database connectivity error during migration bootstrap: %s", exc)
        if settings.environment == "prod":
            raise
    finally:
        engine.dispose()


def _run_migrations() -> None:
    """Run Alembic migrations for the master database on startup.

    Supports both monorepo layout (project root / migrations / master) and the
    Docker service layout where migrations are mounted next to the app package.
    """
    import os
    import subprocess
    import sys

    migrations_dir = _find_migrations_dir()
    if not migrations_dir:
        logger.warning("Master migrations directory not found; skipping alembic")
        return

    _stamp_existing_schema_if_needed(migrations_dir)

    try:
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=migrations_dir,
            capture_output=True,
            text=True,
            check=True,
            env={**dict(os.environ), "DATABASE_URL": settings.database_url},
        )
        logger.info("Master DB migrations applied successfully")
        if result.stdout:
            logger.debug(result.stdout)
    except subprocess.CalledProcessError as exc:
        logger.error("Master DB migration failed: %s\n%s", exc.stderr, exc.stdout)
        # Do not crash the app in development, but log loudly.
        if settings.environment == "prod":
            raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    _run_migrations()

    from app.core.database import init_db
    init_db()

    # Seed canonical subscription plans into the DB catalog.
    from app.db.master import get_master_db
    from app.services.subscription_plans import sync_plans_to_db
    plan_db = get_master_db()
    try:
        sync_plans_to_db(plan_db)
    finally:
        plan_db.close()



    task = asyncio.create_task(_start_suspension_loop())
    logger.info("Suspension background task started")

    consumer_task = None
    try:
        from app.events import subscriber as _sub
        if hasattr(_sub, "start_subscriber"):
            consumer_task = asyncio.create_task(_sub.start_subscriber())
    except Exception:
        pass

    yield

    if consumer_task:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Suspension background task stopped")


async def _start_suspension_loop() -> None:
    from app.services.suspension_job import suspension_loop
    await suspension_loop()


docs_url = None if settings.environment == "prod" else "/docs"
openapi_url = None if settings.environment == "prod" else "/openapi.json"

app = FastAPI(
    title="Hospital Flow Master Service",
    docs_url=docs_url,
    openapi_url=openapi_url,
    lifespan=lifespan,
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

from fastapi.staticfiles import StaticFiles
import os

static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(os.path.join(static_dir, "logos"), exist_ok=True)

app.mount("/api/v1/superadmin/static", StaticFiles(directory=static_dir), name="static")

app.include_router(api_v1_router, prefix="/api/v1")


@app.get("/health")
async def health():
    import os, platform
    from datetime import datetime, timezone

    telemetry = {
        "status": "ok",
        "service": "master-service",
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
    try:
        from app.config import settings
        from sqlalchemy import create_engine, text
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(text("SELECT count(*) FROM pg_stat_activity"))
            telemetry["db_connections"] = {"active": result.scalar() or 0}
        engine.dispose()
    except Exception:
        pass
    return telemetry
