from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.v1.admin import router as r
from app.api.v1.admin.schemas import (
    BedCreate, BedUpdate, DepartmentCreate, DepartmentUpdate,
    FeeScheduleCreate, FeeScheduleUpdate, HospitalProfileUpdate,
    HospitalSettingsUpdate, InsuranceProviderCreate, InsuranceProviderUpdate,
    PermissionUpdate, RoleCreate, RoleUpdate,
)


NOW = datetime.now(timezone.utc)
RID = uuid4()


def ctx():
    return SimpleNamespace(tenant_id="t1", user_sub="actor", is_super_admin=False)


def request():
    return Request({"type": "http", "method": "GET", "path": "/admin", "headers": [],
                    "client": ("127.0.0.1", 1234), "scheme": "https", "server": ("example.test", 443)})


def row(**values):
    return SimpleNamespace(**values)


def out_user(**extra):
    values = dict(keycloak_sub="u1", username="user", full_name="User", email="u@example.test", role="doctor",
                  hospital_id="t1", is_active=True, force_password_change=False, department_id=None,
                  phone=None, last_login_at=None, password_expires_at=None, mfa_enabled=False, deleted_at=None)
    values.update(extra)
    return row(**values)


def out_department(**extra):
    values = dict(department_id=RID, department_name="ICU", department_type="clinical", head_user_sub=None,
                  is_active=True, created_at=NOW, updated_at=NOW)
    values.update(extra); return row(**values)


def out_fee(**extra):
    values = dict(fee_id=RID, item_name="Consult", item_code="C1", item_type="visit", standard_price=10,
                  insurance_price=None, is_active=True, effective_from=date.today(), effective_to=None,
                  created_at=NOW, updated_at=NOW)
    values.update(extra); return row(**values)


def out_provider(**extra):
    values = dict(provider_id=RID, name="NHIF", contact_person=None, policies=[], contact_email=None,
                  contact_phone=None, notes=None, is_active=True, created_at=NOW, updated_at=NOW)
    values.update(extra); return row(**values)


def out_bed(**extra):
    values = dict(bed_id=RID, ward_name="ICU", bed_number="1", bed_type="general", is_available=True,
                  is_active=True, notes=None, created_at=NOW, updated_at=NOW)
    values.update(extra); return row(**values)


@pytest.mark.asyncio
async def test_router_user_and_role_handlers():
    db = MagicMock(); db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(keycloak_realm="realm1", hospital_name="Demo Hospital")
    response = SimpleNamespace(headers={})
    with patch.object(r.admin_svc, "list_users", return_value=([out_user()], 1)), patch.object(r, "_user_out", return_value="user"):
        assert await r.list_hospital_users(request(), response, db=db, ctx=ctx()) == ["user"]
    assert response.headers["X-Total-Count"] == "1"
    with patch.object(r.admin_svc, "get_user", return_value=out_user()), patch.object(r, "_user_out", return_value="user"):
        assert await r.get_hospital_user(request(), "u1", db=db, ctx=ctx()) == "user"
    with patch.object(r.admin_svc, "list_assignable_roles", new_callable=AsyncMock, return_value=["doctor"]):
        assert await r.assignable_roles(request(), master_db=db, ctx=ctx()) == ["doctor"]
    body = r.HospitalUserCreate(username="doctor", password="SecurePass1!", email="d@example.com", role="doctor")
    created = out_user(username="doctor")
    with patch.object(r.admin_svc, "create_user", new_callable=AsyncMock, return_value=created), patch.object(r, "_user_out", return_value="created"), patch.object(r, "publish_user_created", new_callable=AsyncMock):
        assert await r.create_hospital_user(request(), body, MagicMock(), db=db, master_db=db, ctx=ctx()) == "created"
    update = r.HospitalUserUpdate(is_active=False, reason="left")
    with patch.object(r.admin_svc, "get_user", return_value=out_user()), patch.object(r.admin_svc, "update_user", new_callable=AsyncMock, return_value=created), patch.object(r, "_user_out", return_value="updated"), patch.object(r, "publish_user_deactivated", new_callable=AsyncMock):
        assert await r.update_hospital_user(request(), "u1", update, db=db, master_db=db, ctx=ctx()) == "updated"
    with patch.object(r.admin_svc, "delete_user", new_callable=AsyncMock), patch.object(r, "publish_user_deactivated", new_callable=AsyncMock):
        assert await r.delete_hospital_user(request(), "u1", db=db, master_db=db, ctx=ctx()) is None
    with patch.object(r, "get_realm_roles", new_callable=AsyncMock, return_value=[{"id": "1", "name": "doctor"}]):
        assert (await r.list_roles(request(), master_db=db, ctx=ctx()))[0].name == "doctor"
    with patch.object(r, "create_realm_role", new_callable=AsyncMock, return_value={"id": "1", "name": "custom"}):
        assert (await r.create_role(request(), RoleCreate(name="custom"), master_db=db, ctx=ctx())).name == "custom"
    with patch.object(r.admin_svc, "guarded_update_realm_role", new_callable=AsyncMock):
        assert (await r.update_role(request(), "old", RoleUpdate(name="new"), master_db=db, ctx=ctx()))["detail"]
    with patch.object(r.admin_svc, "guarded_delete_realm_role", new_callable=AsyncMock):
        assert await r.delete_role(request(), "custom", master_db=db, ctx=ctx()) is None
    assert r._user_out(out_user()).username == "user"


