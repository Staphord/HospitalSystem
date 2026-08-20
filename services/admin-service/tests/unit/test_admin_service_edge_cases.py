from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import admin as svc
from app.services import tenant_service


def db(first=None, rows=None, count=0):
    d = MagicMock(); q = d.query.return_value
    q.filter.return_value = q; q.order_by.return_value = q; q.offset.return_value = q; q.limit.return_value = q
    q.first.return_value = first; q.all.return_value = rows or []; q.count.return_value = count
    return d


def user(**extra):
    values = dict(keycloak_sub="u", username="doc", email="d@x.com", full_name="Doc", role="doctor", hospital_id="t", is_active=True, force_password_change=False, department_id=None, phone=None, deleted_at=None, mfa_enabled=False)
    values.update(extra); return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_admin_user_lifecycle_edge_cases():
    admin = user(role="hospital_admin")
    with patch.object(svc, "count_active_hospital_admins", return_value=0):
        with pytest.raises(HTTPException): svc.ensure_not_last_admin(db(), "t", admin, deleting=True)
    d = db(first=None)
    d.query.return_value.filter.return_value.first.side_effect = [None, user(deleted_at=datetime.now(timezone.utc))]
    created = user(keycloak_sub="kc")
    with patch.object(svc, "ensure_roles", new_callable=AsyncMock), patch.object(svc, "create_keycloak_user", new_callable=AsyncMock, return_value="kc"), patch.object(svc, "set_user_password", new_callable=AsyncMock), patch.object(svc, "set_user_attribute", new_callable=AsyncMock), patch.object(svc, "create_local_user", return_value=created), patch.object(svc.audit_service, "log_change"):
        assert (await svc.create_user(d, MagicMock(), tenant_id="t", realm="r", username="doc", password="Password1!", email="d@x.com", full_name="Doc", role="doctor", actor_sub="a")).keycloak_sub == "kc"
    with patch.object(svc, "ensure_roles", new_callable=AsyncMock), patch.object(svc, "create_keycloak_user", new_callable=AsyncMock, side_effect=RuntimeError("409 Conflict")):
        with pytest.raises(HTTPException) as exc:
            await svc.create_user(db(), MagicMock(), tenant_id="t", realm="r", username="doc", password="Password1!", email="d@x.com", full_name="Doc", role="doctor", actor_sub="a")
        assert exc.value.status_code == 409
    existing = user(role="doctor")
    d = db(first=existing)
    with patch.object(svc, "update_keycloak_user", new_callable=AsyncMock), patch.object(svc, "ensure_roles", new_callable=AsyncMock), patch.object(svc, "replace_user_roles", new_callable=AsyncMock, side_effect=RuntimeError("roles")):
        with pytest.raises(HTTPException) as exc:
            await svc.update_user(d, tenant_id="t", realm="r", sub="u", actor_sub="a", role="nurse")
        assert exc.value.status_code == 502
    with patch.object(svc, "update_keycloak_user", new_callable=AsyncMock), patch.object(svc, "logout_user_sessions", new_callable=AsyncMock, side_effect=RuntimeError("logout")), patch.object(svc, "update_local_user", return_value=existing), patch.object(svc.audit_service, "log_change"):
        assert await svc.update_user(d, tenant_id="t", realm="r", sub="u", actor_sub="a", is_active=False) is existing
    with patch.object(svc, "delete_keycloak_user", new_callable=AsyncMock, return_value=None):
        with pytest.raises(HTTPException): await svc.delete_user(d, tenant_id="t", realm="r", sub="u", actor_sub="a", hard=True)
    with patch.object(svc, "update_keycloak_user", new_callable=AsyncMock), patch.object(svc, "logout_user_sessions", new_callable=AsyncMock, side_effect=RuntimeError("logout")), patch.object(svc, "update_local_user", return_value=existing), patch.object(svc.audit_service, "log_change"):
        await svc.delete_user(d, tenant_id="t", realm="r", sub="u", actor_sub="a", hard=False)
    with patch.object(svc, "update_keycloak_user", new_callable=AsyncMock), patch.object(svc, "ensure_roles", new_callable=AsyncMock), patch.object(svc, "replace_user_roles", new_callable=AsyncMock), patch.object(svc, "update_local_user", return_value=existing), patch.object(svc.audit_service, "log_change"):
        await svc.update_user(d, tenant_id="t", realm="r", sub="u", actor_sub="a", role="doctor", is_active=True)


