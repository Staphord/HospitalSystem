from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services import admin as service


def _db(*, first=None, all_rows=None, count=0, scalar=0):
    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.first.return_value = first
    query.all.return_value = all_rows or []
    query.count.return_value = count
    query.scalar.return_value = scalar
    return db, query


def test_user_lookup_listing_and_last_admin_branches():
    user = SimpleNamespace(keycloak_sub="u1", deleted_at=None, role="doctor")
    db, query = _db(first=user, all_rows=[user], count=1)
    assert service.get_user(db, "tenant", "u1") is user
    assert service.list_users(db, "tenant", role="doctor", is_active=True, q="x", limit=300)[1] == 1
    with pytest.raises(HTTPException):
        service.get_user(_db(first=None)[0], "tenant", "missing")
    with pytest.raises(HTTPException):
        service.get_user(_db(first=SimpleNamespace(deleted_at=True))[0], "tenant", "deleted")

    admin = SimpleNamespace(role="hospital_admin", keycloak_sub="u1")
    with patch.object(service, "count_active_hospital_admins", return_value=1):
        service.ensure_not_last_admin(db, "tenant", admin, deactivating=True)
    service.ensure_not_last_admin(db, "tenant", SimpleNamespace(role="doctor"), deleting=True)
    service.ensure_not_last_admin(db, "tenant", admin)
    service.count_active_hospital_admins(db, "tenant", exclude_sub="u1")
    service.list_users(db, "tenant", include_deleted=True, department_id=uuid4(), q="search", sort="email")
    service.list_users(db, "tenant", include_deleted=True, sort="unknown")


def test_permission_seeding_and_upsert():
    db, query = _db(first=None)
    with patch.object(service.audit_service, "log_change"):
        row = service.upsert_permission(db, "doctor", ["consultation"], ["read"], "actor")
    assert row.role_name == "doctor"
    assert db.commit.called

    existing = SimpleNamespace(role_name="doctor", modules=[], actions=[])
    db, query = _db(first=existing)
    with patch.object(service.audit_service, "log_change"):
        result = service.upsert_permission(db, "doctor", ["ward"], ["update"], "actor")
    assert result.modules == ["ward"]
    assert result.actions == ["update"]


def test_profile_departments_and_basic_catalog_crud():
    tenant = SimpleNamespace(
        tenant_id="tenant", hospital_name="Old", timezone="UTC", currency="TZS",
        date_format=None, logo_url=None, primary_contact_name=None,
        primary_contact_email=None, primary_contact_phone=None, address=None,
        city=None, country=None,
    )
    db, _ = _db(first=tenant)
    tenant_db, _ = _db()
    with patch.object(service.audit_service, "log_change"):
        assert service.update_hospital_profile(db, tenant_db, "tenant", "actor", {"hospital_name": "New"}).hospital_name == "New"
    with pytest.raises(HTTPException):
        service.get_hospital_profile(_db(first=None)[0], "missing")

    db, query = _db(count=0)
    with patch.object(service.audit_service, "log_change"):
        rows = service.list_departments(db)
    assert rows == []
    assert db.add.call_count == len(service.DEFAULT_DEPARTMENTS)
    seeded, seeded_query = _db(count=1)
    assert service.seed_default_departments(seeded) is None

    department = SimpleNamespace(
        department_id=str(uuid4()), department_name="Old", department_type="old",
        head_user_sub=None, is_active=True,
    )
    db, _ = _db(first=department)
    with patch.object(service.audit_service, "log_change"):
        assert service.update_department(db, uuid4(), {"department_name": "New"}, "actor").department_name == "New"
        service.delete_department(db, uuid4(), "actor")

    with patch.object(service.audit_service, "log_change"):
        created = service.create_department(_db()[0], {"department_name": "ICU", "department_type": "icu"}, "actor")
    assert created.department_name == "ICU"


def test_fee_insurance_and_bed_crud_and_conflicts():
    with patch.object(service.audit_service, "log_change"):
        fee = service.create_fee(
            _db()[0],
            {"item_name": "Ward day", "item_code": "WARD_DAY", "item_type": "ward", "standard_price": "100", "effective_from": date.today()},
            "actor",
        )
    assert fee.standard_price == Decimal("100")
    with pytest.raises(HTTPException):
        service.create_fee(_db(first=object())[0], {"item_code": "WARD_DAY"}, "actor")

    fee_row = SimpleNamespace(fee_id=uuid4(), item_name="Old", standard_price=Decimal("1"), insurance_price=None, is_active=True)
    db, _ = _db(first=fee_row)
    with patch.object(service.audit_service, "log_change"):
        service.update_fee(db, fee_row.fee_id, {"standard_price": "2"}, "actor")
        service.delete_fee(db, fee_row.fee_id, "actor")
        service.update_fee(db, fee_row.fee_id, {"insurance_price": "3", "ignored": None}, "actor")
    fee_row.insurance_price = None
    with patch.object(service.audit_service, "log_change"):
        service.update_fee(db, fee_row.fee_id, {"insurance_price": "3", "item_name": "New", "unknown": "ignored"}, "actor")

    with patch.object(service.audit_service, "log_change"):
        provider = service.create_insurance_provider(_db()[0], {"name": "NHIF"}, "actor")
    assert provider.name == "NHIF"
    provider_row = SimpleNamespace(provider_id=uuid4(), name="NHIF", is_active=True)
    db, _ = _db(first=provider_row)
    with patch.object(service.audit_service, "log_change"):
        service.update_insurance_provider(db, provider_row.provider_id, {"is_active": False, "unknown": "ignored"}, "actor")
        service.delete_insurance_provider(db, provider_row.provider_id, "actor")
    with pytest.raises(HTTPException):
        service.create_insurance_provider(_db(first=object())[0], {"name": "NHIF"}, "actor")
    service.list_insurance_providers(_db(all_rows=[provider_row])[0])
    service.list_beds(_db(all_rows=[])[0])

    with patch.object(service.audit_service, "log_change"):
        bed = service.create_bed(_db()[0], {"ward_name": "Ward A", "bed_number": "A1"}, "actor")
    assert bed.bed_number == "A1"
    bed_row = SimpleNamespace(bed_id=uuid4(), ward_name="Ward A", bed_number="A1", is_available=True, is_active=True)
    db, _ = _db(first=bed_row)
    with patch.object(service.audit_service, "log_change"):
        service.update_bed(db, bed_row.bed_id, {"is_available": False, "unknown": "ignored"}, "actor")
        service.delete_bed(db, bed_row.bed_id, "actor")