@pytest.mark.asyncio
async def test_router_catalog_and_profile_handlers():
    db = MagicMock(); c = ctx(); req = request()
    tenant = row(tenant_id="t1", hospital_name="Hospital", country="TZ", city=None, address=None,
                 primary_contact_name=None, primary_contact_email=None, primary_contact_phone=None,
                 billing_email=None, timezone="UTC", currency="TZS", date_format=None, logo_url=None,
                 status="active", subscription_plan="basic")
    with patch.object(r.admin_svc, "get_hospital_profile", return_value=tenant):
        assert (await r.get_profile(req, master_db=db, ctx=c)).hospital_name == "Hospital"
    with patch.object(r.admin_svc, "update_hospital_profile", return_value=tenant):
        assert (await r.patch_profile(req, HospitalProfileUpdate(hospital_name="New"), master_db=db, db=db, ctx=c)).tenant_id == "t1"
    with patch.object(r.admin_svc, "list_departments", return_value=[out_department()]):
        assert (await r.get_departments(req, db=db, ctx=c))[0].department_name == "ICU"
    with patch.object(r.admin_svc, "create_department", return_value=out_department()), patch.object(r.admin_svc, "update_department", return_value=out_department()), patch.object(r.admin_svc, "delete_department"):
        assert (await r.post_department(req, DepartmentCreate(department_name="ICU", department_type="clinical"), db=db, ctx=c)).department_name == "ICU"
        assert (await r.patch_department(req, RID, DepartmentUpdate(is_active=False), db=db, ctx=c)).department_id == RID
        assert await r.remove_department(req, RID, db=db, ctx=c) is None
    with patch.object(r.admin_svc, "list_fee_schedules", return_value=[out_fee()]), patch.object(r.admin_svc, "create_fee", return_value=out_fee()), patch.object(r.admin_svc, "update_fee", return_value=out_fee()), patch.object(r.admin_svc, "delete_fee"):
        assert (await r.get_fees(req, db=db, ctx=c))[0].fee_id == RID
        fee = FeeScheduleCreate(item_name="Consult", item_code="C1", item_type="visit", standard_price=10, effective_from=date.today())
        assert (await r.post_fee(req, fee, db=db, ctx=c)).item_code == "C1"
        assert (await r.patch_fee(req, RID, FeeScheduleUpdate(is_active=False), db=db, ctx=c)).fee_id == RID
        assert await r.remove_fee(req, RID, db=db, ctx=c) is None
    with patch.object(r.admin_svc, "list_insurance_providers", return_value=[out_provider()]), patch.object(r.admin_svc, "create_insurance_provider", return_value=out_provider()), patch.object(r.admin_svc, "update_insurance_provider", return_value=out_provider()), patch.object(r.admin_svc, "delete_insurance_provider"):
        assert (await r.get_providers(req, db=db, ctx=c))[0].name == "NHIF"
        assert (await r.post_provider(req, InsuranceProviderCreate(name="NHIF"), db=db, ctx=c)).name == "NHIF"
        assert (await r.patch_provider(req, RID, InsuranceProviderUpdate(is_active=False), db=db, ctx=c)).provider_id == RID
        assert await r.remove_provider(req, RID, db=db, ctx=c) is None
    with patch.object(r.admin_svc, "list_wards", return_value=[{"ward_name": "ICU", "bed_count": 2, "available": 1}]), patch.object(r.admin_svc, "beds_summary", return_value={"total": 2}), patch.object(r.admin_svc, "list_beds", return_value=[out_bed()]), patch.object(r.admin_svc, "create_bed", return_value=out_bed()), patch.object(r.admin_svc, "update_bed", return_value=out_bed()), patch.object(r.admin_svc, "delete_bed"):
        assert (await r.get_wards(req, db=db, ctx=c))[0].ward_name == "ICU"
        assert (await r.get_beds_summary(req, db=db, ctx=c))["total"] == 2
        assert (await r.get_beds(req, db=db, ctx=c))[0].bed_number == "1"
        assert (await r.post_bed(req, BedCreate(ward_name="ICU", bed_number="1"), db=db, ctx=c)).bed_id == RID
        assert (await r.patch_bed(req, RID, BedUpdate(is_available=False), db=db, ctx=c)).bed_id == RID
        assert await r.remove_bed(req, RID, db=db, ctx=c) is None


