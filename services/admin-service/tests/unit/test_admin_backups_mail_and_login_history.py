from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.config import settings
from app.services import backup, login_history, mail


def request(headers):
    return Request({"type": "http", "method": "GET", "path": "/", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "client": ("127.0.0.1", 1), "scheme": "https", "server": ("x", 443)})


def test_login_urls_and_history_statuses():
    assert mail.login_url_from_request(request({"referer": "https://portal.example/a"})) == "https://portal.example/login"
    assert mail.login_url_from_request(request({"origin": "https://portal.example"})) == "https://portal.example/login"
    rows = [SimpleNamespace(action="LOGIN", detail=None, created_at=datetime.now(timezone.utc), ip_address="1.1.1.1"), SimpleNamespace(action="LOGOUT", detail=None, created_at=datetime.now(timezone.utc), ip_address=None), SimpleNamespace(action="LOGIN", detail="failed password", created_at=datetime.now(timezone.utc), ip_address=None)]
    db = MagicMock(); db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = rows
    assert [x["status"] for x in login_history.list_login_history(db, user_sub="u", tenant_id="t")] == ["Success", "Expired", "Failed"]
    assert login_history.list_login_history(db, user_sub="u", tenant_id=None) is not None
    with patch.object(mail.settings, "frontend_url", "https://portal.example"):
        assert mail.login_url_from_request(request({})) == "https://portal.example/login"
    assert mail.login_url_from_request(request({"referer": "not-a-url", "origin": "https://portal.example"})) == "https://portal.example/login"


@pytest.mark.asyncio
async def test_welcome_email_mock_and_smtp_paths():
    with patch.object(mail.settings, "smtp_user", ""), patch.object(mail.settings, "smtp_password", ""):
        await mail.send_staff_welcome_email(email="x@y.com", full_name="", username="user", password=None, role="doctor", hospital_name="H", login_url="https://h/login")
        await mail.send_staff_welcome_email(email="x@y.com", full_name="", username="user", password="Temp1!", role="doctor", hospital_name="H", login_url="https://h/login")
    with patch.object(mail.settings, "smtp_user", "u"), patch.object(mail.settings, "smtp_password", "p"), patch("aiosmtplib.send", new_callable=AsyncMock) as send:
        await mail.send_staff_welcome_email(email="x@y.com", full_name="User", username="user", password="Secret1!", role="hospital_admin", hospital_name="H", login_url="https://h/login")
        send.assert_awaited_once()
    with patch.object(mail.settings, "smtp_user", "u"), patch.object(mail.settings, "smtp_password", "p"), patch("aiosmtplib.send", new_callable=AsyncMock, side_effect=RuntimeError("smtp")):
        await mail.send_staff_welcome_email(email="x@y.com", full_name="User", username="user", password=None, role="doctor", hospital_name="H", login_url="https://h/login")


def test_backup_file_guards_and_status():
    with patch.object(backup.settings, "backup_root", "/tmp/admin-backups"):
        path = backup._tenant_backup_dir("tenant"); assert path.name == "tenant"
        assert backup._dsn_to_pg_env("postgresql://u:p@host:5433/db")["PGPORT"] == "5433"
        job = SimpleNamespace(status="pending", file_path=None, tenant_id="tenant")
        with pytest.raises(HTTPException): backup.resolve_download_path(job)
        job.status = "completed"; job.file_path = "/tmp/not-under-root.sql"
        with pytest.raises(HTTPException): backup.resolve_download_path(job)
        wrong = Path("/tmp/admin-backups/other/file.sql"); wrong.parent.mkdir(parents=True, exist_ok=True); wrong.write_text("x")
        job.file_path = str(wrong)
        with pytest.raises(HTTPException): backup.resolve_download_path(job)
    db = MagicMock(); db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    assert backup.backup_status(db, "tenant")["last_success_at"] is None


def test_backup_lookup_and_path_security():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException):
        backup.get_backup(db, "t", uuid4())
    with patch.object(backup.settings, "backup_root", "/tmp/admin-backups"):
        with pytest.raises(HTTPException):
            backup._tenant_backup_dir("../../escape")
        job = SimpleNamespace(status="completed", file_path="/tmp/admin-backups/other/file.sql", tenant_id="tenant")
        with pytest.raises(HTTPException):
            backup.resolve_download_path(job)


