"""Integration-style smoke tests for admin schemas and role helpers."""

from app.api.v1.admin.schemas import HospitalUserCreate, PermissionUpdate
from app.services.roles import DEFAULT_ROLE_MODULES


def test_hospital_user_create_schema_accepts_receptionist():
    body = HospitalUserCreate(
        username="recv1",
        password="SecurePass1!",
        email="recv1@example.com",
        full_name="Reception One",
        role="receptionist",
    )
    assert body.role == "receptionist"


def test_permission_update_schema():
    body = PermissionUpdate(modules=["reception"], actions=["read", "create"])
    assert "reception" in body.modules


def test_default_role_modules_seed_complete():
    assert "doctor" in DEFAULT_ROLE_MODULES
    assert "consultation" in DEFAULT_ROLE_MODULES["doctor"]