@pytest.mark.asyncio
async def test_router_reports_audit_settings_sessions_and_login_history():
    db = MagicMock(); c = ctx(); req = request()
    report_funcs = [("patient_census", r.report_census), ("wait_times", r.report_wait_times), ("discharges", r.report_discharges)]
    for name, handler in report_funcs:
        with patch.object(r.reports_svc, name, return_value={"ok": True}) as fn:
            assert await handler(req, db=db, ctx=c) == {"ok": True}; fn.assert_called_once()
    for name, handler in [("bed_occupancy", r.report_bed_occupancy), ("dashboard", r.report_dashboard)]:
        with patch.object(r.reports_svc, name, return_value={"ok": True}):
            assert await handler(req, db=db, ctx=c) == {"ok": True}
    for name, handler in [("revenue_summary", r.report_revenue), ("operational_activity", r.report_operational)]:
        with patch.object(r.reports_svc, name, return_value={"ok": True}):
            assert await handler(req, db=db, ctx=c) == {"ok": True}
    audit = row(log_id=RID, user_id="u", action="update", table_name="x", record_id=None, old_values=None, new_values=None, ip_address=None, session_id=None, created_at=NOW)
    with patch.object(r.audit_service, "list_audit_logs", return_value=([audit], 1)):
        assert (await r.list_audit_logs(req, limit=50, offset=0, db=db, ctx=c)).total == 1
        assert "audit_logs.csv" in (await r.export_audit_logs(req, db=db, ctx=c)).headers["content-disposition"]
        assert "application/json" in (await r.export_audit_logs(req, format="json", db=db, ctx=c)).media_type
    with patch.object(r.audit_service, "get_audit_log", return_value=audit):
        assert (await r.get_audit_log(req, RID, db=db, ctx=c)).log_id == RID
    permission = row(role_name="doctor", modules=["x"], actions=["read"], updated_at=NOW)
    with patch.object(r.admin_svc, "list_permissions", return_value=[permission]), patch.object(r.admin_svc, "upsert_permission", return_value=permission):
        assert (await r.get_permissions(req, db=db, ctx=c))[0].role_name == "doctor"
        assert (await r.put_permission(req, "doctor", PermissionUpdate(), db=db, ctx=c)).role_name == "doctor"
    setting = row(key="x", value="y", updated_by="a", updated_at=NOW)
    with patch.object(r.settings_service, "list_settings", return_value=[setting]), patch.object(r.settings_service, "upsert_settings", return_value=[setting]):
        assert (await r.get_settings(req, db=db, ctx=c))[0].key == "x"
        assert (await r.put_settings(req, HospitalSettingsUpdate(settings={"x": "y"}), db=db, ctx=c))[0].key == "x"
    session = dict(id="s", staff_id="u", staff_name="User", staff_role="doctor", department=None, login_time=NOW, device="Web", ip_address="127.0.0.1", avatar_url="")
    with patch.object(r.sessions_svc, "list_active_sessions", return_value=[session]), patch.object(r.sessions_svc, "revoke_session", new_callable=AsyncMock):
        assert (await r.list_sessions(req, master_db=db, db=db, ctx=c))[0].id == "s"
        assert await r.revoke_session(req, "s", master_db=db, db=db, ctx=c) is None
    history = dict(timestamp=NOW, ip=None, device="Web Browser", duration="—", workspace="Hospital Portal", status="Success", detail=None)
    with patch.object(r.login_history_svc, "list_login_history", return_value=[history]):
        assert (await r.get_user_login_history(req, "u", master_db=db, ctx=c))[0].status == "Success"