def test_backup_run_success_and_failure_paths(tmp_path):
    job = SimpleNamespace(tenant_id="t", backup_id=uuid4(), status="pending", file_path=None, size_bytes=None, finished_at=None, error=None)
    db = MagicMock(); master = MagicMock(); master.execute.return_value.scalar.return_value = "enc"
    out = tmp_path / "t.sql"
    with patch.object(backup, "get_master_db", return_value=master), patch.object(backup, "decrypt_dsn", return_value="postgresql://u:p@h/db"), patch.object(backup, "_tenant_backup_dir", return_value=tmp_path), patch.object(backup, "_dsn_to_pg_env", return_value={}), patch("subprocess.run", return_value=SimpleNamespace(returncode=0, stderr="", stdout="")), patch.object(Path, "stat", return_value=SimpleNamespace(st_size=3)), patch.object(backup, "_prune_old_backups"):
        out.write_text("sql")
        assert backup.run_backup_job(db, job).status == "completed"
    job = SimpleNamespace(tenant_id="t", backup_id=uuid4(), status="pending", file_path=None, size_bytes=None, finished_at=None, error=None)
    with patch.object(backup, "get_master_db", return_value=master), patch.object(backup, "decrypt_dsn", return_value="enc"), patch.object(backup, "_tenant_backup_dir", return_value=tmp_path), patch.object(backup, "_dsn_to_pg_env", return_value={}), patch("subprocess.run", side_effect=RuntimeError("pg_dump")), patch.object(backup, "_prune_old_backups"):
        assert backup.run_backup_job(db, job).status == "failed"
    master.execute.return_value.scalar.return_value = None
    with patch.object(backup, "get_master_db", return_value=master):
        with pytest.raises(RuntimeError, match="DSN"):
            backup.run_backup_job(db, job)
    master.execute.return_value.scalar.return_value = "enc"
    with patch.object(backup, "get_master_db", return_value=master), patch.object(backup, "decrypt_dsn", return_value="enc"), patch.object(backup, "_tenant_backup_dir", return_value=tmp_path), patch.object(backup, "_dsn_to_pg_env", return_value={}), patch("subprocess.run", return_value=SimpleNamespace(returncode=1, stderr="failed", stdout="")), patch.object(backup, "_prune_old_backups"):
        assert backup.run_backup_job(db, job).status == "failed"
    with patch.object(backup, "get_master_db", return_value=master), patch.object(backup, "decrypt_dsn", return_value="enc"), patch.object(backup, "_tenant_backup_dir", return_value=tmp_path), patch.object(backup, "_dsn_to_pg_env", return_value={}), patch("subprocess.run", side_effect=RuntimeError("pg_dump")), patch.object(backup.Path, "exists", return_value=True), patch.object(backup.Path, "unlink", side_effect=RuntimeError("unlink")), patch.object(backup, "_prune_old_backups"):
        assert backup.run_backup_job(db, job).status == "failed"


def test_backup_pruning_and_scheduled_tenants(tmp_path):
    old_file = tmp_path / "old.sql"; old_file.write_text("old")
    old_job = SimpleNamespace(file_path=str(old_file))
    d = MagicMock(); d.query.return_value.filter.return_value.all.return_value = [old_job]
    with patch.object(backup.settings, "backup_root", str(tmp_path)), patch.object(backup.settings, "backup_retention_days", 1):
        backup._prune_old_backups(d, "tenant")
    assert not old_file.exists() and d.delete.called
    invalid = SimpleNamespace(file_path="/outside/old.sql")
    d.query.return_value.filter.return_value.all.return_value = [invalid]
    with patch.object(backup.settings, "backup_root", str(tmp_path)):
        backup._prune_old_backups(d, "tenant")
    bad = SimpleNamespace(file_path=str(old_file), started_at=datetime.now(timezone.utc) - __import__("datetime").timedelta(days=100))
    d.query.return_value.filter.return_value.all.return_value = [bad]
    with patch.object(backup.settings, "backup_root", str(tmp_path)), patch.object(Path, "resolve", side_effect=RuntimeError("path")):
        backup._prune_old_backups(d, "tenant")
    d.query.return_value.filter.return_value.all.return_value = [SimpleNamespace(file_path=None)]
    backup._prune_old_backups(d, "tenant")

    master = MagicMock(); master.execute.return_value.fetchall.return_value = [("t1",), ("t2",)]
    recent_db = MagicMock(); recent_db.query.return_value.filter.return_value.first.return_value = object()
    with patch.object(backup, "get_master_db", return_value=master), patch("app.db.tenant_sync._get_tenant_engine", return_value=(None, lambda: recent_db)), patch.object(backup, "create_backup_job"), patch.object(backup, "run_backup_job"):
        backup._run_scheduled_backups()
    recent_db.close.assert_has_calls([])


@pytest.mark.asyncio
async def test_backup_scheduler_handles_cycle_failure_and_timeout():
    stop = __import__("asyncio").Event()
    calls = {"n": 0}
    async def wait(*args, **kwargs):
        calls["n"] += 1
        stop.set()
        raise __import__("asyncio").TimeoutError()
    with patch.object(backup, "_run_scheduled_backups", side_effect=RuntimeError("cycle")), patch.object(backup.asyncio, "to_thread", new_callable=AsyncMock), patch.object(backup.asyncio, "wait_for", side_effect=wait):
        await backup.backup_scheduler_loop(stop)
    assert calls["n"] == 1
    stop = __import__("asyncio").Event()
    async def wait_again(*args, **kwargs):
        stop.set(); raise __import__("asyncio").TimeoutError()
    with patch.object(backup.asyncio, "to_thread", new_callable=AsyncMock, side_effect=RuntimeError("cycle")), patch.object(backup.asyncio, "wait_for", side_effect=wait_again):
        await backup.backup_scheduler_loop(stop)


def test_scheduled_backup_creates_missing_recent_job():
    master = MagicMock(); master.execute.return_value.fetchall.return_value = [("t1",)]
    tenant_db = MagicMock(); tenant_db.query.return_value.filter.return_value.first.return_value = None
    job = SimpleNamespace(tenant_id="t1")
    with patch.object(backup, "get_master_db", return_value=master), patch("app.db.tenant_sync._get_tenant_engine", return_value=(None, lambda: tenant_db)), patch.object(backup, "create_backup_job", return_value=job) as create, patch.object(backup, "run_backup_job") as run:
        backup._run_scheduled_backups()
    create.assert_called_once(); run.assert_called_once_with(tenant_db, job)
    with patch.object(backup, "get_master_db", return_value=master), patch("app.db.tenant_sync._get_tenant_engine", side_effect=RuntimeError("tenant")):
        backup._run_scheduled_backups()