@pytest.mark.asyncio
async def test_admin_assignable_permissions_and_catalog_error_paths():
    master = db(rows=[SimpleNamespace(name="custom")]); master.execute.side_effect = RuntimeError("global db")
    with patch.object(svc, "get_realm_roles", new_callable=AsyncMock, side_effect=RuntimeError("kc")):
        assert "custom" in await svc.list_assignable_roles(master, "t", "r")
    permission_db = db(first=SimpleNamespace(role_name="doctor", modules=[], actions=[]))
    with patch.object(svc.audit_service, "log_change"):
        svc.seed_default_permissions(permission_db)
        svc.list_permissions(permission_db)
    for fn, args in [(svc.update_department, (uuid4(), {})), (svc.delete_department, (uuid4(),)), (svc.update_fee, (uuid4(), {})), (svc.delete_fee, (uuid4(),)), (svc.update_insurance_provider, (uuid4(), {})), (svc.delete_insurance_provider, (uuid4(),)), (svc.update_bed, (uuid4(), {})), (svc.delete_bed, (uuid4(),))]:
        with pytest.raises(HTTPException): fn(db(), *args, "actor")
    assert svc.list_wards(db(rows=[SimpleNamespace(ward_name="ICU", is_available=True, is_active=True), SimpleNamespace(ward_name="ICU", is_available=False, is_active=True)]))[0]["bed_count"] == 2
    catalog_db = MagicMock()
    catalog_query = catalog_db.query.return_value
    catalog_query.filter.return_value = catalog_query
    catalog_query.order_by.return_value = catalog_query
    catalog_query.all.return_value = []
    assert svc.list_fee_schedules(catalog_db, item_type="visit", q="consult", active_only=True) == []
    catalog_db.query.return_value.filter.return_value.first.return_value = object()
    with pytest.raises(HTTPException): svc.create_bed(catalog_db, {"ward_name": "ICU", "bed_number": "1"}, "actor")
    counts = [5, 2]
    q1 = MagicMock(); q1.filter.return_value = q1; q1.scalar.side_effect = counts
    catalog_db.query.side_effect = [q1, q1]
    assert svc.beds_summary(catalog_db) == {"total": 5, "available": 2, "occupied": 3}
    with patch.object(svc, "create_realm_role", new_callable=AsyncMock, side_effect=Exception("already exists")), patch.object(svc, "ensure_roles", new_callable=AsyncMock):
        await svc.sync_tenant_role_to_keycloak("r", "custom")
    with patch.object(svc, "create_realm_role", new_callable=AsyncMock, side_effect=Exception("unexpected")), patch.object(svc, "ensure_roles", new_callable=AsyncMock):
        await svc.sync_tenant_role_to_keycloak("r", "custom")
    with patch.object(svc, "create_realm_role", new_callable=AsyncMock, side_effect=Exception("409 Conflict")), patch.object(svc, "ensure_roles", new_callable=AsyncMock):
        await svc.sync_tenant_role_to_keycloak("r", "custom")
    service_db = db(first=SimpleNamespace(hospital_name="H", timezone="UTC", currency="TZS", date_format=None, logo_url=None, primary_contact_name=None, primary_contact_email=None, primary_contact_phone=None, address=None, city=None, country=None))
    with patch.object(svc.audit_service, "log_change"):
        svc.update_hospital_profile(service_db, db(), "t", "a", {"ignored": "x", "hospital_name": None})
        svc.update_department(db(first=SimpleNamespace(department_name="x", department_type="x", head_user_sub=None, is_active=True)), uuid4(), {"department_name": None, "ignored": "x"}, "a")
        fee_db = MagicMock(); fee_q = fee_db.query.return_value; fee_q.order_by.return_value = fee_q; fee_q.all.return_value = []
        svc.list_fee_schedules(fee_db)


@pytest.mark.asyncio
async def test_tenant_subscription_and_dsn_fallback_paths():
    d = MagicMock(); d.execute.return_value.one_or_none.side_effect = [None, ("active", None, 1), ("active", datetime.now(timezone.utc) - timedelta(days=31), 1)]
    assert await tenant_service.check_and_update_tenant_status(d, "missing") == "not_found"
    assert await tenant_service.check_and_update_tenant_status(d, "active") == "active"
    with patch.object(tenant_service, "cache_tenant_suspension", new_callable=AsyncMock), patch.object(tenant_service, "_revoke_keycloak_sessions", new_callable=AsyncMock):
        assert await tenant_service.check_and_update_tenant_status(d, "expired") == "suspended"
    encrypted = tenant_service.encrypt_dsn("postgresql://fallback")
    d.execute.return_value.scalar.side_effect = [encrypted]
    assert await tenant_service.get_tenant_db_dsn(d, "default") == "postgresql://fallback"
    d.execute.return_value.scalar.side_effect = [None, None]
    with patch.object(tenant_service.settings, "database_url", "postgresql://master"):
        assert await tenant_service.get_tenant_db_dsn(d, "tenant") == "postgresql://master"
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def post(self, *a, **k): return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"access_token": "a"})
        async def get(self, *a, **k): return SimpleNamespace(is_success=True, json=lambda: [{"id": "u"}])
        async def put(self, *a, **k): return SimpleNamespace()
    with patch.object(tenant_service.settings, "keycloak_url", "http://keycloak"), patch("httpx.AsyncClient", return_value=Client()):
        await tenant_service._revoke_keycloak_sessions("t")