@pytest.mark.asyncio
async def test_keycloak_role_guards_and_role_listing():
    with pytest.raises(HTTPException):
        await service.guarded_delete_realm_role("realm", "doctor")
    with pytest.raises(HTTPException):
        await service.guarded_update_realm_role("realm", "doctor", "new")
    with patch.object(service, "delete_realm_role", new_callable=AsyncMock) as deleted:
        await service.guarded_delete_realm_role("realm", "custom")
        deleted.assert_awaited_once()
    with patch.object(service, "update_realm_role", new_callable=AsyncMock) as updated:
        await service.guarded_update_realm_role("realm", "custom", "new")
        updated.assert_awaited_once()
    master, query = _db(all_rows=[SimpleNamespace(name="tenant_role")])
    master.execute.return_value.fetchall.return_value = [("global_role",)]
    with patch.object(service, "get_realm_roles", new_callable=AsyncMock, return_value=[{"name": "custom"}, {"name": "default-roles-x"}]):
        roles = await service.list_assignable_roles(master, "tenant", "realm")
    assert "custom" in roles and "super_admin" not in roles


@pytest.mark.asyncio
async def test_create_user_success_duplicate_and_keycloak_errors():
    user = SimpleNamespace(
        keycloak_sub="kc-1", username="doctor", email="d@example.com", role="doctor",
        is_active=True, deleted_at=None, full_name="Doctor", department_id=None,
        phone=None, force_password_change=True,
    )
    db, query = _db(first=None)
    with patch.object(service, "ensure_roles", new_callable=AsyncMock), \
         patch.object(service, "create_keycloak_user", new_callable=AsyncMock, return_value="kc-1"), \
         patch.object(service, "set_user_password", new_callable=AsyncMock), \
         patch.object(service, "set_user_attribute", new_callable=AsyncMock), \
         patch.object(service, "create_local_user", return_value=user), \
         patch.object(service.audit_service, "log_change"):
        result = await service.create_user(
            db, MagicMock(), tenant_id="tenant", realm="realm", username="doctor",
            password="SecurePass1!", email="d@example.com", full_name="Doctor",
            role="doctor", actor_sub="actor",
        )
    assert result is user

    with patch.object(service, "ensure_roles", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as duplicate:
            await service.create_user(
                _db(first=object())[0], MagicMock(), tenant_id="tenant", realm="realm",
                username="doctor", password="SecurePass1!", email="d@example.com",
                full_name="Doctor", role="doctor", actor_sub="actor",
            )
    assert duplicate.value.status_code == 409

    with patch.object(service, "ensure_roles", new_callable=AsyncMock), \
         patch.object(service, "create_keycloak_user", new_callable=AsyncMock, side_effect=RuntimeError("500")):
        with pytest.raises(HTTPException) as failed:
            await service.create_user(
                _db()[0], MagicMock(), tenant_id="tenant", realm="realm", username="doctor",
                password="SecurePass1!", email="d@example.com", full_name="Doctor",
                role="doctor", actor_sub="actor",
            )
    assert failed.value.status_code == 502


@pytest.mark.asyncio
async def test_update_and_delete_user_success_and_self_delete_guard():
    user = SimpleNamespace(
        keycloak_sub="u1", username="doctor", email="d@example.com", role="doctor",
        is_active=True, deleted_at=None, full_name="Doctor", force_password_change=False,
        department_id=None, phone=None, mfa_enabled=False,
    )
    db, _ = _db(first=user)
    with patch.object(service, "update_keycloak_user", new_callable=AsyncMock), \
         patch.object(service, "set_user_password", new_callable=AsyncMock), \
         patch.object(service, "ensure_roles", new_callable=AsyncMock), \
         patch.object(service, "replace_user_roles", new_callable=AsyncMock), \
         patch.object(service, "update_local_user", return_value=user), \
         patch.object(service.audit_service, "log_change"):
        result = await service.update_user(
            db, tenant_id="tenant", realm="realm", sub="u1", actor_sub="actor",
            username="new-doctor", password="SecurePass1!", role="nurse",
            is_active=False, force_password_change=True, reason="role change",
        )
    assert result is user

    with patch.object(service, "delete_keycloak_user", new_callable=AsyncMock, return_value=True), \
         patch.object(service, "delete_local_user"), \
         patch.object(service.audit_service, "log_change"):
        await service.delete_user(db, tenant_id="tenant", realm="realm", sub="u1", actor_sub="actor", hard=True)

    with pytest.raises(HTTPException) as own:
        await service.delete_user(db, tenant_id="tenant", realm="realm", sub="actor", actor_sub="actor")
    assert own.value.status_code == 400
