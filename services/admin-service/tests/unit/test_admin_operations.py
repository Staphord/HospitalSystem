from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core import database
from app.services import backup, sessions
from app.db import tenant_sync


def _query_db(first=None, all_rows=None, count=0):
    db = MagicMock()
    q = MagicMock()
    db.query.return_value = q
    q.filter.return_value = q
    q.order_by.return_value = q
    q.offset.return_value = q
    q.limit.return_value = q
    q.first.return_value = first
    q.all.return_value = all_rows or []
    q.count.return_value = count
    return db, q


def test_backup_listing_status_and_download_validation(tmp_path):
    job = SimpleNamespace(
        backup_id=uuid4(), tenant_id="tenant", status="completed",
        finished_at=datetime.now(timezone.utc), file_path=None,
    )
    db, q = _query_db(first=job, all_rows=[job])
    assert backup.list_backups(db, "tenant", limit=500) == [job]
    assert backup.get_backup(db, "tenant", job.backup_id) is job
    status = backup.backup_status(db, "tenant")
    assert status["last_backup_id"] == str(job.backup_id)

    with pytest.raises(HTTPException):
        backup.get_backup(_query_db(first=None)[0], "tenant", uuid4())
    with pytest.raises(HTTPException):
        backup.resolve_download_path(SimpleNamespace(status="pending", file_path=None, tenant_id="tenant"))

    file_path = tmp_path / "tenant" / "backup.sql"
    file_path.parent.mkdir()
    file_path.write_text("select 1;")
    with patch.object(backup.settings, "backup_root", str(tmp_path)):
        valid = SimpleNamespace(status="completed", file_path=str(file_path), tenant_id="tenant")
        assert backup.resolve_download_path(valid) == file_path
        outside = SimpleNamespace(status="completed", file_path=str(tmp_path / "backup.sql"), tenant_id="tenant")
        with pytest.raises(HTTPException):
            backup.resolve_download_path(outside)


def test_create_backup_job_and_prune_old_files(tmp_path):
    db = MagicMock()
    job = backup.create_backup_job(db, tenant_id="tenant", triggered_by="manual", triggered_by_sub="u1")
    assert job.status == "pending"
    assert db.commit.called

    old_file = tmp_path / "tenant" / "old.sql"
    old_file.parent.mkdir()
    old_file.write_text("old")
    old = SimpleNamespace(
        file_path=str(old_file),
        started_at=datetime.now(timezone.utc) - timedelta(days=100),
    )
    q = db.query.return_value
    q.filter.return_value = q
    q.all.return_value = [old]
    with patch.object(backup.settings, "backup_root", str(tmp_path)), \
         patch.object(backup.settings, "backup_retention_days", 30):
        backup._prune_old_backups(db, "tenant")
    assert not old_file.exists()
    db.delete.assert_called_once_with(old)