@pytest.mark.asyncio
async def test_router_tenant_required_guards():
    no_tenant = SimpleNamespace(tenant_id=None, user_sub="actor")
    db = MagicMock(); req = request()
    for call in [
        lambda: r.assignable_roles(req, master_db=db, ctx=no_tenant),
        lambda: r.list_hospital_users(req, SimpleNamespace(headers={}), db=db, ctx=no_tenant),
        lambda: r.get_hospital_user(req, "u", db=db, ctx=no_tenant),
        lambda: r.get_profile(req, master_db=db, ctx=no_tenant),
        lambda: r.create_backup(req, db=db, ctx=no_tenant),
        lambda: r.list_backup_jobs(req, db=db, ctx=no_tenant),
        lambda: r.get_backup_status(req, db=db, ctx=no_tenant),
        lambda: r.download_backup(req, RID, db=db, ctx=no_tenant),
        lambda: r.list_sessions(req, master_db=db, db=db, ctx=no_tenant),
        lambda: r.revoke_session(req, "s", master_db=db, db=db, ctx=no_tenant),
    ]:
        with pytest.raises(HTTPException) as exc:
            result = call()
            if hasattr(result, "__await__"):
                await result
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_all_remaining_router_tenant_guards():
    no_tenant = SimpleNamespace(tenant_id=None, user_sub="actor")
    req = request(); db = MagicMock(); body = SimpleNamespace(model_dump=lambda **kw: {})
    calls = [
        lambda: r.create_hospital_user(req, body, MagicMock(), db=db, master_db=db, ctx=no_tenant),
        lambda: r.update_hospital_user(req, "u", body, db=db, master_db=db, ctx=no_tenant),
        lambda: r.delete_hospital_user(req, "u", db=db, master_db=db, ctx=no_tenant),
        lambda: r.list_roles(req, master_db=db, ctx=no_tenant), lambda: r.create_role(req, body, master_db=db, ctx=no_tenant),
        lambda: r.update_role(req, "x", body, master_db=db, ctx=no_tenant), lambda: r.delete_role(req, "x", master_db=db, ctx=no_tenant),
        lambda: r.list_global_roles(req, master_db=db, ctx=no_tenant), lambda: r.create_tenant_role(req, body, master_db=db, ctx=no_tenant),
        lambda: r.list_tenant_roles(req, master_db=db, ctx=no_tenant), lambda: r.update_tenant_role(req, RID, body, master_db=db, ctx=no_tenant),
        lambda: r.delete_tenant_role(req, RID, master_db=db, ctx=no_tenant), lambda: r.get_permissions(req, db=db, ctx=no_tenant),
        lambda: r.put_permission(req, "x", body, db=db, ctx=no_tenant), lambda: r.list_audit_logs(req, limit=50, offset=0, db=db, ctx=no_tenant),
        lambda: r.export_audit_logs(req, db=db, ctx=no_tenant), lambda: r.get_audit_log(req, RID, db=db, ctx=no_tenant),
        lambda: r.patch_profile(req, body, master_db=db, db=db, ctx=no_tenant), lambda: r.get_permissions(req, db=db, ctx=no_tenant),
    ]
    for call in calls:
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_router_error_branches_and_tenant_role_handlers(tmp_path):
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(keycloak_realm=None, hospital_name="Demo")
    c = ctx(); req = request()
    with patch.object(r, "get_realm_roles", new_callable=AsyncMock, side_effect=RuntimeError("kc down")):
        with pytest.raises(HTTPException) as exc:
            await r.list_roles(req, master_db=db, ctx=c)
        assert exc.value.status_code == 502
    with patch.object(r, "create_realm_role", new_callable=AsyncMock, side_effect=RuntimeError("kc down")):
        with pytest.raises(HTTPException) as exc:
            await r.create_role(req, RoleCreate(name="custom"), master_db=db, ctx=c)
        assert exc.value.status_code == 502
    with patch.object(r.admin_svc, "guarded_update_realm_role", new_callable=AsyncMock, side_effect=RuntimeError("kc down")):
        with pytest.raises(HTTPException) as exc:
            await r.update_role(req, "old", RoleUpdate(name="new"), master_db=db, ctx=c)
        assert exc.value.status_code == 502
    with patch.object(r.admin_svc, "guarded_delete_realm_role", new_callable=AsyncMock, side_effect=RuntimeError("kc down")):
        with pytest.raises(HTTPException) as exc:
            await r.delete_role(req, "custom", master_db=db, ctx=c)
        assert exc.value.status_code == 502

    # Exercise the user notification fallback and the inactive/no-email paths.
    body = r.HospitalUserCreate(username="doctor", password="SecurePass1!", email="d@example.com", role="doctor")
    user = out_user(email="d@example.com")
    with patch.object(r.admin_svc, "create_user", new_callable=AsyncMock, return_value=user), patch.object(r, "publish_user_created", new_callable=AsyncMock, side_effect=RuntimeError("broker")), patch.object(r, "_user_out", return_value="ok"):
        assert await r.create_hospital_user(req, body, MagicMock(), db=db, master_db=db, ctx=c) == "ok"
    user_no_email = out_user(email=None)
    with patch.object(r.admin_svc, "create_user", new_callable=AsyncMock, return_value=user_no_email), patch.object(r, "publish_user_created", new_callable=AsyncMock), patch.object(r, "_user_out", return_value="ok"):
        assert await r.create_hospital_user(req, body, MagicMock(), db=db, master_db=db, ctx=c) == "ok"
    with patch.object(r.admin_svc, "get_user", side_effect=HTTPException(404, "missing")), patch.object(r.admin_svc, "update_user", new_callable=AsyncMock, return_value=user_no_email), patch.object(r, "_user_out", return_value="ok"):
        assert await r.update_hospital_user(req, "u1", r.HospitalUserUpdate(), db=db, master_db=db, ctx=c) == "ok"
    with patch.object(r.admin_svc, "get_user", return_value=out_user(is_active=True)), patch.object(r.admin_svc, "update_user", new_callable=AsyncMock, return_value=user), patch.object(r, "publish_user_deactivated", new_callable=AsyncMock, side_effect=RuntimeError("broker")), patch.object(r, "_user_out", return_value="ok"):
        assert await r.update_hospital_user(req, "u1", r.HospitalUserUpdate(is_active=False), db=db, master_db=db, ctx=c) == "ok"
    with patch.object(r.admin_svc, "delete_user", new_callable=AsyncMock), patch.object(r, "publish_user_deactivated", new_callable=AsyncMock, side_effect=RuntimeError("broker")):
        assert await r.delete_hospital_user(req, "u1", db=db, master_db=db, ctx=c) is None

    # Tenant-role create/list/update/delete, including the Keycloak fallback.
    fake_role = row(tenant_role_id=RID, tenant_id="t1", name="custom", description="d", scope={}, created_by="actor", created_at=NOW, updated_at=NOW)
    db.execute.return_value.scalar.return_value = None
    db.query.return_value.filter.return_value.first.return_value = None
    with patch.object(r, "TenantRoleModel", return_value=fake_role), patch.object(r.admin_svc, "sync_tenant_role_to_keycloak", new_callable=AsyncMock):
        assert (await r.create_tenant_role(req, r.TenantRoleCreate(name="custom"), master_db=db, ctx=c)).name == "custom"
    db.query.return_value.filter.return_value.order_by.return_value.all.return_value = [fake_role]
    assert (await r.list_tenant_roles(req, master_db=db, ctx=c))[0].name == "custom"
    db.query.return_value.filter.return_value.first.side_effect = [fake_role, None]
    with patch.object(r, "_get_tenant_realm", return_value="realm"), patch.object(r.admin_svc, "guarded_update_realm_role", new_callable=AsyncMock, side_effect=HTTPException(502, "fallback")), patch.object(r.admin_svc, "sync_tenant_role_to_keycloak", new_callable=AsyncMock):
        assert (await r.update_tenant_role(req, RID, r.TenantRoleUpdate(name="new"), master_db=db, ctx=c)).name == "new"
    db.query.return_value.filter.return_value.first.side_effect = None
    db.query.return_value.filter.return_value.first.return_value = fake_role
    with patch.object(r, "_get_tenant_realm", return_value="realm"):
        assert (await r.update_tenant_role(req, RID, r.TenantRoleUpdate(description="new description", scope={"read": True}), master_db=db, ctx=c)).description == "new description"
    db.query.return_value.filter.return_value.first.side_effect = None
    db.query.return_value.filter.return_value.first.return_value = fake_role
    with patch.object(r, "_get_tenant_realm", return_value="realm"), patch.object(r.admin_svc, "guarded_delete_realm_role", new_callable=AsyncMock, side_effect=RuntimeError("ignore")):
        assert await r.delete_tenant_role(req, RID, master_db=db, ctx=c) is None

    # Backup success paths.
    backup = dict(backup_id=RID, tenant_id="t1", status="completed", file_path="/tmp/x.sql", size_bytes=1,
                  triggered_by="user", triggered_by_sub="actor", error=None, started_at=NOW, finished_at=NOW)
    with patch.object(r.backup_svc, "create_backup_job", return_value=backup), patch.object(r.backup_svc, "run_backup_job", return_value=backup):
        assert (await r.create_backup(req, db=db, ctx=c)).backup_id == RID
    with patch.object(r.backup_svc, "list_backups", return_value=[backup]), patch.object(r.backup_svc, "backup_status", return_value={"ok": True}):
        assert (await r.list_backup_jobs(req, db=db, ctx=c))[0].status == "completed"
        assert await r.get_backup_status(req, db=db, ctx=c) == {"ok": True}
    path = tmp_path / "x.sql"; path.write_text("select 1;")
    with patch.object(r.backup_svc, "get_backup", return_value=backup), patch.object(r.backup_svc, "resolve_download_path", return_value=path):
        assert (await r.download_backup(req, RID, db=db, ctx=c)).path == path


