import pytest
from unittest.mock import AsyncMock
from starlette.requests import Request

from app.api.v1.users import router as users
from app.api.v1.users.schemas import PasswordChange, UserUpdate
from app.core.tenant_auth import TenantContext
from app.models.admin import SuperAdmin
from app.models.user import User


def ctx(**kwargs):
    values = dict(tenant_id=None, user_sub="sub", preferred_username="user", email="u@example.com", roles=["doctor"], is_super_admin=False, scope="full", raw_token={})
    values.update(kwargs)
    return TenantContext(**values)


def request(ip="127.0.0.1"):
    return Request({"type": "http", "headers": [], "method": "GET", "path": "/", "client": (ip, 1)})


def test_derive_primary_role_priorities():
    assert users._derive_primary_role(ctx(is_super_admin=True)) == "super_admin"
    assert users._derive_primary_role(ctx(roles=["hospital_user", "doctor"])) == "doctor"
    assert users._derive_primary_role(ctx(roles=["custom"])) == "custom"
    assert users._derive_primary_role(ctx(roles=[])) == "hospital_user"


@pytest.mark.asyncio
async def test_me_superadmin_and_tenant(monkeypatch, db_session):
    admin = SuperAdmin(username="admin", email="a@example.com", password_hash="x", full_name="Admin", mfa_secret="secret")
    db_session.add(admin)
    db_session.commit()
    result = await users.me(request(), ctx=ctx(user_sub=str(admin.super_admin_id), preferred_username="admin", email="a@example.com", roles=["super_admin"], is_super_admin=True), db=db_session)
    assert result["is_super_admin"] is True

    user = User(keycloak_sub="sub", username="db-user", full_name="Database User", email="u@example.com", hospital_id="tenant")
    db_session.add(user)
    db_session.commit()
    tenant_session = db_session
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: tenant_session)
    result = await users.me(request(), ctx=ctx(tenant_id="tenant"), db=db_session)
    assert result["username"] == "db-user"


@pytest.mark.asyncio
async def test_me_impersonation(monkeypatch, db_session):
    result = await users.me(request("127.0.0.79"), ctx=ctx(tenant_id="tenant", raw_token={"impersonator": True}), db=db_session)
    assert result["scope"] == "full"


@pytest.mark.asyncio
async def test_update_and_change_password(monkeypatch, db_session):
    monkeypatch.setattr(users, "update_keycloak_user", AsyncMock())
    body = UserUpdate(username="new", email="new@example.com", full_name="New User")
    result = await users.update_me(request("127.0.0.77"), body, ctx=ctx(), db=db_session)
    assert result["detail"] == "Profile updated successfully"

    monkeypatch.setattr(users.auth_service, "login", AsyncMock(return_value={}))
    monkeypatch.setattr(users, "set_user_password", AsyncMock())
    result = await users.change_password(request("127.0.0.78"), PasswordChange(current_password="old", new_password="NewPassword1!"), ctx=ctx(), db=db_session)
    assert result["detail"] == "Password changed successfully"


@pytest.mark.asyncio
async def test_users_router_superadmin_and_tenant_update_paths(monkeypatch, db_session):
    admin = SuperAdmin(username="profile-admin", email="profile@example.com", password_hash="x", full_name="Profile Admin", mfa_secret="secret")
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    monkeypatch.setattr(users, "update_keycloak_user", AsyncMock())
    body = UserUpdate(username="profile-new", email="new-profile@example.com", full_name="New Profile")
    result = await users.update_me(request("127.0.0.80"), body, ctx=ctx(user_sub=admin.super_admin_id, is_super_admin=True), db=db_session)
    assert result["detail"] == "Profile updated successfully"

    tenant_user = User(keycloak_sub="tenant-profile", username="old", email="old@example.com", hospital_id="tenant")
    db_session.add(tenant_user); db_session.commit()
    monkeypatch.setattr("app.services.provision.get_tenant_db_session", lambda _: db_session)
    monkeypatch.setattr(users, "update_local_user", lambda **kwargs: tenant_user)
    result = await users.update_me(request("127.0.0.81"), body, ctx=ctx(user_sub="tenant-profile", tenant_id="tenant"), db=db_session)
    assert result["detail"] == "Profile updated successfully"

    monkeypatch.setattr(users, "update_keycloak_user", AsyncMock(side_effect=RuntimeError("identity down")))
    with pytest.raises(Exception) as exc:
        await users.update_me(request("127.0.0.82"), body, ctx=ctx(), db=db_session)
    assert getattr(exc.value, "status_code", None) == 400


@pytest.mark.asyncio
async def test_users_router_password_failure_paths(monkeypatch, db_session):
    monkeypatch.setattr(users.auth_service, "login", AsyncMock(side_effect=RuntimeError("bad password")))
    with pytest.raises(Exception) as exc:
        await users.change_password(request("127.0.0.83"), PasswordChange(current_password="bad", new_password="N3w!CedarRiver"), ctx=ctx(), db=db_session)
    assert getattr(exc.value, "status_code", None) == 400
    monkeypatch.setattr(users.auth_service, "login", AsyncMock(return_value={}))
    monkeypatch.setattr(users, "set_user_password", AsyncMock(side_effect=RuntimeError("keycloak down")))
    with pytest.raises(Exception) as exc:
        await users.change_password(request("127.0.0.84"), PasswordChange(current_password="old", new_password="N3w!CedarRiver"), ctx=ctx(), db=db_session)
    assert getattr(exc.value, "status_code", None) == 500


@pytest.mark.asyncio
async def test_users_router_profile_update_partial_fields(monkeypatch, db_session):
    admin = SuperAdmin(username="partial-admin", email="partial@example.com", password_hash="x", full_name="Partial Admin", mfa_secret="secret")
    db_session.add(admin); db_session.commit(); db_session.refresh(admin)
    monkeypatch.setattr(users, "update_keycloak_user", AsyncMock())
    body = UserUpdate(username=None, email=None, full_name=None)
    result = await users.update_me(request("127.0.0.95"), body, ctx=ctx(user_sub=admin.super_admin_id, is_super_admin=True), db=db_session)
    assert result["detail"] == "Profile updated successfully"