def test_session_device_columns_and_listing():
    assert sessions._device_from_ua("Mozilla iPad") == "iPad"
    assert sessions._device_from_ua("Unknown") == "Web Browser"
    assert sessions.list_active_sessions(MagicMock(), _query_db(all_rows=[])[0], "tenant") == []
    db = MagicMock()
    db.get_bind.side_effect = RuntimeError("not inspectable")
    assert sessions._refresh_token_columns(db) == set()

    user = SimpleNamespace(keycloak_sub="u1", hospital_id="tenant", deleted_at=None, full_name="Jane Doe", username="jane", email=None, role="doctor")
    tenant_db, tq = _query_db(all_rows=[user])
    master_db = MagicMock()
    master_db.get_bind.side_effect = RuntimeError("legacy schema")
    master_db.execute.return_value = [SimpleNamespace(session_id="s1", keycloak_sub="u1", created_at=datetime.now(timezone.utc))]
    result = sessions.list_active_sessions(master_db, tenant_db, "tenant")
    assert result[0]["device"] == "Web Browser"
    assert result[0]["staff_name"] == "Jane Doe"
    user.hospital_id = "other"
    tenant_db, tq = _query_db(all_rows=[user])
    master_db = MagicMock()
    master_db.get_bind.return_value = object()
    token = SimpleNamespace(session_id="s2", keycloak_sub="u1", created_at=datetime.now(timezone.utc), ip_address="1.2.3.4", user_agent="Mozilla Macintosh")
    master_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [token]
    with patch.object(sessions, "_refresh_token_columns", return_value={"ip_address", "user_agent"}):
        result = sessions.list_active_sessions(master_db, tenant_db, "tenant")
    assert result[0]["device"] == "Mac"
    empty_db, _ = _query_db(all_rows=[])
    assert sessions.list_active_sessions(master_db, empty_db, "tenant") == []
    user2 = SimpleNamespace(keycloak_sub=None, hospital_id="tenant", deleted_at=None, full_name=None, username=None, email=None, role=None)
    no_sub_db, _ = _query_db(all_rows=[user2])
    assert sessions.list_active_sessions(master_db, no_sub_db, "tenant") == []
    token2 = SimpleNamespace(session_id="orphan", keycloak_sub="orphan", created_at=datetime.now(timezone.utc), ip_address=None, user_agent=None)
    master_db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [token2]
    with patch.object(sessions, "_refresh_token_columns", return_value={"ip_address", "user_agent"}):
        assert sessions.list_active_sessions(master_db, tenant_db, "tenant") == []


@pytest.mark.asyncio
async def test_revoke_session_success_and_missing_paths():
    token = SimpleNamespace(keycloak_sub="u1", is_revoked=False)
    master_db, _ = _query_db(first=token)
    tenant_db, _ = _query_db(all_rows=[SimpleNamespace(keycloak_sub="u1", deleted_at=None)])
    with patch.object(sessions, "logout_user_sessions", new_callable=AsyncMock), \
         patch.object(sessions.audit_service, "log_change"):
        await sessions.revoke_session(master_db, tenant_db, session_id="s1", tenant_id="tenant", actor_sub="actor", realm="realm")
    assert token.is_revoked is True

    with pytest.raises(HTTPException):
        await sessions.revoke_session(_query_db(first=None)[0], tenant_db, session_id="missing", tenant_id="tenant", actor_sub="actor", realm="realm")
    with pytest.raises(HTTPException):
        await sessions.revoke_session(master_db, _query_db(all_rows=[])[0], session_id="s1", tenant_id="tenant", actor_sub="actor", realm="realm")
    with patch.object(sessions, "logout_user_sessions", new_callable=AsyncMock, side_effect=RuntimeError("keycloak")), patch.object(sessions.audit_service, "log_change"):
        await sessions.revoke_session(master_db, tenant_db, session_id="s1", tenant_id="tenant", actor_sub="actor", realm="realm")


def test_database_context_helpers_without_connecting():
    db = MagicMock()
    context = database.HospitalContext("tenant", db)
    database.close_hospital_context(context)
    db.close.assert_called_once()

    with patch.object(database._router, "get_session", return_value=db):
        ctx = database.get_hospital_context("tenant")
    assert ctx.db is db

    with patch.object(database, "get_session_local", return_value=lambda: db):
        generator = database.get_db()
        assert next(generator) is db
        with pytest.raises(StopIteration):
            next(generator)
    db.close.assert_called()


def test_tenant_engine_missing_row_and_session_generator_cleanup():
    tenant_sync._tenant_engine_cache.clear()
    master = MagicMock(); master.execute.return_value.scalar.return_value = None
    with patch.object(tenant_sync, "get_master_db", return_value=master):
        with pytest.raises(ValueError):
            tenant_sync._get_tenant_engine("missing")
    session = MagicMock()
    with patch.object(tenant_sync, "_get_tenant_engine", return_value=(None, lambda: session)):
        generator = tenant_sync.get_tenant_db_sync("tenant")
        assert next(generator) is session
        generator.close()
    session.close.assert_called_once()
