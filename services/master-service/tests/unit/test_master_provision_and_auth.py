from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from jose import jwt

from app.services import provision, superadmin_auth


@pytest.mark.asyncio
async def test_superadmin_authentication_and_password_lifecycle():
    db = MagicMock(); db.query.return_value.filter.return_value.first.side_effect = [None, None]
    admin = superadmin_auth.create_superadmin(db, "root", "root@example.com", "pw", "Root Admin", mfa_secret="secret")
    assert admin.username == "root" and len(admin.plaintext_backup_codes) == 10
    db.query.return_value.filter.return_value.first.side_effect = None; db.query.return_value.filter.return_value.first.return_value = admin
    assert superadmin_auth.authenticate_superadmin(db, "root", "pw") is admin
    superadmin_auth.update_superadmin_password(db, admin, "new-pw"); assert superadmin_auth._verify_password("new-pw", admin.password_hash)
    superadmin_auth.update_superadmin_role(db, admin, "admin"); assert admin.role == "admin"
    token = superadmin_auth.create_access_token("id", "root", "super_admin")
    assert superadmin_auth.decode_superadmin_token(token)["type"] == "superadmin"
    with pytest.raises(Exception): superadmin_auth.decode_superadmin_token(jwt.encode({"type":"user"}, superadmin_auth.settings.secret_key, algorithm="HS256"))
    with pytest.raises(Exception): superadmin_auth.authenticate_superadmin(db, "root", "bad")
    admin.is_active = False
    with pytest.raises(Exception): superadmin_auth.authenticate_superadmin(db, "root", "new-pw")


def test_provision_helpers_and_database_update_paths(monkeypatch):
    assert provision._build_tenant_dsn("tenant-1") == provision.settings.tenant_db_template.format(tenant_id="tenant-1")
    engine = MagicMock(); conn = MagicMock(); conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(); result = MagicMock(); result.scalar.return_value = None; conn.execute.return_value = result; engine.connect.return_value = conn
    monkeypatch.setattr(provision, "_get_admin_engine", lambda: engine)
    dsn = provision._create_database("tenant-1"); assert dsn.endswith("tenant-1")
    result.scalar.return_value = 1; assert provision._create_database("tenant-1") == dsn
    monkeypatch.setattr(provision, "_run_alembic_migrations", MagicMock()); monkeypatch.setattr(provision, "_create_database", MagicMock(return_value="dsn")); monkeypatch.setattr(provision, "_update_tenant_record", MagicMock());
    assert provision.provision_tenant_database_sync("t", "name") == "dsn"
    monkeypatch.setattr(provision, "_create_database", MagicMock(side_effect=RuntimeError("failed"))); monkeypatch.setattr(provision, "drop_tenant_database", MagicMock())
    with pytest.raises(Exception): provision.provision_tenant_database_sync("t", "name")


def test_provision_migrations_and_drop_database(monkeypatch):
    completed = MagicMock(stdout="done"); monkeypatch.setattr(provision.subprocess, "run", MagicMock(return_value=completed)); provision._run_alembic_migrations("t", "tenant_t")
    monkeypatch.setattr(provision.subprocess, "run", MagicMock(side_effect=provision.subprocess.CalledProcessError(1, "alembic", output="bad")))
    with pytest.raises(Exception): provision._run_alembic_migrations("t", "tenant_t")
    engine = MagicMock(); conn = MagicMock(); conn.__enter__ = MagicMock(return_value=conn); conn.__exit__ = MagicMock(); conn.execute.side_effect = [RuntimeError("terminate"), RuntimeError("drop")]; engine.connect.return_value = conn
    monkeypatch.setattr(provision, "_get_admin_engine", lambda: engine); provision.drop_tenant_database("t")