@pytest.mark.asyncio
async def test_router_global_and_tenant_role_conflicts_and_audit_missing():
    req = request(); c = ctx(); db = MagicMock()
    db.execute.return_value.fetchall.return_value = [(RID, "global", "desc", {}, NOW, NOW)]
    assert (await r.list_global_roles(req, master_db=db, ctx=c))[0].name == "global"
    db.execute.return_value.scalar.return_value = True
    with pytest.raises(HTTPException) as exc:
        await r.create_tenant_role(req, r.TenantRoleCreate(name="global"), master_db=db, ctx=c)
    assert exc.value.status_code == 409
    db.execute.return_value.scalar.return_value = None
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(name="custom")
    with pytest.raises(HTTPException) as exc:
        await r.create_tenant_role(req, r.TenantRoleCreate(name="custom"), master_db=db, ctx=c)
    assert exc.value.status_code == 409
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException):
        await r.update_tenant_role(req, RID, r.TenantRoleUpdate(), master_db=db, ctx=c)
    db.query.return_value.filter.return_value.first.return_value = SimpleNamespace(name="custom", tenant_role_id=RID, tenant_id="t1", description=None, scope=None, created_by="actor", created_at=NOW, updated_at=NOW)
    with pytest.raises(HTTPException):
        await r.update_tenant_role(req, RID, r.TenantRoleUpdate(name="hospital_admin"), master_db=db, ctx=c)
    db.query.return_value.filter.return_value.first.return_value = None
    with pytest.raises(HTTPException):
        await r.delete_tenant_role(req, RID, master_db=db, ctx=SimpleNamespace(tenant_id="t1", user_sub="actor"))
    with patch.object(r.audit_service, "get_audit_log", return_value=None):
        with pytest.raises(HTTPException):
            await r.get_audit_log(req, RID, db=db, ctx=c)
    with patch.object(r.admin_svc, "guarded_update_realm_role", new_callable=AsyncMock, side_effect=HTTPException(400, "guard")):
        with pytest.raises(HTTPException):
            await r.update_role(req, "old", r.RoleUpdate(name="new"), master_db=db, ctx=c)
    with patch.object(r.admin_svc, "guarded_delete_realm_role", new_callable=AsyncMock, side_effect=HTTPException(400, "guard")):
        with pytest.raises(HTTPException):
            await r.delete_role(req, "old", master_db=db, ctx=c)
    role = SimpleNamespace(name="custom", tenant_role_id=RID, tenant_id="t1", description=None, scope=None, created_by="actor", created_at=NOW, updated_at=NOW)
    db.query.return_value.filter.return_value.first.return_value = role
    with patch.object(r, "_get_tenant_realm", return_value="realm"), patch.object(r.admin_svc, "guarded_delete_realm_role", new_callable=AsyncMock, side_effect=RuntimeError("ignore")):
        await r.delete_tenant_role(req, RID, master_db=db, ctx=c)
    system_role = SimpleNamespace(name="hospital_admin", tenant_role_id=RID, tenant_id="t1", description=None, scope=None, created_by="actor", created_at=NOW, updated_at=NOW)
    db.query.return_value.filter.return_value.first.return_value = system_role
    with patch.object(r, "_get_tenant_realm", return_value="realm"):
        await r.delete_tenant_role(req, RID, master_db=db, ctx=c)


@pytest.mark.asyncio
async def test_router_tenant_role_update_duplicate_conflict():
    req = request(); c = ctx(); db = MagicMock()
    role = SimpleNamespace(name="old", tenant_role_id=RID, tenant_id="t1", description=None, scope=None, created_by="actor", created_at=NOW, updated_at=NOW)
    duplicate = SimpleNamespace(name="new")
    db.query.return_value.filter.return_value.first.side_effect = [role, duplicate]
    with pytest.raises(HTTPException) as exc:
        await r.update_tenant_role(req, RID, r.TenantRoleUpdate(name="new"), master_db=db, ctx=c)
    assert exc.value.status_code == 409
