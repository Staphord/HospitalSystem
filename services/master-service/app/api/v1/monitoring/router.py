import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.security import get_current_active_user, require_role, TokenPayload
from app.core.database import get_db
from app.core.limiter import limiter
from app.models.master import Tenant

logger = logging.getLogger("master_service.monitoring")

router = APIRouter(dependencies=[Depends(require_role("super_admin"))])


@router.get("/telemetry", response_model=dict, tags=["Monitoring"])
@limiter.limit("60/minute")
async def monitoring_telemetry(
    request,
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Return system telemetry data (CPU, RAM, disk, DB connections)."""
    import os, platform
    from datetime import datetime, timezone

    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "master-service",
        "system": {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        },
    }
    try:
        import psutil
        data["cpu"] = {
            "percent": psutil.cpu_percent(interval=0.1),
            "count": psutil.cpu_count(),
            "per_cpu": psutil.cpu_percent(interval=0.1, percpu=True),
        }
        mem = psutil.virtual_memory()
        data["memory"] = {
            "total": mem.total,
            "available": mem.available,
            "used": mem.used,
            "percent": mem.percent,
        }
        disk = psutil.disk_usage(os.path.abspath(os.sep))
        data["disk"] = {
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": disk.percent,
        }
    except ImportError:
        pass
    try:
        from app.config import settings
        from sqlalchemy import create_engine, text as sa_text
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            result = conn.execute(sa_text("SELECT count(*) FROM pg_stat_activity"))
            data["db_connections"] = {"active": result.scalar() or 0}
            result = conn.execute(sa_text("SELECT pg_database_size(current_database())"))
            data["db_size_bytes"] = result.scalar() or 0
        engine.dispose()
    except Exception as exc:
        data["db_error"] = str(exc)[:100]
    return data


@router.get("/tenant-counts", response_model=dict, tags=["Monitoring"])
@limiter.limit("60/minute")
async def monitoring_tenant_counts(
    request,
    db: Session = Depends(get_db),
    _: TokenPayload = Depends(get_current_active_user),
) -> dict:
    """Return counts of tenants by status."""
    from sqlalchemy import text as sa_text

    total = db.query(Tenant).count()
    active = db.query(Tenant).filter(Tenant.is_active == True).count()
    suspended = db.query(Tenant).filter(Tenant.status == "suspended").count()
    terminated = db.query(Tenant).filter(Tenant.status == "terminated").count()
    trial = db.query(Tenant).filter(Tenant.status == "trial").count()

    return {
        "total": total,
        "active": active,
        "suspended": suspended,
        "terminated": terminated,
        "trial": trial,
    }
