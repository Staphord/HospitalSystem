"""Tenant database backup jobs (FR-58)."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.config import settings
from app.db.master import get_master_db
from app.models.admin import BackupJob
from app.services.tenant_service import decrypt_dsn

logger = logging.getLogger("admin_service.backup")


def _tenant_backup_dir(tenant_id: str) -> Path:
    root = Path(settings.backup_root).resolve()
    path = (root / tenant_id).resolve()
    if not str(path).startswith(str(root)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid tenant path")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _dsn_to_pg_env(dsn: str) -> dict[str, str]:
    parsed = urlparse(dsn)
    env = os.environ.copy()
    env["PGHOST"] = parsed.hostname or "localhost"
    env["PGPORT"] = str(parsed.port or 5432)
    env["PGUSER"] = parsed.username or "postgres"
    env["PGPASSWORD"] = parsed.password or ""
    env["PGDATABASE"] = (parsed.path or "/").lstrip("/")
    return env


def list_backups(db: Session, tenant_id: str, limit: int = 50) -> list[BackupJob]:
    return (
        db.query(BackupJob)
        .filter(BackupJob.tenant_id == tenant_id)
        .order_by(BackupJob.started_at.desc())
        .limit(min(limit, 200))
        .all()
    )


def get_backup(db: Session, tenant_id: str, backup_id: uuid.UUID) -> BackupJob:
    row = (
        db.query(BackupJob)
        .filter(BackupJob.backup_id == backup_id, BackupJob.tenant_id == tenant_id)
        .first()
    )
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Backup not found")
    return row


def backup_status(db: Session, tenant_id: str) -> dict:
    last = (
        db.query(BackupJob)
        .filter(BackupJob.tenant_id == tenant_id, BackupJob.status == "completed")
        .order_by(BackupJob.finished_at.desc())
        .first()
    )
    next_run = datetime.now(timezone.utc) + timedelta(seconds=settings.backup_check_interval)
    return {
        "last_success_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "last_backup_id": str(last.backup_id) if last else None,
        "schedule_interval_seconds": settings.backup_check_interval,
        "retention_days": settings.backup_retention_days,
        "approx_next_check_at": next_run.isoformat(),
    }


def create_backup_job(
    db: Session,
    *,
    tenant_id: str,
    triggered_by: str,
    triggered_by_sub: str | None,
) -> BackupJob:
    job = BackupJob(
        backup_id=uuid.uuid4(),
        tenant_id=tenant_id,
        status="pending",
        triggered_by=triggered_by,
        triggered_by_sub=triggered_by_sub,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def run_backup_job(db: Session, job: BackupJob) -> BackupJob:
    job.status = "running"
    db.commit()

    master = get_master_db()
    try:
        from sqlalchemy import text

        enc = master.execute(
            text(
                "SELECT db_connection_string FROM tenants WHERE tenant_id = :tid AND is_active = true"
            ),
            {"tid": job.tenant_id},
        ).scalar()
        if not enc:
            raise RuntimeError("Tenant DSN not found")
        dsn = decrypt_dsn(str(enc))
    finally:
        master.close()

    out_dir = _tenant_backup_dir(job.tenant_id)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_file = out_dir / f"{job.tenant_id}_{stamp}_{job.backup_id}.sql"
    env = _dsn_to_pg_env(dsn)

    try:
        result = subprocess.run(
            ["pg_dump", "--no-owner", "--no-acl", "-f", str(out_file)],
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr or result.stdout or "pg_dump failed")
        size = out_file.stat().st_size
        job.status = "completed"
        job.file_path = str(out_file)
        job.size_bytes = size
        job.finished_at = datetime.now(timezone.utc)
        job.error = None
    except Exception as e:
        logger.exception("Backup failed for %s", job.tenant_id)
        job.status = "failed"
        job.error = str(e)
        job.finished_at = datetime.now(timezone.utc)
        if out_file.exists():
            try:
                out_file.unlink()
            except Exception:
                pass
    db.commit()
    db.refresh(job)
    _prune_old_backups(db, job.tenant_id)
    return job


def _prune_old_backups(db: Session, tenant_id: str) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.backup_retention_days)
    old = (
        db.query(BackupJob)
        .filter(BackupJob.tenant_id == tenant_id, BackupJob.started_at < cutoff)
        .all()
    )
    for job in old:
        if job.file_path:
            try:
                p = Path(job.file_path)
                root = Path(settings.backup_root).resolve()
                if p.resolve().is_file() and str(p.resolve()).startswith(str(root)):
                    p.unlink(missing_ok=True)
            except Exception:
                pass
        db.delete(job)
    db.commit()


def resolve_download_path(job: BackupJob) -> Path:
    if job.status != "completed" or not job.file_path:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Backup file not available")
    path = Path(job.file_path).resolve()
    root = Path(settings.backup_root).resolve()
    if not str(path).startswith(str(root)) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Backup file missing")
    # Ensure file is under tenant directory
    tenant_dir = (root / job.tenant_id).resolve()
    if not str(path).startswith(str(tenant_dir)):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Invalid backup path")
    return path


async def backup_scheduler_loop(stop_event: asyncio.Event) -> None:
    """Periodically back up all active tenants."""
    while not stop_event.is_set():
        try:
            await asyncio.to_thread(_run_scheduled_backups)
        except Exception:
            logger.exception("Scheduled backup cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.backup_check_interval)
        except asyncio.TimeoutError:
            continue


def _run_scheduled_backups() -> None:
    from app.db.tenant_sync import _get_tenant_engine

    master = get_master_db()
    try:
        from sqlalchemy import text

        tenants = master.execute(
            text("SELECT tenant_id FROM tenants WHERE is_active = true AND status = 'active'")
        ).fetchall()
    finally:
        master.close()

    for (tenant_id,) in tenants:
        try:
            _, SessionLocal = _get_tenant_engine(tenant_id)
            db = SessionLocal()
            try:
                # Skip if a successful backup exists within interval
                since = datetime.now(timezone.utc) - timedelta(seconds=settings.backup_check_interval)
                recent = (
                    db.query(BackupJob)
                    .filter(
                        BackupJob.tenant_id == tenant_id,
                        BackupJob.status == "completed",
                        BackupJob.finished_at >= since,
                    )
                    .first()
                )
                if recent:
                    continue
                job = create_backup_job(
                    db,
                    tenant_id=tenant_id,
                    triggered_by="system",
                    triggered_by_sub=None,
                )
                run_backup_job(db, job)
            finally:
                db.close()
        except Exception:
            logger.exception("Scheduled backup failed for tenant %s", tenant_id)
